import os
import time
from collections import defaultdict, deque

_local_windows: dict[str, deque[float]] = defaultdict(deque)


def allow_request(tenant_id: str, limit: int) -> bool:
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        try:
            from redis import Redis
            bucket = int(time.time() // 60)
            client = Redis.from_url(redis_url, socket_connect_timeout=0.5, socket_timeout=0.5)
            key = f"gateway:limit:{tenant_id}:{bucket}"
            count = client.incr(key)
            if count == 1: client.expire(key, 65)
            return count <= limit
        except (OSError, TimeoutError, ConnectionError):
            pass
    now = time.monotonic()
    window = _local_windows[tenant_id]
    while window and window[0] <= now - 60: window.popleft()
    if len(window) >= limit: return False
    window.append(now)
    return True
