import secrets

from src.config import MAIL_DOMAIN


def generate_random_email():
    token = "test" + "-" + secrets.token_hex(10)
    test_mail = token + "@" + MAIL_DOMAIN
    return test_mail
