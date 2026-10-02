from datetime import date

import pytest

from src import service
from src.config import get_settings
from src.pipeline import medallion
from src.pipeline.sinks import DatabricksSink, get_sink


def _check_all():
    for i in range(1, 10):
        service.check_eligibility(f"P00{i}", date(2026, 10, 2))


def test_pipeline_is_incremental():
    _check_all()
    first = medallion.run("sqlite")
    assert first["backend"] == "sqlite"
    assert (first["bronze_rows_added"], first["silver_rows_added"]) == (18, 9)

    assert medallion.run("sqlite")["bronze_rows_added"] == 0

    service.check_eligibility("P001", date(2026, 10, 2))
    third = medallion.run("sqlite")
    assert (third["bronze_rows_added"], third["silver_rows_added"]) == (2, 1)


def test_gold_tables():
    _check_all()
    service.check_eligibility("P001", date(2026, 10, 2))
    medallion.run("sqlite")
    gold = medallion.read_gold("sqlite")

    aetna = next(r for r in gold["gold_payer_performance"] if r["payer_id"] == "AETNA")
    assert aetna["total_checks"] == 3 and aetna["rejected"] == 3 and aetna["failure_rate_pct"] == 100.0

    categories = {r["denial_category"]: r for r in gold["gold_denial_categories"]}
    assert categories["INVALID_MEMBER_ID"]["occurrences"] == 2
    assert categories["INVALID_MEMBER_ID"]["patients"] == 1

    latest = gold["gold_patient_latest_status"]
    assert len(latest) == 9
    assert next(r for r in latest if r["patient_id"] == "P002")["status"] == "ELIGIBLE"


def test_read_gold_before_first_run():
    assert medallion.read_gold("sqlite") == {name: [] for name in medallion.GOLD_TABLES}


def test_databricks_requires_configuration():
    with pytest.raises(RuntimeError, match="Databricks is not configured"):
        get_sink(get_settings(), "databricks")


class FakeCursor:
    def __init__(self, log):
        self.log = log
        self.description = [("max_id",)]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, parameters=None):
        self.log.append(("execute", " ".join(sql.split()), parameters))

    def executemany(self, sql, rows):
        self.log.append(("executemany", sql, len(rows)))

    def fetchall(self):
        return [(None,)]


class FakeConnection:
    def __init__(self):
        self.log = []

    def cursor(self):
        return FakeCursor(self.log)

    def close(self):
        pass


def test_databricks_sink_writes_delta_tables(monkeypatch):
    monkeypatch.setenv("DATABRICKS_CATALOG", "workspace")
    monkeypatch.setenv("DATABRICKS_SCHEMA", "eligibility")
    _check_all()
    conn = FakeConnection()
    sink = DatabricksSink(get_settings(), connection=conn)

    assert medallion.load_bronze(sink) == 18
    medallion.silver_to_gold(sink)

    statements = [entry[1] for entry in conn.log]
    assert statements[0] == "CREATE SCHEMA IF NOT EXISTS workspace.eligibility"
    ddl = next(s for s in statements if s.startswith("CREATE TABLE IF NOT EXISTS workspace.eligibility.bronze_x12_raw"))
    assert "payload STRING" in ddl and "transaction_id BIGINT" in ddl
    assert ("executemany", 18) == (conn.log[3][0], conn.log[3][2])
    assert "CREATE TABLE workspace.eligibility.gold_payer_performance AS" in " ".join(statements)
