from fastapi.testclient import TestClient

from app.main import app
from app.version import APP_VERSION

client = TestClient(app)


def test_healthz_returns_200_ok_with_version() -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": APP_VERSION}
