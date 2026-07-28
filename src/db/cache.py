import redis

from src.config import REDIS_URL

client = redis.Redis.from_url(REDIS_URL, decode_responses=True)


def get_cache():
    return client


def address_key(to_address: str) -> str:
    return "mailtester:rcpt:" + (to_address or "").strip().lower()


def event_channel(to_address: str) -> str:
    return "mailtester:events:" + (to_address or "").strip().lower()


def publish_status(to_address: str, status: str) -> None:
    """Tarayıcıya SSE ile iletilecek durum değişikliği. Hata olursa akışı bozmuyoruz."""
    try:
        get_cache().publish(event_channel(to_address), status)
    except Exception as e:
        print("durum yayınlanamadı:", to_address, status, repr(e), flush=True)
