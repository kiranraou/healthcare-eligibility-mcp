"""Storage backends for the medallion pipeline.

Both sinks accept the same SQL with `:name` parameters. DDL uses type
placeholders ({STR}, {INT}, {DBL}, {BOOL}) that each sink fills in, because
SQLite and Databricks spell column types differently.
"""

import sqlite3
from pathlib import Path
from typing import Any, Protocol

from src.config import Settings


class Sink(Protocol):
    name: str

    def table(self, name: str) -> str: ...
    def execute(self, sql: str, params: dict[str, Any] | None = None) -> None: ...
    def executemany(self, sql: str, rows: list[dict[str, Any]]) -> None: ...
    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]: ...
    def close(self) -> None: ...


class SQLiteSink:
    """Local development backend: medallion tables live next to the operational tables."""

    name = "sqlite"
    types = {"STR": "TEXT", "INT": "INTEGER", "DBL": "REAL", "BOOL": "INTEGER"}

    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row

    def table(self, name: str) -> str:
        return name

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> None:
        self.conn.execute(sql.format(**self.types), params or {})
        self.conn.commit()

    def executemany(self, sql: str, rows: list[dict[str, Any]]) -> None:
        if rows:
            self.conn.executemany(sql, rows)
            self.conn.commit()

    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(sql, params or {}).fetchall()]

    def close(self) -> None:
        self.conn.close()


class DatabricksSink:
    """Databricks SQL warehouse backend: tables are Delta tables in <catalog>.<schema>."""

    name = "databricks"
    types = {"STR": "STRING", "INT": "BIGINT", "DBL": "DOUBLE", "BOOL": "BOOLEAN"}

    def __init__(self, settings: Settings, connection: Any | None = None):
        self.catalog = settings.databricks_catalog
        self.schema = settings.databricks_schema
        if connection is None:
            from databricks import sql as dbsql

            connection = dbsql.connect(
                server_hostname=settings.databricks_host,
                http_path=settings.databricks_http_path,
                access_token=settings.databricks_token,
            )
        self.conn = connection
        self.execute(f"CREATE SCHEMA IF NOT EXISTS {self.catalog}.{self.schema}")

    def table(self, name: str) -> str:
        return f"{self.catalog}.{self.schema}.{name}"

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> None:
        with self.conn.cursor() as cur:
            cur.execute(sql.format(**self.types), parameters=params or None)

    def executemany(self, sql: str, rows: list[dict[str, Any]]) -> None:
        if rows:
            with self.conn.cursor() as cur:
                cur.executemany(sql, rows)

    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with self.conn.cursor() as cur:
            cur.execute(sql, parameters=params or None)
            columns = [c[0] for c in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]

    def close(self) -> None:
        self.conn.close()


def get_sink(settings: Settings, backend: str | None = None) -> Sink:
    """backend: 'sqlite', 'databricks', or None to use Databricks when configured."""
    use_databricks = backend == "databricks" or (backend is None and settings.databricks_enabled)
    if use_databricks:
        if not settings.databricks_enabled:
            raise RuntimeError(
                "Databricks is not configured. Set DATABRICKS_SERVER_HOSTNAME, "
                "DATABRICKS_HTTP_PATH and DATABRICKS_TOKEN in .env."
            )
        return DatabricksSink(settings)
    return SQLiteSink(settings.db_path)
