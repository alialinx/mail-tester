import redis

from src.config import REDIS_URL

client = redis.Redis.from_url(REDIS_URL, decode_responses=True)


def get_cache():
    return client


def address_key(to_address: str) -> str:
    return "mailtester:rcpt:" + (to_address or "").strip().lower()
