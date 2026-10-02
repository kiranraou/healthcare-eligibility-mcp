"""SQLite storage for patients, payers, coverage, payer enrollment and transactions.

Two views of insurance are stored on purpose:
- `coverages` is what the provider captured at registration (may be wrong).
- `payer_enrollments` is the payer's system of record, read only by the mock payer.
Eligibility problems come from the gap between the two.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from src.config import get_settings
from src.models import Coverage, Patient, Payer

SCHEMA = """
CREATE TABLE IF NOT EXISTS payers (
    payer_id      TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    edi_payer_id  TEXT NOT NULL,
    online        INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS patients (
    patient_id  TEXT PRIMARY KEY,
    first_name  TEXT NOT NULL,
    last_name   TEXT NOT NULL,
    dob         TEXT NOT NULL,
    gender      TEXT NOT NULL,
    address     TEXT NOT NULL,
    city        TEXT NOT NULL,
    state       TEXT NOT NULL,
    zip         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS coverages (
    coverage_id       TEXT PRIMARY KEY,
    patient_id        TEXT NOT NULL REFERENCES patients(patient_id),
    payer_id          TEXT NOT NULL REFERENCES payers(payer_id),
    member_id         TEXT NOT NULL,
    group_number      TEXT,
    plan_name         TEXT NOT NULL,
    plan_type         TEXT NOT NULL,
    priority          TEXT NOT NULL DEFAULT 'primary',
    pcp_npi           TEXT,
    pcp_name          TEXT,
    effective_date    TEXT NOT NULL,
    termination_date  TEXT
);

CREATE TABLE IF NOT EXISTS payer_enrollments (
    payer_id               TEXT NOT NULL REFERENCES payers(payer_id),
    member_id              TEXT NOT NULL,
    first_name             TEXT NOT NULL,
    last_name              TEXT NOT NULL,
    dob                    TEXT NOT NULL,
    gender                 TEXT NOT NULL,
    group_number           TEXT,
    plan_name              TEXT NOT NULL,
    plan_type              TEXT NOT NULL,
    effective_date         TEXT NOT NULL,
    termination_date       TEXT,
    copay                  REAL,
    specialist_copay       REAL,
    deductible             REAL,
    deductible_remaining   REAL,
    coinsurance_pct        REAL,
    oop_max                REAL,
    oop_remaining          REAL,
    pcp_required           INTEGER NOT NULL DEFAULT 0,
    pcp_npi                TEXT,
    pcp_name               TEXT,
    referral_required      INTEGER NOT NULL DEFAULT 0,
    other_payer_name       TEXT,
    other_payer_member_id  TEXT,
    other_payer_primary    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (payer_id, member_id)
);

CREATE TABLE IF NOT EXISTS eligibility_transactions (
    transaction_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_number     TEXT NOT NULL,
    patient_id       TEXT NOT NULL,
    payer_id         TEXT NOT NULL,
    date_of_service  TEXT NOT NULL,
    request_270      TEXT NOT NULL,
    response_271     TEXT NOT NULL,
    status           TEXT NOT NULL,
    denial_category  TEXT,
    created_at       TEXT NOT NULL
);
"""


def _db_path() -> Path:
    return get_settings().db_path


@contextmanager
def connect(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    path = db_path or _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path | None = None) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)


def list_patients() -> list[Patient]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM patients ORDER BY patient_id").fetchall()
    return [Patient(**dict(r)) for r in rows]


def get_patient(patient_id: str) -> Patient | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM patients WHERE patient_id = ?", (patient_id,)).fetchone()
    return Patient(**dict(row)) if row else None


def find_patients(name: str) -> list[Patient]:
    """Case-insensitive match on first, last or 'first last' name."""
    pattern = f"%{name.strip().lower()}%"
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM patients
            WHERE lower(first_name || ' ' || last_name) LIKE ?
               OR lower(last_name || ', ' || first_name) LIKE ?
            ORDER BY patient_id
            """,
            (pattern, pattern),
        ).fetchall()
    return [Patient(**dict(r)) for r in rows]


def list_payers() -> list[Payer]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM payers ORDER BY payer_id").fetchall()
    return [Payer(**dict(r)) for r in rows]


def get_payer(payer_id: str) -> Payer | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM payers WHERE payer_id = ?", (payer_id,)).fetchone()
    return Payer(**dict(row)) if row else None


def get_coverages(patient_id: str) -> list[Coverage]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT c.*, p.name AS payer_name
            FROM coverages c JOIN payers p ON p.payer_id = c.payer_id
            WHERE c.patient_id = ?
            ORDER BY CASE c.priority WHEN 'primary' THEN 0 WHEN 'secondary' THEN 1 ELSE 2 END
            """,
            (patient_id,),
        ).fetchall()
    return [Coverage(**dict(r)) for r in rows]


def get_primary_coverage(patient_id: str) -> Coverage | None:
    coverages = get_coverages(patient_id)
    return coverages[0] if coverages else None


def find_enrollment(payer_id: str, member_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM payer_enrollments WHERE payer_id = ? AND member_id = ?",
            (payer_id, member_id),
        ).fetchone()
    return dict(row) if row else None


def find_enrollment_by_demographics(payer_id: str, last_name: str, dob: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM payer_enrollments
            WHERE payer_id = ? AND upper(last_name) = upper(?) AND dob = ?
            """,
            (payer_id, last_name, dob),
        ).fetchone()
    return dict(row) if row else None


def save_transaction(
    *,
    trace_number: str,
    patient_id: str,
    payer_id: str,
    date_of_service: date,
    request_270: str,
    response_271: str,
    status: str,
    denial_category: str | None,
) -> int:
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO eligibility_transactions
                (trace_number, patient_id, payer_id, date_of_service, request_270,
                 response_271, status, denial_category, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trace_number,
                patient_id,
                payer_id,
                date_of_service.isoformat(),
                request_270,
                response_271,
                status,
                denial_category,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
        return cur.lastrowid


def list_transactions(limit: int = 100) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM eligibility_transactions ORDER BY transaction_id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]
