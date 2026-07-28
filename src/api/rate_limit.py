from fastapi import HTTPException

from src.db.cache import get_cache


def hit(scope: str, identity: str, limit: int, window: int) -> dict:
    key = f"mailtester:rate:{scope}:{identity}"
    cache = get_cache()

    try:
        pipe = cache.pipeline()
        pipe.incr(key)
        pipe.ttl(key)
        used, ttl = pipe.execute()
    except Exception as e:
        print("rate limit sayaci okunamadi:", scope, identity, repr(e), flush=True)
        return {"allowed": True, "used": 0, "limit": limit, "retry_after": 0}

    if used == 1 or ttl is None or ttl < 0:
        try:
            cache.expire(key, window)
        except Exception:
            pass
        ttl = window

    return {
        "allowed": used <= limit,
        "used": int(used),
        "limit": limit,
        "retry_after": int(ttl),
    }


def enforce(scope: str, identity: str, limit: int, window: int) -> None:
    result = hit(scope, identity, limit, window)

    if not result["allowed"]:
        raise HTTPException(
            status_code=429,
            detail="Too many requests, try again later",
            headers={"Retry-After": str(result["retry_after"])},
        )
