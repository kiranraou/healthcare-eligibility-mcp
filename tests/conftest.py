import pytest

from src.seed import seed


@pytest.fixture(autouse=True)
def eligibility_db(tmp_path, monkeypatch):
    """Give every test its own freshly seeded database."""
    path = tmp_path / "eligibility.db"
    monkeypatch.setenv("ELIGIBILITY_DB_PATH", str(path))
    for var in ("DATABRICKS_SERVER_HOSTNAME", "DATABRICKS_HTTP_PATH", "DATABRICKS_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    seed(path)
    return path
