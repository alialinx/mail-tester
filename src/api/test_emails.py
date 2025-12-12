import secrets
from datetime import datetime

from src.config import DOMAIN
from src.db.db import get_db

domain = DOMAIN
db = get_db()

def create_random_email(user_id:str = "aliqwe123"):
    token = "test" + "-" + secrets.token_hex(10)
    test_mail = token + "@" + domain

    payload = {
        "token": token,
        "email": test_mail,
        "created_at": datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
        "is_used": False,
        "user_id": user_id
    }
    db.test_emails.insert_one(payload)
    print(test_mail)
    return test_mail



create_random_email()