import hashlib
import math
import os
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CacheRecord


def prompt_hash(prompt: str, model: str) -> str:
    return hashlib.sha256(prompt.strip().casefold().encode()).hexdigest()


def _embedding(text: str) -> list[float] | None:
    if not os.getenv("OPENAI_API_KEY"): return None
    try:
        from openai import OpenAI
        return OpenAI(api_key=os.environ["OPENAI_API_KEY"]).embeddings.create(model="text-embedding-3-small", input=text[:8000]).data[0].embedding
    except (ValueError, RuntimeError):
        return None


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    nl, nr = math.sqrt(sum(a*a for a in left)), math.sqrt(sum(b*b for b in right))
    return dot / (nl * nr) if nl and nr else 0.0


def find(db: Session, prompt: str, model: str) -> CacheRecord | None:
    now = datetime.now(UTC)
    exact = db.scalar(select(CacheRecord).where(CacheRecord.prompt_hash == prompt_hash(prompt, model), CacheRecord.expires_at > now))
    if exact: return exact
    vector = _embedding(prompt)
    if vector is None: return None
    threshold = float(os.getenv("CACHE_SIMILARITY_THRESHOLD", "0.94"))
    candidates = db.scalars(select(CacheRecord).where(CacheRecord.model == model, CacheRecord.expires_at > now).limit(500))
    best, score = None, threshold
    for row in candidates:
        if row.embedding:
            similarity = _cosine(vector, row.embedding)
            if similarity >= score: best, score = row, similarity
    return best


def store(db: Session, prompt: str, response: str, provider: str, model: str) -> None:
    key = prompt_hash(prompt, model)
    row = db.scalar(select(CacheRecord).where(CacheRecord.prompt_hash == key))
    values = {"prompt": prompt, "response": response, "provider": provider, "model": model, "embedding": _embedding(prompt), "expires_at": datetime.now(UTC) + timedelta(seconds=max(1, int(os.getenv("CACHE_TTL_SECONDS", "300"))))}
    if row:
        for name, value in values.items(): setattr(row, name, value)
    else:
        db.add(CacheRecord(prompt_hash=key, **values))
    db.commit()
