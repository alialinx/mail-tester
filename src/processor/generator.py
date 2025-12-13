import secrets
from datetime import datetime

from src.config import DOMAIN
from src.db.db import get_db

domain = DOMAIN
db = get_db()

def generate_random_email():
    token = "test" + "-" + secrets.token_hex(10)
    test_mail = token + "@" + domain
    return test_mail
