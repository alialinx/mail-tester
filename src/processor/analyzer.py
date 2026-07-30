from email.header import decode_header, make_header

from src.config import SPAM_PENALTY_CAP
from src.processor.content import inspect_content
from src.processor.score import Score
from src.processor.service import (
    check_spf,
    check_dkim,
    check_dmarc,
    check_rdns,
    check_blacklists,
    check_domain_blacklists,
    organizational_domain,
    is_public_ip,
)
from src.worker.spamassassin_client import spamd_check

SPAM_RULE_SKIP = (
    "SPF_", "T_SPF_", "DKIM_", "T_DKIM_", "DKIMWL_", "DMARC_", "RCVD_IN_", "URIBL_",
    "RDNS_", "HELO_", "MISSING_DATE", "MISSING_MID", "MISSING_SUBJECT", "NO_SUBJECT",
    "NO_RELAYS", "ALL_TRUSTED", "NO_RECEIVED",
    "SUBJ_ALL_CAPS", "HTML_IMAGE_ONLY", "HTML_IMAGE_RATIO", "MIME_HTML_ONLY",
    "T_SHORT_SHORTNER", "SHORT_SHORTNER",
)

SPF_PENALTY = {"fail": 2.0, "softfail": 1.0, "neutral": 0.5, "none": 0.5, "permerror": 1.0, "temperror": 0.3}


def decode_header_value(value):
    if not value:
        return value
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


class Analyzer:
    def __init__(self, email_message, domain, sender_ip=None, raw_email=None, connection=None, envelope_from=None):
        self.msg = email_message
        self.domain = domain
        self.sender_ip = sender_ip
        self.connection = connection or {}
        self.envelope_from = envelope_from
        self.raw_email = raw_email if isinstance(raw_email, bytes) else self._fallback_raw()
        self.public_ip = sender_ip if sender_ip and is_public_ip(sender_ip) else None
        self.score = Score()

    def _fallback_raw(self):
        try:
            return self.msg.as_bytes()
        except Exception:
            return b""

    def analyze(self):
        checks = {}
        helo = self.connection.get("helo")

        spf = check_spf(self.domain, self.public_ip, self.envelope_from, helo)
        checks["spf"] = spf

        if spf["status"] == "unknown":
            pass
        elif spf["status"] == "missing":
            self.score.minus(2.0, "SPF record not found", code="SPF_MISSING", severity="high",
                             how_to_fix=f"Add an SPF TXT record for {self.domain}. Example: v=spf1 a mx ~all")
        elif spf["result"] and spf["result"] != "pass":
            self.score.minus(SPF_PENALTY.get(spf["result"], 0.5), f"SPF check returned {spf['result']}",
                             code="SPF_" + spf["result"].upper(), severity="high",
                             details=spf.get("explanation") or "",
                             how_to_fix=f"Add {self.public_ip} to the SPF record of {self.domain}.")

        dkim = check_dkim(self.domain, self.raw_email)
        checks["dkim"] = dkim

        if dkim["status"] == "unknown":
            pass
        elif dkim["status"] == "missing":
            self.score.minus(1.5, "DKIM signature not found", code="DKIM_MISSING", severity="high",
                             how_to_fix=f"Sign outgoing mail with DKIM and publish the selector at "
                                        f"<selector>._domainkey.{self.domain}.")
        elif dkim["status"] == "broken":
            self.score.minus(1.0, "DKIM signature is malformed", code="DKIM_BROKEN", severity="high",
                             how_to_fix="The DKIM-Signature header has no selector tag. Check the signing setup.")
        elif dkim["status"] == "no_key":
            self.score.minus(1.5, "DKIM public key not published", code="DKIM_NO_KEY", severity="high",
                             details=f"{dkim['selector']}._domainkey.{dkim['signing_domain'] or self.domain}",
                             how_to_fix="Publish the DKIM selector TXT record in DNS.")
        elif dkim["verified"] is False:
            self.score.minus(1.5, "DKIM signature does not verify", code="DKIM_INVALID", severity="high",
                             details=dkim.get("error") or "",
                             how_to_fix="The signature does not match the message. Check whether a relay rewrites "
                                        "the body or headers after signing.")

        dmarc = check_dmarc(self.domain, spf, dkim)
        checks["dmarc"] = dmarc

        if dmarc["status"] == "unknown" or dmarc["result"] == "unknown":
            pass
        elif dmarc["status"] == "missing":
            self.score.minus(1.0, "DMARC record not found", code="DMARC_MISSING", severity="medium",
                             how_to_fix=f"Add a DMARC TXT record at _dmarc.{self.domain}. "
                                        f"Start with p=none, then enforce.")
        elif dmarc["result"] != "pass":
            self.score.minus(1.0, "DMARC alignment failed", code="DMARC_FAIL", severity="high",
                             details=f"aspf={dmarc['aspf']} adkim={dmarc['adkim']}",
                             how_to_fix="Make the From domain match the SPF or DKIM domain so DMARC can align.")
        elif (dmarc["policy"] or "none").lower() == "none":
            self.score.minus(0.3, "DMARC policy is set to none", code="DMARC_POLICY_NONE", severity="low",
                             how_to_fix="Move to p=quarantine and then p=reject once your reports look clean.")

        headers = {name: decode_header_value(value) for name, value in self.msg.items()}
        header_check = {"status": "ok", "missing_required": [], "missing_recommended": [],
                        "raw": {
                            "from": headers.get("From"),
                            "to": headers.get("To"),
                            "subject": headers.get("Subject"),
                            "date": headers.get("Date"),
                            "message_id": headers.get("Message-ID"),
                            "reply_to": headers.get("Reply-To"),
                            "return_path": headers.get("Return-Path"),
                        }}

        if "Message-ID" not in headers:
            header_check["status"] = "warning"
            header_check["missing_required"].append("Message-ID")
            self.score.minus(0.5, "Message-ID header missing", code="HDR_MESSAGE_ID_MISSING", severity="medium",
                             how_to_fix="Let the sending server generate a unique Message-ID for every mail.")

        if "Date" not in headers:
            header_check["status"] = "warning"
            header_check["missing_required"].append("Date")
            self.score.minus(0.5, "Date header missing", code="HDR_DATE_MISSING", severity="medium",
                             how_to_fix="Add a Date header in RFC 5322 format.")

        if "List-Unsubscribe" not in headers:
            header_check["status"] = "warning"
            header_check["missing_recommended"].append("List-Unsubscribe")
            self.score.minus(0.2, "List-Unsubscribe header missing", code="HDR_LIST_UNSUB_MISSING", severity="low",
                             how_to_fix="Add List-Unsubscribe with a mailto and https URL, plus "
                                        "List-Unsubscribe-Post for one click unsubscribe.")

        from_domain = organizational_domain(self.domain)
        envelope_domain = organizational_domain((self.envelope_from or "").rsplit("@", 1)[-1])

        if self.envelope_from and envelope_domain and from_domain != envelope_domain:
            self.score.minus(0.3, "Envelope sender domain differs from the From domain",
                             code="HDR_ENVELOPE_MISMATCH", severity="low",
                             details=f"envelope={envelope_domain} from={from_domain}",
                             how_to_fix="Use the same domain in the envelope sender and the From header.")

        checks["headers"] = header_check
        checks["sender_ip"] = {"status": "ok" if self.sender_ip else "missing", "value": self.sender_ip}
        checks["connection"] = self.connection

        if not self.connection.get("tls"):
            self.score.minus(0.5, "Mail was delivered without TLS", code="TLS_MISSING", severity="medium",
                             how_to_fix="Enable opportunistic TLS on the sending server.")

        helo_check = {"value": helo, "is_fqdn": False, "matches_rdns": None}
        if helo:
            bare_helo = helo.strip("[]")
            helo_check["is_fqdn"] = "." in bare_helo and not bare_helo.replace(".", "").isdigit()

            if not helo_check["is_fqdn"]:
                self.score.minus(0.3, "HELO name is not a fully qualified hostname", code="HELO_NOT_FQDN",
                                 severity="medium", details=helo,
                                 how_to_fix="Set the sending server to greet with its own public hostname.")

        if self.public_ip:
            rdns = check_rdns(self.public_ip)
            rdns["skipped"] = False
            rdns["status"] = ("unknown" if rdns.get("success") is None
                              else "ok" if rdns.get("matches")
                              else "warning" if rdns.get("success") else "missing")
            checks["rdns"] = rdns

            if rdns.get("success") is None:
                pass
            elif not rdns.get("success"):
                self.score.minus(0.5, "Reverse DNS record not found", code="RDNS_MISSING", severity="medium",
                                 how_to_fix=f"Ask the network owner to add a PTR record for {self.public_ip}.")
            elif rdns.get("matches") is False:
                self.score.minus(0.5, "Reverse DNS is not forward confirmed", code="RDNS_NO_MATCH", severity="medium",
                                 details=f"{rdns.get('hostname')} -> {', '.join(rdns.get('forward_ips') or []) or 'no A record'}",
                                 how_to_fix=f"Make {rdns.get('hostname')} resolve back to {self.public_ip}.")

            if helo and rdns.get("hostname"):
                helo_check["matches_rdns"] = helo.strip("[]").lower() == rdns["hostname"].lower()
                if not helo_check["matches_rdns"]:
                    self.score.minus(0.2, "HELO name does not match reverse DNS", code="HELO_RDNS_MISMATCH",
                                     severity="low", details=f"helo={helo} rdns={rdns['hostname']}",
                                     how_to_fix="Use the same hostname for HELO and the PTR record.")

            bl = check_blacklists(self.public_ip)
            checks["blacklists"] = bl

            listed_on = [k for k, v in bl.get("results", {}).items() if v == "listed"]
            if listed_on:
                self.score.minus(min(2.0, 0.4 * len(listed_on)), "IP is listed in blacklists: " + ", ".join(listed_on),
                                 code="DNSBL_LISTED", severity="high", details=f"Listed on: {', '.join(listed_on)}",
                                 how_to_fix="Request delisting from the listing providers or send from a clean IP.")
        else:
            checks["rdns"] = {"success": None, "hostname": None, "forward_ips": [], "matches": None,
                              "status": "unknown", "skipped": True}
            checks["blacklists"] = {"checked": 0, "results": {}, "summary": {}, "skipped": True}

        checks["helo"] = helo_check

        content = inspect_content(self.msg, headers.get("Subject"))
        checks["content"] = content

        if content["has_html"] and not content["has_plain"]:
            self.score.minus(0.5, "No plain text alternative", code="BODY_NO_PLAIN", severity="medium",
                             how_to_fix="Send multipart/alternative with both a text/plain and a text/html part.")

        if content["text_ratio"] is not None and content["text_ratio"] < 0.05:
            self.score.minus(0.5, "Almost no text next to the HTML markup", code="BODY_LOW_TEXT", severity="medium",
                             details=f"text/html ratio {content['text_ratio']}",
                             how_to_fix="Add real text content instead of relying on images and markup.")

        if content["image_count"] and content["text_length"] < 100:
            self.score.minus(0.5, "Image heavy mail with very little text", code="BODY_IMAGE_ONLY", severity="medium",
                             how_to_fix="Spam filters distrust image only mail. Add meaningful text.")

        if content["images_without_alt"]:
            self.score.minus(0.2, f"{content['images_without_alt']} image(s) without ALT text",
                             code="BODY_IMG_NO_ALT", severity="low",
                             how_to_fix="Add an alt attribute to every image.")

        if content["shortened_links"]:
            self.score.minus(0.5, "Shortened links found: " + ", ".join(content["shortened_links"]),
                             code="BODY_SHORT_LINKS", severity="medium",
                             how_to_fix="Use your own domain for links instead of a URL shortener.")

        if not content["has_unsubscribe_link"] and "List-Unsubscribe" not in headers:
            self.score.minus(0.2, "No unsubscribe link in the body", code="BODY_NO_UNSUB", severity="low",
                             how_to_fix="Add a visible unsubscribe link for bulk mail.")

        if not content["subject_length"]:
            self.score.minus(0.5, "Subject is empty", code="SUBJ_EMPTY", severity="medium",
                             how_to_fix="Always send a meaningful subject.")
        elif content["subject_all_caps"]:
            self.score.minus(0.3, "Subject is written in all capitals", code="SUBJ_ALL_CAPS", severity="low",
                             how_to_fix="Use normal capitalisation in the subject.")

        if content["subject_exclamations"] > 2:
            self.score.minus(0.2, "Subject has too many exclamation marks", code="SUBJ_EXCLAIM", severity="low",
                             how_to_fix="Keep punctuation in the subject to a minimum.")

        domain_targets = [self.domain] + [h for h in content["link_hosts"]]
        domain_bl = check_domain_blacklists(domain_targets)
        checks["domain_blacklists"] = domain_bl

        if domain_bl["listed"]:
            names = ", ".join(sorted({item["domain"] for item in domain_bl["listed"]}))
            self.score.minus(min(2.0, 1.0 * len(domain_bl["listed"])), "Domain is listed in blacklists: " + names,
                             code="URIBL_LISTED", severity="high",
                             details="; ".join(f"{i['domain']} on {i['list']}" for i in domain_bl["listed"]),
                             how_to_fix="Request delisting for the listed domain, or remove the link from the mail.")

        sa = spamd_check(self.raw_email)
        checks["spamassassin"] = sa

        penalty, counted = self.spam_penalty(sa)
        if penalty > 0:
            self.score.minus(penalty, f"SpamAssassin flagged {len(counted)} content rule(s)",
                             code="SPAM_RULES", severity="high",
                             details="; ".join(f"{r['name']} {r['points']:+}" for r in counted),
                             how_to_fix="Fix the wording, formatting and links the rules above point at.")

        meta = {
            "sender_domain": self.domain,
            "sender_ip": self.sender_ip,
            "message_id": headers.get("Message-ID"),
            "subject": headers.get("Subject"),
            "from": headers.get("From"),
            "to": headers.get("To"),
            "helo": helo,
            "tls": self.connection.get("tls"),
        }

        base = self.score.result()
        meta["message_detail"] = {
            "from": headers.get("From"),
            "to": headers.get("To"),
            "subject": headers.get("Subject"),
            "body": content.get("preview"),
            "plain": content.get("plain"),
            "html": content.get("html"),
            "message_id": headers.get("Message-ID"),
            "date": headers.get("Date"),
        }

        base["meta"] = meta
        base["checks"] = checks
        base["raw_email"] = self.raw_email.decode("utf-8", errors="replace")
        base["summary"] = {"score": base["score"], "grade": base["title"], "headline": base["description"],
                           "top_issues": base.get("issues", [])[:3]}

        return base

    def spam_penalty(self, sa: dict):
        if not sa or sa.get("status") != "ok" or sa.get("score") is None:
            return 0.0, []

        counted = [r for r in (sa.get("rules") or [])
                   if r.get("points", 0) > 0 and not r.get("name", "").startswith(SPAM_RULE_SKIP)]

        if not counted:
            return 0.0, []

        return min(SPAM_PENALTY_CAP, round(sum(r["points"] for r in counted), 2)), counted
