"""Synthetic patients, payers, coverage and payer enrollment.

Each patient is built to exercise one real-world eligibility scenario.
All data is fictional. Run with: python -m src.seed
"""

from pathlib import Path

from src.db import connect, init_db

PAYERS = [
    # payer_id, name, edi_payer_id, online
    ("AETNA", "AETNA", "60054", 1),
    ("BCBS", "BLUE CROSS BLUE SHIELD", "00590", 1),
    ("UHC", "UNITEDHEALTHCARE", "87726", 1),
    ("CIGNA", "CIGNA", "62308", 1),
    ("MEDICARE", "MEDICARE PART B", "MCARE", 1),
    ("HUMANA", "HUMANA", "61101", 0),  # simulates a payer outage
]

PATIENTS = [
    # patient_id, first, last, dob, gender, address, city, state, zip
    ("P001", "John", "Smith", "1980-04-12", "M", "12 Oak St", "Dallas", "TX", "75201"),
    ("P002", "Maria", "Garcia", "1975-09-30", "F", "48 Elm Ave", "Austin", "TX", "73301"),
    ("P003", "Robert", "Johnson", "1962-01-22", "M", "7 Pine Rd", "Houston", "TX", "77002"),
    ("P004", "Linda", "Williams", "1955-06-15", "F", "301 Cedar Ln", "Plano", "TX", "75023"),
    ("P005", "David", "Brown", "1990-11-03", "M", "19 Birch Ct", "Irving", "TX", "75038"),
    ("P006", "Susan", "Miller", "1988-02-28", "F", "88 Maple Dr", "Frisco", "TX", "75034"),
    ("P007", "James", "Davis", "1971-07-19", "M", "5 Walnut Way", "Garland", "TX", "75040"),
    ("P008", "Patricia", "Wilson", "1983-12-08", "F", "61 Spruce St", "Arlington", "TX", "76010"),
    ("P009", "Michael", "Moore", "1969-03-25", "M", "230 Ash Blvd", "Denton", "TX", "76201"),
]

# What the provider captured at registration.
COVERAGES = [
    # coverage_id, patient_id, payer_id, member_id, group, plan_name, plan_type, priority,
    # pcp_npi, pcp_name, effective, termination
    # P001: member ID typo at registration (payer has W123456780) -> payer rejects (AAA 72)
    ("C001", "P001", "AETNA", "W123456789", "GRP1001", "AETNA CHOICE POS II", "POS", "primary",
     None, None, "2025-01-01", None),
    # P002: clean, active PPO
    ("C002", "P002", "BCBS", "XYZ987654321", "GRP2002", "BLUE CHOICE PPO", "PPO", "primary",
     None, None, "2024-01-01", None),
    # P003: plan terminated at the payer; provider record still shows it open
    ("C003", "P003", "UHC", "U55512345", "GRP3003", "UHC CHOICE PLUS", "PPO", "primary",
     None, None, "2023-01-01", None),
    # P004: registered as primary, but payer says Medicare is primary (COB)
    ("C004", "P004", "CIGNA", "C77788899", "GRP4004", "CIGNA OPEN ACCESS PLUS", "EPO", "primary",
     None, None, "2024-07-01", None),
    # P005: HMO with PCP on file that differs from payer's assigned PCP; referral required
    ("C005", "P005", "UHC", "U66677788", "GRP5005", "UHC NAVIGATE HMO", "HMO", "primary",
     "1902345678", "DR. ALAN REED", "2025-03-01", None),
    # P006: billed to the wrong payer; BCBS has no record of her
    ("C006", "P006", "BCBS", "XYZ111222333", "GRP6006", "BLUE CHOICE PPO", "PPO", "primary",
     None, None, "2025-01-01", None),
    # P007: DOB keyed wrong at registration (payer has 1971-07-09)
    ("C007", "P007", "AETNA", "W987654321", "GRP7007", "AETNA OPEN ACCESS", "PPO", "primary",
     None, None, "2024-01-01", None),
    # P008: active high-deductible plan, deductible not met
    ("C008", "P008", "CIGNA", "C12312312", "GRP8008", "CIGNA HDHP", "HDHP", "primary",
     None, None, "2025-01-01", None),
    # P009: payer system is down
    ("C009", "P009", "HUMANA", "H44455566", "GRP9009", "HUMANA GOLD PLUS", "HMO", "primary",
     None, None, "2024-01-01", None),
]

# The payer's system of record (only the mock payer reads this).
_ENROLLMENT_COLUMNS = (
    "payer_id", "member_id", "first_name", "last_name", "dob", "gender", "group_number",
    "plan_name", "plan_type", "effective_date", "termination_date", "copay", "specialist_copay",
    "deductible", "deductible_remaining", "coinsurance_pct", "oop_max", "oop_remaining",
    "pcp_required", "pcp_npi", "pcp_name", "referral_required", "other_payer_name",
    "other_payer_member_id", "other_payer_primary",
)

ENROLLMENTS = [
    ("AETNA", "W123456780", "JOHN", "SMITH", "1980-04-12", "M", "GRP1001", "AETNA CHOICE POS II",
     "POS", "2025-01-01", None, 25, 50, 1500, 600, 20, 6000, 4100, 0, None, None, 0, None, None, 0),
    ("BCBS", "XYZ987654321", "MARIA", "GARCIA", "1975-09-30", "F", "GRP2002", "BLUE CHOICE PPO",
     "PPO", "2024-01-01", None, 30, 60, 1000, 0, 20, 5000, 2200, 0, None, None, 0, None, None, 0),
    ("UHC", "U55512345", "ROBERT", "JOHNSON", "1962-01-22", "M", "GRP3003", "UHC CHOICE PLUS",
     "PPO", "2023-01-01", "2026-06-30", 20, 40, 2000, 2000, 20, 7000, 7000, 0, None, None, 0,
     None, None, 0),
    ("CIGNA", "C77788899", "LINDA", "WILLIAMS", "1955-06-15", "F", "GRP4004",
     "CIGNA OPEN ACCESS PLUS", "EPO", "2024-07-01", None, 35, 70, 1500, 250, 20, 6500, 3000, 0,
     None, None, 0, "MEDICARE PART B", "1EG4TE5MK72", 1),
    ("UHC", "U66677788", "DAVID", "BROWN", "1990-11-03", "M", "GRP5005", "UHC NAVIGATE HMO", "HMO",
     "2025-03-01", None, 15, 40, 500, 500, 0, 4000, 4000, 1, "1801234567", "DR. KAREN PATEL", 1,
     None, None, 0),
    ("AETNA", "W987654321", "JAMES", "DAVIS", "1971-07-09", "M", "GRP7007", "AETNA OPEN ACCESS",
     "PPO", "2024-01-01", None, 30, 55, 1000, 300, 20, 5500, 2600, 0, None, None, 0, None, None, 0),
    ("CIGNA", "C12312312", "PATRICIA", "WILSON", "1983-12-08", "F", "GRP8008", "CIGNA HDHP", "HDHP",
     "2025-01-01", None, 0, 0, 3500, 3100, 30, 7500, 7100, 0, None, None, 0, None, None, 0),
    ("HUMANA", "H44455566", "MICHAEL", "MOORE", "1969-03-25", "M", "GRP9009", "HUMANA GOLD PLUS",
     "HMO", "2024-01-01", None, 10, 35, 0, 0, 0, 3900, 3900, 1, "1700000001", "DR. SARA LIN", 0,
     None, None, 0),
]


def seed(db_path: Path | None = None) -> None:
    """Create the schema and (re)load all synthetic data. Safe to run repeatedly."""
    init_db(db_path)
    with connect(db_path) as conn:
        for table in ("eligibility_transactions", "payer_enrollments", "coverages", "patients", "payers"):
            conn.execute(f"DELETE FROM {table}")
        conn.executemany("INSERT INTO payers VALUES (?, ?, ?, ?)", PAYERS)
        conn.executemany("INSERT INTO patients VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", PATIENTS)
        conn.executemany(
            "INSERT INTO coverages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", COVERAGES
        )
        placeholders = ", ".join("?" for _ in _ENROLLMENT_COLUMNS)
        conn.executemany(
            f"INSERT INTO payer_enrollments ({', '.join(_ENROLLMENT_COLUMNS)}) VALUES ({placeholders})",
            ENROLLMENTS,
        )


def ensure_seeded() -> None:
    """Seed the database on first use."""
    init_db()
    with connect() as conn:
        empty = conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0] == 0
    if empty:
        seed()


if __name__ == "__main__":
    seed()
    print(f"Seeded {len(PATIENTS)} patients, {len(PAYERS)} payers, {len(ENROLLMENTS)} enrollments.")
