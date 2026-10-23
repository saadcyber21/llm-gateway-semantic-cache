from fastapi.testclient import TestClient

from app.database import Base, SessionLocal, engine
from app.limits import _local_windows
from app.main import app
from app.models import Tenant
from app.security import issue_key

client = TestClient(app)


def setup_function():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    _local_windows.clear()


def tenant_key(name="tenant", limit=50):
    raw, salt, digest = issue_key()
    with SessionLocal() as db:
        db.add(Tenant(id=name, name=name, api_key_hash=digest, salt=salt, requests_per_minute=limit))
        db.commit()
    return raw


def test_chat_mock_provider_and_usage():
    key = tenant_key()
    headers = {"Authorization": f"Bearer {key}"}
    response = client.post("/v1/chat", headers=headers, json={"prompt": "Say hello", "max_tokens": 30})
    assert response.status_code == 200 and response.json()["provider"] == "mock"
    usage = client.get("/v1/usage", headers=headers)
    assert usage.status_code == 200 and usage.json()["requests"] == 1


def test_exact_cache_hit():
    key = tenant_key()
    headers = {"Authorization": f"Bearer {key}"}
    payload = {"prompt": "cache this prompt", "max_tokens": 40}
    first = client.post("/v1/chat", headers=headers, json=payload)
    second = client.post("/v1/chat", headers=headers, json=payload)
    assert first.json()["cache_hit"] is False and second.json()["cache_hit"] is True


def test_invalid_api_key_rejected():
    assert client.post("/v1/chat", headers={"Authorization": "Bearer invalid"}, json={"prompt": "hello"}).status_code == 401


def test_rate_limit_applies():
    key = tenant_key(limit=1)
    headers = {"Authorization": f"Bearer {key}"}
    assert client.post("/v1/chat", headers=headers, json={"prompt": "one"}).status_code == 200
    assert client.post("/v1/chat", headers=headers, json={"prompt": "two"}).status_code == 429


def test_stream_endpoint_sends_sse():
    key = tenant_key()
    response = client.post("/v1/chat/stream", headers={"Authorization": f"Bearer {key}"}, json={"prompt": "stream me"})
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
    assert '"type": "done"' in response.text


def test_tenant_provisioning_requires_secret(monkeypatch):
    monkeypatch.delenv("TENANT_BOOTSTRAP_SECRET", raising=False)
    assert client.post("/v1/admin/tenants", json={"name": "created"}).status_code == 403
