import os
from urllib.parse import quote_plus

from dotenv import load_dotenv
from cryptography.fernet import Fernet

load_dotenv()


MONGO_HOST = os.getenv("MONGO_HOST", "mongo")
MONGO_PORT = int(os.getenv("MONGO_PORT", "27017"))
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "mail_tester")

MONGO_DB_USER = (os.getenv("MONGO_DB_USER") or "").strip()
MONGO_DB_PASS = (os.getenv("MONGO_DB_PASS") or "").strip()
MONGO_AUTH_SOURCE = (os.getenv("MONGO_AUTH_SOURCE") or "").strip()


MONGODB_URI = os.getenv("MONGODB_URI")
DOMAIN = os.getenv("DOMAIN")

# Test adreslerinin domaini. Eski kurulumlarda DOMAIN adıyla duruyordu.
MAIL_DOMAIN = os.getenv("MAIL_DOMAIN") or DOMAIN

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/2")

INGEST_LMTP_PORT = int(os.getenv("INGEST_LMTP_PORT", "2400"))
INGEST_MAP_PORT = int(os.getenv("INGEST_MAP_PORT", "2500"))
MESSAGE_SIZE_LIMIT = int(os.getenv("MESSAGE_SIZE_LIMIT", "26214400"))
TEST_ADDRESS_TTL_MINUTES = int(os.getenv("TEST_ADDRESS_TTL_MINUTES", "10"))

IMAP_HOST = os.getenv("IMAP_HOST")
IMAP_PORT = os.getenv("IMAP_PORT")
IMAP_EMAIL = os.getenv("IMAP_EMAIL")
IMAP_PASSWORD = os.getenv("IMAP_PASSWORD")
IMAP_FOLDER = os.getenv("IMAP_FOLDER")


SPAMD_HOST = os.getenv("SPAMD_HOST", "spamassassin")
SPAMD_TIMEOUT = float(os.getenv("SPAMD_TIMEOUT", "3.0"))
SPAMD_PORT = int(os.getenv("SPAMD_PORT", "783"))

# Kendi unbound container'ımız. Boş bırakılırsa sistemin resolver'ı kullanılır.
DNS_RESOLVER = (os.getenv("DNS_RESOLVER") or "").strip()
DNS_TIMEOUT = float(os.getenv("DNS_TIMEOUT", "3.0"))
DNS_LIFETIME = float(os.getenv("DNS_LIFETIME", "5.0"))

SSE_TIMEOUT = int(os.getenv("SSE_TIMEOUT", "900"))

DNSBL_TIMEOUT = float(os.getenv("DNSBL_TIMEOUT", "2.0"))
DNSBL_LIFETIME = float(os.getenv("DNSBL_LIFETIME", "2.0"))
DNSBL_MAX_LISTS = int(os.getenv("DNSBL_MAX_LISTS", "20"))
DNSBL_CONCURRENCY = int(os.getenv("DNSBL_CONCURRENCY", "10"))



TOKEN_EXPIRE_MINUTES = int(os.getenv("TOKEN_EXPIRE_MINUTES", "1440"))
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM")