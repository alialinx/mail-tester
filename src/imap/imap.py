import email
import imaplib

from src.config import IMAP_EMAIL, IMAP_HOST, IMAP_PASSWORD, IMAP_PORT, IMAP_FOLDER

imap_host = IMAP_HOST
imap_password = IMAP_PASSWORD
imap_email = IMAP_EMAIL
imap_port = IMAP_PORT
imap_folder = IMAP_FOLDER


def imap_conn():
    try:
        imap = imaplib.IMAP4_SSL(imap_host, 993)
        imap.login(imap_email, imap_password)
        imap.select(imap_folder)
        return imap
    except Exception as e:
        print("IMAP bağlantı hatası:", e)
        return e



def get_email_from_imap(to_address):
    imap = imap_conn()

    status, data = imap.search(None,'HEADER', 'X-Original-To', to_address)

    if status == "OK" and data[0]:
        mail_id = data[0].split()[-1]

    else:
        status, data = imap.search(None,'HEADER', 'To', to_address)

        if status != "OK" or not data[0]:
            imap.logout()
            return None

        mail_id = data[0].split()[-1]

    status, msg_data = imap.fetch(mail_id, '(RFC822)')
    if status != "OK":
        imap.logout()
        return None

    raw_email = msg_data[0][1]
    msg = email.message_from_bytes(raw_email)

    imap.logout()
    return msg


