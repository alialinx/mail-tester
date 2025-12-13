import ipaddress
import re

import dns.resolver
import smtplib

def check_spf_record(domain: str) -> bool:
    try:
        records = dns.resolver.resolve(domain, "TXT")
        for r in records:
            if "v=spf1" in r.to_text():
                return True
        return False
    except Exception:
        return False


def check_dkim_record(domain: str, selector: str = "default") -> bool:
    try:
        dkim_domain = f"{selector}._domainkey.{domain}"
        dns.resolver.resolve(dkim_domain, "TXT")
        return True
    except Exception:
        return False


def check_dmarc_record(domain: str) -> bool:
    try:
        dmarc_domain = f"_dmarc.{domain}"
        records = dns.resolver.resolve(dmarc_domain, "TXT")
        for r in records:
            if "v=DMARC1" in r.to_text():
                return True
        return False
    except Exception:
        return False

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
    reversed_ip = ".".join(ip.split('.')[::-1])
    results = {}

    for dnsbl in DNSBL_LISTS:
        query = f"{reversed_ip}.{dnsbl}"
        try:
            dns.resolver.resolve(query, "A")
            results[dnsbl] = "listed"
        except dns.resolver.NXDOMAIN:
            results[dnsbl] = "not_listed"
        except Exception:
            results[dnsbl] = "error"

    return results



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