"""Bronze -> Silver -> Gold pipeline over eligibility transactions.

Bronze: raw 270/271 X12 payloads, append-only, incremental by transaction_id.
Silver: one parsed, typed row per 271 response.
Gold:   business aggregates rebuilt on every run (payer performance, denial
        categories, latest status per patient).

Run with: python -m src.pipeline.medallion [--backend sqlite|databricks]
"""

import argparse
import json
from datetime import datetime, timezone

from src import db
from src.config import get_settings
from src.pipeline.sinks import Sink, get_sink
from src.rules import summarize_benefits
from src.x12.x271 import parse_271

BRONZE = "bronze_x12_raw"
SILVER = "silver_eligibility"
GOLD_TABLES = ("gold_payer_performance", "gold_denial_categories", "gold_patient_latest_status")

BRONZE_COLUMNS = (
    "transaction_id", "transaction_type", "trace_number", "patient_id", "payer_id", "date_of_service",
    "status", "denial_category", "payload", "source_created_at", "ingested_at",
)

BRONZE_DDL = """
CREATE TABLE IF NOT EXISTS {table} (
    transaction_id    {{INT}},
    transaction_type  {{STR}},
    trace_number      {{STR}},
    patient_id        {{STR}},
    payer_id          {{STR}},
    date_of_service   {{STR}},
    status            {{STR}},
    denial_category   {{STR}},
    payload           {{STR}},
    source_created_at {{STR}},
    ingested_at       {{STR}}
)
"""

SILVER_DDL = """
CREATE TABLE IF NOT EXISTS {table} (
    transaction_id        {{INT}},
    trace_number          {{STR}},
    patient_id            {{STR}},
    payer_id              {{STR}},
    date_of_service       {{STR}},
    status                {{STR}},
    denial_category       {{STR}},
    member_id             {{STR}},
    coverage_active       {{BOOL}},
    plan_name             {{STR}},
    plan_begin            {{STR}},
    plan_end              {{STR}},
    reject_code           {{STR}},
    reject_description    {{STR}},
    office_visit_copay    {{DBL}},
    deductible            {{DBL}},
    deductible_remaining  {{DBL}},
    coinsurance_pct       {{DBL}},
    oop_max               {{DBL}},
    oop_remaining         {{DBL}},
    has_other_payer       {{BOOL}},
    pcp_npi               {{STR}},
    referral_required     {{BOOL}},
    payer_messages        {{STR}},
    processed_at          {{STR}}
)
"""


def _gold_sql(sink: Sink) -> dict[str, str]:
    silver = sink.table(SILVER)
    return {
        "gold_payer_performance": f"""
            SELECT payer_id,
                   COUNT(*) AS total_checks,
                   SUM(CASE WHEN status = 'ELIGIBLE' THEN 1 ELSE 0 END) AS eligible,
                   SUM(CASE WHEN status = 'ELIGIBLE_WITH_ISSUES' THEN 1 ELSE 0 END) AS eligible_with_issues,
                   SUM(CASE WHEN status = 'INELIGIBLE' THEN 1 ELSE 0 END) AS ineligible,
                   SUM(CASE WHEN status = 'REJECTED' THEN 1 ELSE 0 END) AS rejected,
                   SUM(CASE WHEN status = 'PAYER_UNAVAILABLE' THEN 1 ELSE 0 END) AS payer_unavailable,
                   ROUND(100.0 * SUM(CASE WHEN status IN ('REJECTED', 'INELIGIBLE') THEN 1 ELSE 0 END)
                         / COUNT(*), 1) AS failure_rate_pct
            FROM {silver}
            GROUP BY payer_id
        """,
        "gold_denial_categories": f"""
            SELECT COALESCE(denial_category, 'NONE') AS denial_category,
                   COUNT(*) AS occurrences,
                   COUNT(DISTINCT patient_id) AS patients
            FROM {silver}
            GROUP BY COALESCE(denial_category, 'NONE')
        """,
        "gold_patient_latest_status": f"""
            SELECT patient_id, payer_id, date_of_service, status, denial_category,
                   coverage_active, deductible_remaining, processed_at
            FROM (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY patient_id ORDER BY transaction_id DESC) AS rn
                FROM {silver}
            ) latest
            WHERE rn = 1
        """,
    }


def _max_id(sink: Sink, table: str, where: str = "") -> int:
    rows = sink.query(f"SELECT MAX(transaction_id) AS max_id FROM {table} {where}")
    return int(rows[0]["max_id"] or 0) if rows else 0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_bronze(sink: Sink) -> int:
    """Append new 270/271 payloads from the operational store."""
    table = sink.table(BRONZE)
    sink.execute(BRONZE_DDL.format(table=table))
    last = _max_id(sink, table)
    new = [t for t in db.list_transactions(limit=1_000_000) if t["transaction_id"] > last]
    ingested_at = _now()
    rows = []
    for t in sorted(new, key=lambda t: t["transaction_id"]):
        for kind, payload in (("270", t["request_270"]), ("271", t["response_271"])):
            rows.append({
                "transaction_id": t["transaction_id"], "transaction_type": kind,
                "trace_number": t["trace_number"], "patient_id": t["patient_id"],
                "payer_id": t["payer_id"], "date_of_service": t["date_of_service"],
                "status": t["status"], "denial_category": t["denial_category"], "payload": payload,
                "source_created_at": t["created_at"], "ingested_at": ingested_at,
            })
    columns = list(BRONZE_COLUMNS)
    sink.executemany(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(':' + c for c in columns)})",
        rows,
    )
    return len(rows)


SILVER_COLUMNS = (
    "transaction_id", "trace_number", "patient_id", "payer_id", "date_of_service", "status",
    "denial_category", "member_id", "coverage_active", "plan_name", "plan_begin", "plan_end",
    "reject_code", "reject_description", "office_visit_copay", "deductible", "deductible_remaining",
    "coinsurance_pct", "oop_max", "oop_remaining", "has_other_payer", "pcp_npi", "referral_required",
    "payer_messages", "processed_at",
)


def bronze_to_silver(sink: Sink) -> int:
    """Parse new bronze 271 payloads into typed silver rows."""
    bronze, silver = sink.table(BRONZE), sink.table(SILVER)
    sink.execute(SILVER_DDL.format(table=silver))
    last = _max_id(sink, silver)
    raw = sink.query(
        f"SELECT * FROM {bronze} WHERE transaction_type = '271' AND transaction_id > :last "
        "ORDER BY transaction_id",
        {"last": last},
    )
    processed_at = _now()
    rows = []
    for r in raw:
        resp = parse_271(r["payload"])
        benefits = summarize_benefits(resp)
        reject = resp.rejections[0] if resp.rejections else None
        pcp = next((b.related_entity for b in resp.benefits if b.code == "L" and b.related_entity), None)
        rows.append({
            "transaction_id": r["transaction_id"], "trace_number": r["trace_number"],
            "patient_id": r["patient_id"], "payer_id": r["payer_id"],
            "date_of_service": r["date_of_service"], "status": r["status"],
            "denial_category": r["denial_category"], "member_id": resp.member_id,
            "coverage_active": resp.coverage_active, "plan_name": benefits["plan"],
            "plan_begin": resp.plan_begin.isoformat() if resp.plan_begin else None,
            "plan_end": resp.plan_end.isoformat() if resp.plan_end else None,
            "reject_code": reject.code if reject else None,
            "reject_description": reject.description if reject else None,
            "office_visit_copay": benefits["office_visit_copay"], "deductible": benefits["deductible"],
            "deductible_remaining": benefits["deductible_remaining"],
            "coinsurance_pct": benefits["coinsurance_pct"], "oop_max": benefits["out_of_pocket_max"],
            "oop_remaining": benefits["out_of_pocket_remaining"],
            "has_other_payer": any(b.code == "R" for b in resp.benefits),
            "pcp_npi": pcp["npi"] if pcp else None,
            "referral_required": any(b.authorization_required for b in resp.benefits),
            "payer_messages": json.dumps(benefits["messages"]), "processed_at": processed_at,
        })
    columns = list(SILVER_COLUMNS)
    sink.executemany(
        f"INSERT INTO {silver} ({', '.join(columns)}) VALUES ({', '.join(':' + c for c in columns)})",
        rows,
    )
    return len(rows)


def silver_to_gold(sink: Sink) -> list[str]:
    """Rebuild every gold table from silver."""
    for name, select in _gold_sql(sink).items():
        table = sink.table(name)
        sink.execute(f"DROP TABLE IF EXISTS {table}")
        sink.execute(f"CREATE TABLE {table} AS {select}")
    return list(GOLD_TABLES)


def run(backend: str | None = None) -> dict:
    """Run bronze -> silver -> gold and return a summary."""
    sink = get_sink(get_settings(), backend)
    try:
        bronze_rows = load_bronze(sink)
        silver_rows = bronze_to_silver(sink)
        gold = silver_to_gold(sink)
        return {
            "backend": sink.name,
            "bronze_rows_added": bronze_rows,
            "silver_rows_added": silver_rows,
            "gold_tables": [sink.table(t) for t in gold],
        }
    finally:
        sink.close()


def read_gold(backend: str | None = None) -> dict[str, list[dict]]:
    """Return the contents of every gold table (empty lists if the pipeline has not run)."""
    sink = get_sink(get_settings(), backend)
    try:
        result = {}
        for name in GOLD_TABLES:
            try:
                result[name] = sink.query(f"SELECT * FROM {sink.table(name)}")
            except Exception:
                result[name] = []
        return result
    finally:
        sink.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--backend", choices=["sqlite", "databricks"], default=None,
                        help="Default: Databricks if configured in .env, else SQLite.")
    args = parser.parse_args()
    print(json.dumps(run(args.backend), indent=2))


if __name__ == "__main__":
    main()
