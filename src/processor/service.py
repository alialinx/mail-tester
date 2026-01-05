import ipaddress
import re
import dns.resolver
import smtplib


from src.config import DNSBL_TIMEOUT, DNSBL_LIFETIME, DNSBL_MAX_LISTS, DNSBL_CONCURRENCY
from concurrent.futures import ThreadPoolExecutor, as_completed

def check_spf_record(domain: str) -> bool:
    records = dns.resolver.resolve(domain, "TXT")
    spf_list = []
    for r in records:
        spf_list.append(r.to_text())
    if spf_list != []:
        return True, spf_list
    else:
        return False, spf_list

def _as_raw_string(msg_or_raw) -> str:

    if isinstance(msg_or_raw, str):
        return msg_or_raw
    if hasattr(msg_or_raw, "as_string"):
        return msg_or_raw.as_string()
    return str(msg_or_raw)

def get_dkim_content(msg_raw: str):
    msg_raw = _as_raw_string(msg_raw)
    msg_list = msg_raw.splitlines()

    record_list = []
    in_dkim = False

    for line in msg_list:
        if line == "":
            break

        if line.lower().startswith("dkim-signature:"):
            in_dkim = True
            record_list.append(line)
            continue

        if in_dkim and line.startswith(("\t", " ")):
            record_list.append(line)
            continue

        if in_dkim:
            break

    return record_list


def get_dkim_selector(record_list: list):
    if not record_list:
        return None

    clean_record_list = [x.lstrip(" \t") for x in record_list]
    joined = " ".join([x.strip() for x in clean_record_list])

    m = re.search(r"(?:^|;)\s*s=([^;]+)", joined, flags=re.IGNORECASE)
    return m.group(1).strip() if m else None



def check_dkim_record(domain: str, msg_raw: str):
    dkim_record = None

    dkim_content = get_dkim_content(msg_raw)
    clean_dkim_content = [x.lstrip(" \t") for x in dkim_content] if dkim_content else []


    if not dkim_content:
        return False, None, []

    selector = get_dkim_selector(dkim_content)

    if not selector:
        return False, None, clean_dkim_content

    dkim_domain = f"{selector}._domainkey.{domain}"

    try:
        answer = dns.resolver.resolve(dkim_domain, "TXT")
    except Exception:
        return False, None, clean_dkim_content

    for dkim in answer:
        dkim_record = dkim.to_text()

    if not dkim_record:
        return False, None, clean_dkim_content

    return True, dkim_record, clean_dkim_content


def _txt_to_str(rdata) -> str:
    try:
        return b"".join(rdata.strings).decode("utf-8", errors="replace")
    except Exception:
        return str(rdata).strip('"')

def check_dmarc_record(domain: str):
    dmarc_domain = f"_dmarc.{domain}".strip(".")

    try:
        answers = dns.resolver.resolve(dmarc_domain, "TXT")
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers, dns.exception.Timeout):

        return False, None
    except Exception:
        return False, None


    records = [_txt_to_str(r) for r in answers]
    dmarc = next((t for t in records if "v=DMARC1" in t), None)

    return (dmarc is not None), dmarc


def check_rdns(ip: str) -> dict:
    reversed_ip = ".".join(ip.split(".")[::-1]) + ".in-addr.arpa"
    try:
        answer = dns.resolver.resolve(reversed_ip, "PTR")
        hostname = str(answer[0]).rstrip(".")
        return {"success": True, "hostname": hostname}
    except Exception:
        return {"success": False, "hostname": None}

DNSBL_LISTS = [
    "zen.spamhaus.org",
    "bl.spamcop.net",
    "dnsbl.sorbs.net",
    "b.barracudacentral.org",
    "cbl.abuseat.org",
    "ips.backscatterer.org",
    "truncate.gbudb.net",
    "ubl.unsubscore.com",
    "virus.rbl.msrbl.net",
    "spam.rbl.msrbl.net",
    "phishing.rbl.msrbl.net",
    "ricn.dnsbl.net.au",
    "dnsbl.kempt.net",
    "bl.mailspike.net",
    "z.mailspike.net",
    "bl.score.senderscore.com",
    "dnsbl.dronebl.org",
    "dnsbl.spfbl.net",
    "dnsbl.cyberlogic.net",
    "psbl.surriel.com",
    "bl.blocklist.de",
    "rbl.interserver.net",
    "bad.psky.me",
    "hostkarma.junkemailfilter.com",
    "bl.konstant.no",
    "dnsbl.anticaptcha.net",
    "all.s5h.net",
]

def check_blacklists(ip: str) -> dict:
    if not ip:
        return {"checked": 0, "results": {}, "summary": {"listed": 0, "not_listed": 0, "timeout": 0, "error": 0}}

    reversed_ip = ".".join(ip.split(".")[::-1])

    resolver = dns.resolver.Resolver(configure=True)
    resolver.timeout = DNSBL_TIMEOUT
    resolver.lifetime = DNSBL_LIFETIME

    dnsbls = DNSBL_LISTS[:DNSBL_MAX_LISTS]
    results = {}

    def query_one(dnsbl: str):
        q = f"{reversed_ip}.{dnsbl}"
        try:
            resolver.resolve(q, "A")
            return dnsbl, "listed"
        except dns.resolver.NXDOMAIN:
            return dnsbl, "not_listed"
        except (dns.resolver.Timeout, dns.exception.Timeout):
            return dnsbl, "timeout"
        except Exception:
            return dnsbl, "error"

    with ThreadPoolExecutor(max_workers=max(1, DNSBL_CONCURRENCY)) as ex:
        futures = [ex.submit(query_one, dnsbl) for dnsbl in dnsbls]
        for fut in as_completed(futures):
            dnsbl, status = fut.result()
            results[dnsbl] = status

    summary = {"listed": 0, "not_listed": 0, "timeout": 0, "error": 0}
    for st in results.values():
        summary[st] += 1

    return {"checked": len(results), "results": results, "summary": summary}

def get_mx_record(domain: str):
    try:
        mx_records = []
        answers = dns.resolver.resolve(domain, "MX")
        for r in answers:
            host = str(r.exchange).rstrip(".")
            mx_records.append(host)
        return mx_records
    except Exception:
        return False


def check_a_record(domain: str) -> bool:
    try:
        records = dns.resolver.resolve(domain, "A")
        return bool(records)
    except Exception:
        return False


def check_smtp_server(domain: str) -> bool:
    mx_records = get_mx_record(domain)
    if not mx_records:
        return False

    for mx in mx_records:
        try:
            server = smtplib.SMTP(mx, 25, timeout=6)
            server.ehlo()
            server.quit()
        except Exception:
            return False

    return True


def check_user_ctrl(domain: str, user: str):
    mx_records = get_mx_record(domain)
    if not mx_records:
        return False

    for mx in mx_records:
        try:
            server = smtplib.SMTP(mx, 25, timeout=6)
            server.ehlo()
            server.mail(user)
            code, _ = server.rcpt(user)
            server.quit()

            return code == 250

        except Exception as e:
            return {"message": str(e)}



def is_public_ip(ip: str) -> bool:
    try:
        ip_obj = ipaddress.ip_address(ip)
        return ip_obj.is_global
    except ValueError:
        return False


def get_sender_ip(msg):
    received_headers = msg.get_all("Received", [])

    # Received header'lar sondan başa okunur
    for header in reversed(received_headers):
        ips = re.findall(r'\[(\d{1,3}(?:\.\d{1,3}){3})\]', header)

        for ip in ips:
            if is_public_ip(ip):
                return ip

    return None