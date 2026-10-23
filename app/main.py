import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import cache, limits
from app.database import Base, engine, get_db
from app.models import Tenant, UsageRecord
from app.providers import generate, stream
from app.schemas import ChatRequest, ChatResponse, TenantCreate, TenantCreated, UsageOut
from app.security import issue_key, verify_key

Base.metadata.create_all(bind=engine)
app = FastAPI(title="LLM Gateway and Semantic Cache", version="0.1.0")


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    details = [{"type": e.get("type"), "loc": e.get("loc"), "msg": e.get("msg")} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"error": {"code": "validation_error", "message": "Request validation failed", "details": jsonable_encoder(details)}})


@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "request_error", "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content={"error": {**detail, "details": detail.get("details", [])}})


def authenticated_tenant(authorization: str | None, db: Session) -> Tenant:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"code": "unauthorized", "message": "A tenant bearer key is required"})
    raw = authorization.removeprefix("Bearer ").strip()
    for tenant in db.scalars(select(Tenant).where(Tenant.active.is_(True))):
        if verify_key(raw, tenant.salt, tenant.api_key_hash):
            return tenant
    raise HTTPException(status_code=401, detail={"code": "unauthorized", "message": "Invalid tenant key"})


def meter(db: Session, tenant: Tenant, provider: str, model: str, input_tokens: int, output_tokens: int, cache_hit: bool):
    db.add(UsageRecord(tenant_id=tenant.id, provider=provider, model=model, input_tokens=input_tokens, output_tokens=output_tokens, cache_hit=cache_hit))
    db.commit()


@app.get("/health")
def health():
    return {"status": "ok", "redis_configured": bool(os.getenv("REDIS_URL"))}


@app.post("/v1/admin/tenants", response_model=TenantCreated, status_code=201)
def create_tenant(payload: TenantCreate, x_bootstrap_secret: str | None = Header(default=None), db: Session = Depends(get_db)):
    secret = os.getenv("TENANT_BOOTSTRAP_SECRET")
    if not secret or not x_bootstrap_secret or not secrets.compare_digest(secret, x_bootstrap_secret):
        raise HTTPException(status_code=403, detail={"code": "provisioning_disabled", "message": "Tenant provisioning is disabled or unauthorized"})
    raw, salt, digest = issue_key()
    tenant = Tenant(id=str(uuid4()), name=payload.name.strip(), api_key_hash=digest, salt=salt, requests_per_minute=payload.requests_per_minute)
    db.add(tenant)
    db.commit()
    return {"tenant_id": tenant.id, "name": tenant.name, "api_key": raw}


def _prepare(payload: ChatRequest, authorization: str | None, db: Session) -> tuple[Tenant, ChatResponse]:
    tenant = authenticated_tenant(authorization, db)
    if not limits.allow_request(tenant.id, tenant.requests_per_minute):
        raise HTTPException(status_code=429, detail={"code": "rate_limited", "message": "Tenant request limit exceeded"})
    model = payload.model or "gpt-4o-mini"
    cached = cache.find(db, payload.prompt, model)
    if cached:
        meter(db, tenant, cached.provider, cached.model, 0, 0, True)
        return tenant, ChatResponse(response=cached.response, provider=cached.provider, model=cached.model, input_tokens=0, output_tokens=0, cache_hit=True)
    try:
        text, provider, actual_model, input_tokens, output_tokens = generate(payload.prompt, payload.max_tokens, payload.model)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail={"code": "providers_unavailable", "message": "All configured providers failed"}) from exc
    cache.store(db, payload.prompt, text, provider, actual_model)
    meter(db, tenant, provider, actual_model, input_tokens, output_tokens, False)
    return tenant, ChatResponse(response=text, provider=provider, model=actual_model, input_tokens=input_tokens, output_tokens=output_tokens, cache_hit=False)


@app.post("/v1/chat", response_model=ChatResponse)
def chat(payload: ChatRequest, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    return _prepare(payload, authorization, db)[1]


@app.post("/v1/chat/stream")
def chat_stream(payload: ChatRequest, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    tenant = authenticated_tenant(authorization, db)
    if not limits.allow_request(tenant.id, tenant.requests_per_minute):
        raise HTTPException(status_code=429, detail={"code": "rate_limited", "message": "Tenant request limit exceeded"})
    cached = cache.find(db, payload.prompt, payload.model or "gpt-4o-mini")
    if cached:
        def cached_events():
            yield "data: " + json.dumps({"type": "token", "text": cached.response}) + "\n\n"
            yield "data: " + json.dumps({"type": "done", "provider": cached.provider, "model": cached.model, "cache_hit": True}) + "\n\n"
        meter(db, tenant, cached.provider, cached.model, 0, 0, True)
        return StreamingResponse(cached_events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    def events():
        pieces = []
        provider_name = model_name = "unknown"
        input_tokens = output_tokens = 0
        try:
            for event in stream(payload.prompt, payload.max_tokens, payload.model):
                if event["type"] == "token": pieces.append(event["text"])
                else: provider_name, model_name, input_tokens, output_tokens = event["provider"], event["model"], event["input_tokens"], event["output_tokens"]
                yield "data: " + json.dumps(event) + "\n\n"
            cache.store(db, payload.prompt, "".join(pieces).strip(), provider_name, model_name)
            meter(db, tenant, provider_name, model_name, input_tokens, output_tokens, False)
        except RuntimeError:
            yield "data: " + json.dumps({"type": "error", "code": "providers_unavailable"}) + "\n\n"
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/v1/usage", response_model=UsageOut)
def usage(authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    tenant = authenticated_tenant(authorization, db)
    start = datetime.now(UTC) - timedelta(days=30)
    rows = list(db.scalars(select(UsageRecord).where(UsageRecord.tenant_id == tenant.id, UsageRecord.created_at >= start)))
    return {"tenant_id": tenant.id, "requests": len(rows), "input_tokens": sum(x.input_tokens for x in rows), "output_tokens": sum(x.output_tokens for x in rows), "cache_hits": sum(x.cache_hit for x in rows), "period_start": start}
