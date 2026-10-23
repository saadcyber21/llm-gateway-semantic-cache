from datetime import datetime

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=12000)
    max_tokens: int = Field(default=500, ge=1, le=4000)
    model: str | None = Field(default=None, max_length=120)


class ChatResponse(BaseModel):
    response: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_hit: bool


class TenantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    requests_per_minute: int = Field(default=60, ge=1, le=10000)


class TenantCreated(BaseModel):
    tenant_id: str
    name: str
    api_key: str


class UsageOut(BaseModel):
    tenant_id: str
    requests: int
    input_tokens: int
    output_tokens: int
    cache_hits: int
    period_start: datetime
