import os
from urllib.parse import quote_plus

from dotenv import load_dotenv
from cryptography.fernet import Fernet

load_dotenv()


MONGO_HOST = os.getenv("MONGO_HOST", "mongo")
MONGO_PORT = int(os.getenv("MONGO_PORT"))
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "mail_tester")

MONGO_DB_USER = (os.getenv("MONGO_DB_USER") or "").strip()
MONGO_DB_PASS = (os.getenv("MONGO_DB_PASS") or "").strip()
MONGO_AUTH_SOURCE = (os.getenv("MONGO_AUTH_SOURCE") or "").strip()

def build_mongo_uri() -> str:

    if not MONGO_DB_USER or not MONGO_DB_PASS:
        return f"mongodb://{MONGO_HOST}:{MONGO_PORT}/{MONGO_DB_NAME}"

    auth_source = MONGO_AUTH_SOURCE or MONGO_DB_NAME

    user = quote_plus(MONGO_DB_USER)
    pwd = quote_plus(MONGO_DB_PASS)

    return (
        f"mongodb://{user}:{pwd}@{MONGO_HOST}:{MONGO_PORT}/{MONGO_DB_NAME}"
        f"?authSource={auth_source}"
    )

MONGO_URI = build_mongo_uri()

DOMAIN = os.getenv("DOMAIN")
IMAP_HOST = os.getenv("IMAP_HOST")
IMAP_PORT = os.getenv("IMAP_PORT")
IMAP_EMAIL = os.getenv("IMAP_EMAIL")
IMAP_PASSWORD = os.getenv("IMAP_PASSWORD")
IMAP_FOLDER = os.getenv("IMAP_FOLDER")


SPAMD_HOST = os.getenv("SPAMD_HOST", "spamassassin")
SPAMD_TIMEOUT = float(os.getenv("SPAMD_TIMEOUT", "3.0"))
SPAMD_PORT = int(os.getenv("SPAMD_PORT", "783"))

DNSBL_TIMEOUT = float(os.getenv("DNSBL_TIMEOUT", "2.0"))
DNSBL_LIFETIME = float(os.getenv("DNSBL_LIFETIME", "2.0"))
DNSBL_MAX_LISTS = int(os.getenv("DNSBL_MAX_LISTS", "20"))
DNSBL_CONCURRENCY = int(os.getenv("DNSBL_CONCURRENCY", "10"))