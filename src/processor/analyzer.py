from src.imap.imap import get_email_from_imap
from src.processor.score import Score
from src.processor.service import check_spf_record, check_dkim_record,check_dmarc_record,check_rdns,check_blacklists


class Analyzer:
    def __init__(self, email_message,domain,sender_ip=None):
        self.msg = email_message
        self.domain = domain
        self.sender_ip = sender_ip
        self.score = Score()

    def analyze(self):


        if not check_spf_record(self.domain):
            self.score.minus(2.0, "SPF record not found")


        if not check_dkim_record(self.domain):
            self.score.minus(1.5, "DKIM record not found")


        if not check_dmarc_record(self.domain):
            self.score.minus(1.5, "DMARC record not found")


        headers = dict(self.msg.items())

        if "Message-ID" not in headers:
            self.score.minus(0.5, "Message-ID header missing")

        if "Date" not in headers:
            self.score.minus(0.5, "Date header missing")


        if "List-Unsubscribe" not in headers:
            self.score.minus(0.2, "List-Unsubscribe header missing")

        if self.sender_ip:
            rdns = check_rdns(self.sender_ip)
            if not rdns["success"]:
                self.score.minus(0.4, "Reverse DNS not matching")


        if self.sender_ip:

            blacklist = check_blacklists(self.sender_ip)

            listed = []

            for name,status in blacklist.items():
                if status == "listed":
                    listed.append(name)

            if listed:
                self.score.minus(2.0, "IP is listed blacklist: " + ", ".join(listed))


        return self.score.result()
