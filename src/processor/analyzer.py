from src.processor.score import Score
from src.processor.service import (
    check_spf_record,
    check_dkim_record,
    check_dmarc_record,
    check_rdns,
    check_blacklists,
)
from src.worker.spamassassin_client import spamd_check


class Analyzer:
    def __init__(self, email_message, domain, sender_ip=None):
        self.msg = email_message
        self.domain = domain
        self.sender_ip = sender_ip
        self.score = Score()

    def analyze(self):
        checks = {}

        # ---------------------------
        # AUTH: SPF / DKIM / DMARC
        # ---------------------------
        spf_ok = check_spf_record(self.domain)
        checks["spf"] = {"status": "pass" if spf_ok else "missing", "domain": self.domain}
        if not spf_ok:
            self.score.minus(
                2.0,
                "SPF record not found",
                code="SPF_MISSING",
                severity="high",
                how_to_fix=f"Add an SPF TXT record for {self.domain}. Example: v=spf1 a mx ~all",
            )

        dkim_ok = check_dkim_record(self.domain)
        checks["dkim"] = {"status": "pass" if dkim_ok else "missing", "domain": self.domain, "selector": "default"}
        if not dkim_ok:
            self.score.minus(
                1.5,
                "DKIM record not found",
                code="DKIM_MISSING",
                severity="high",
                how_to_fix=f"Configure DKIM signing for {self.domain} and publish the selector TXT record (e.g., default._domainkey.{self.domain}).",
            )

        dmarc_ok = check_dmarc_record(self.domain)
        checks["dmarc"] = {"status": "pass" if dmarc_ok else "missing", "domain": self.domain}
        if not dmarc_ok:
            self.score.minus(
                1.5,
                "DMARC record not found",
                code="DMARC_MISSING",
                severity="medium",
                how_to_fix=f"Add a DMARC TXT record at _dmarc.{self.domain}. Start with p=none to monitor, then enforce.",
            )

        # ---------------------------
        # HEADERS
        # ---------------------------
        headers = dict(self.msg.items())
        header_check = {
            "status": "ok",
            "missing_required": [],
            "missing_recommended": [],
            "raw": {
                "from": headers.get("From"),
                "to": headers.get("To"),
                "subject": headers.get("Subject"),
                "date": headers.get("Date"),
                "message_id": headers.get("Message-ID"),
            },
        }

        if "Message-ID" not in headers:
            header_check["status"] = "warning"
            header_check["missing_required"].append("Message-ID")
            self.score.minus(0.5, "Message-ID header missing", code="HDR_MESSAGE_ID_MISSING", severity="medium")

        if "Date" not in headers:
            header_check["status"] = "warning"
            header_check["missing_required"].append("Date")
            self.score.minus(0.5, "Date header missing", code="HDR_DATE_MISSING", severity="medium")

        if "List-Unsubscribe" not in headers:
            header_check["status"] = "warning"
            header_check["missing_recommended"].append("List-Unsubscribe")
            self.score.minus(
                0.2,
                "List-Unsubscribe header missing",
                code="HDR_LIST_UNSUB_MISSING",
                severity="low",
                how_to_fix="Add List-Unsubscribe with mailto and/or https URL to improve deliverability and compliance.",
            )

        checks["headers"] = header_check

        # ---------------------------
        # SENDER IP / RDNS / BLACKLISTS
        # ---------------------------
        checks["sender_ip"] = {"status": "ok" if self.sender_ip else "missing", "value": self.sender_ip}

        if self.sender_ip:
            rdns = check_rdns(self.sender_ip)
            checks["rdns"] = rdns
            if not rdns.get("success"):
                self.score.minus(0.4, "Reverse DNS not matching", code="RDNS_FAIL", severity="low")

            bl = check_blacklists(self.sender_ip)  # senin fonksiyon summary/results döndürüyor
            checks["blacklists"] = bl

            listed_on = [k for k, v in bl.get("results", {}).items() if v == "listed"]
            if listed_on:
                self.score.minus(
                    2.0,
                    "IP is listed in blacklists: " + ", ".join(listed_on),
                    code="DNSBL_LISTED",
                    severity="high",
                    details=f"Listed on: {', '.join(listed_on)}",
                    how_to_fix="Request delisting from the DNSBL provider(s) or change sending IP.",
                )
        else:
            checks["rdns"] = {"success": False, "hostname": None, "skipped": True}
            checks["blacklists"] = {"checked": 0, "results": {}, "summary": {}, "skipped": True}

        # ---------------------------
        # SPAMASSASSIN
        # ---------------------------
        try:
            raw_email = self.msg.as_bytes()
        except Exception:
            raw_email = b""

        sa = spamd_check(raw_email)
        checks["spamassassin"] = sa

        # ---------------------------
        # META (UI için)
        # ---------------------------
        meta = {
            "sender_domain": self.domain,
            "sender_ip": self.sender_ip,
            "message_id": headers.get("Message-ID"),
            "subject": headers.get("Subject"),
            "from": headers.get("From"),
            "to": headers.get("To"),
        }

        base = self.score.result()


        base["meta"] = meta
        base["checks"] = checks



        base["summary"] = {
            "score": base["score"],
            "grade": base["title"],
            "headline": base["description"],
            "top_issues": base.get("issues", [])[:3],
        }

        return base
