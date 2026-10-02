"""Application settings, loaded from environment variables and `.env`."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    db_path: Path
    provider_name: str
    provider_npi: str
    submitter_id: str
    databricks_host: str | None
    databricks_http_path: str | None
    databricks_token: str | None
    databricks_catalog: str
    databricks_schema: str
    anthropic_model: str

    @property
    def databricks_enabled(self) -> bool:
        return bool(self.databricks_host and self.databricks_http_path and self.databricks_token)


def get_settings() -> Settings:
    return Settings(
        db_path=Path(os.getenv("ELIGIBILITY_DB_PATH", PROJECT_ROOT / "data" / "eligibility.db")),
        provider_name=os.getenv("PROVIDER_NAME", "SUNRISE MEDICAL GROUP"),
        provider_npi=os.getenv("PROVIDER_NPI", "1234567893"),
        submitter_id=os.getenv("SUBMITTER_ID", "SUNRISEMG"),
        databricks_host=os.getenv("DATABRICKS_SERVER_HOSTNAME") or None,
        databricks_http_path=os.getenv("DATABRICKS_HTTP_PATH") or None,
        databricks_token=os.getenv("DATABRICKS_TOKEN") or None,
        databricks_catalog=os.getenv("DATABRICKS_CATALOG", "workspace"),
        databricks_schema=os.getenv("DATABRICKS_SCHEMA", "eligibility"),
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5"),
    )
