import ipaddress
import re
import socket
import dns.resolver
import smtplib
import dkim
import spf


from src.config import DNSBL_TIMEOUT, DNSBL_LIFETIME, DNSBL_MAX_LISTS, DNSBL_CONCURRENCY
from src.config import DNS_RESOLVER, DNS_TIMEOUT, DNS_LIFETIME
from src.config import SPF_TIMEOUT, DKIM_MIN_KEY_BITS, URIBL_MAX_DOMAINS
from concurrent.futures import ThreadPoolExecutor, as_completed

_resolver_ip = {}

def get_resolver_ip():
    if not DNS_RESOLVER:
        return None

    if "ip" not in _resolver_ip:
        try:
            _resolver_ip["ip"] = socket.gethostbyname(DNS_RESOLVER)
        except Exception as e:
            print("dns resolver adresi çözülemedi:", DNS_RESOLVER, repr(e), flush=True)
            _resolver_ip["ip"] = None

    return _resolver_ip["ip"]


def get_resolver(timeout: float = None, lifetime: float = None) -> dns.resolver.Resolver:
    resolver_ip = get_resolver_ip()

    resolver = dns.resolver.Resolver(configure=not resolver_ip)
    if resolver_ip:
        resolver.nameservers = [resolver_ip]

    resolver.timeout = timeout or DNS_TIMEOUT
    resolver.lifetime = lifetime or DNS_LIFETIME
    return resolver


def spf_dns_lookup(name, qtype, tcpfallback=True, timeout=30):
    records = []

    try:
        answers = get_resolver().resolve(name, qtype)
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
        return records
    except (dns.resolver.NoNameservers, dns.exception.Timeout) as e:
        raise spf.TempError("DNS " + str(e))

    for rdata in answers:
        if qtype in ("A", "AAAA"):
            records.append(((name, qtype), rdata.address))
        elif qtype == "MX":
            records.append(((name, qtype), (rdata.preference, rdata.exchange)))
        elif qtype == "PTR":
            records.append(((name, qtype), rdata.target.to_text(True)))
        elif qtype in ("TXT", "SPF"):
            records.append(((name, qtype), rdata.strings))

    return records


def dkim_dns_lookup(name, timeout=5):
    if isinstance(name, bytes):
        name = name.decode("ascii", errors="ignore")

    try:
        answers = get_resolver().resolve(name.rstrip("."), "TXT")
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return None
    except Exception:
        raise

    for rdata in answers:
        return b"".join(rdata.strings)

    return None


def dkim_key_lookup(name):
    try:
        return dkim_dns_lookup(name), None
    except Exception as e:
        return None, type(e).__name__


spf.DNSLookup = spf_dns_lookup


def check_spf_record(domain: str):
    try:
        records = get_resolver().resolve(domain, "TXT")
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return False, []
    except Exception:
        return None, []

    spf_list = []
    for r in records:
        record = _txt_to_str(r)
        if record.lower().startswith("v=spf1"):
            spf_list.append(record)

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


def get_dkim_tag(record_list: list, tag: str):
    if not record_list:
        return None

    joined = " ".join([x.lstrip(" \t").strip() for x in record_list])

    m = re.search(r"(?:^|[;:])\s*" + tag + r"=([^;]+)", joined, flags=re.IGNORECASE)
    return m.group(1).strip() if m else None


def check_spf(domain: str, sender_ip: str = None, envelope_from: str = None, helo: str = None) -> dict:
    found, records = check_spf_record(domain)

    check = {"status": "ok" if found else "unknown" if found is None else "missing",
             "record": records, "domain": domain,
             "result": None, "explanation": None, "checked_ip": sender_ip, "checked_sender": None}

    if not found or not sender_ip:
        return check

    check["checked_sender"] = envelope_from or f"postmaster@{domain}"

    try:
        result, explanation = spf.check2(i=sender_ip, s=check["checked_sender"], h=helo or domain, timeout=SPF_TIMEOUT)
    except Exception as e:
        check["result"] = "temperror"
        check["explanation"] = repr(e)
        return check

    check["result"] = result
    check["explanation"] = explanation
    return check


def check_dkim(domain: str, raw_email) -> dict:
    raw_bytes = raw_email if isinstance(raw_email, bytes) else _as_raw_string(raw_email).encode("utf-8", errors="replace")
    dkim_content = get_dkim_content(raw_bytes.decode("utf-8", errors="replace"))
    clean_dkim_content = [x.lstrip(" \t") for x in dkim_content] if dkim_content else []

    check = {"status": "missing", "record": None, "domain": domain, "dkim_content": clean_dkim_content,
             "selector": None, "signing_domain": None, "algorithm": None, "verified": None, "error": None}

    if not dkim_content:
        return check

    check["selector"] = get_dkim_tag(clean_dkim_content, "s")
    check["signing_domain"] = get_dkim_tag(clean_dkim_content, "d")
    check["algorithm"] = get_dkim_tag(clean_dkim_content, "a")

    if not check["selector"]:
        check["status"] = "broken"
        return check

    signing_domain = check["signing_domain"] or domain

    record, lookup_error = dkim_key_lookup(f"{check['selector']}._domainkey.{signing_domain}")

    if lookup_error:
        check["status"] = "unknown"
        check["error"] = lookup_error
        return check

    if not record:
        check["status"] = "no_key"
        return check

    check["record"] = record.decode("utf-8", errors="replace")
    check["status"] = "ok"

    try:
        check["verified"] = bool(dkim.verify(raw_bytes, dnsfunc=dkim_dns_lookup, minkey=DKIM_MIN_KEY_BITS))
    except Exception as e:
        check["verified"] = False
        check["error"] = repr(e)

    return check


def _txt_to_str(rdata) -> str:
    try:
        return b"".join(rdata.strings).decode("utf-8", errors="replace")
    except Exception:
        return str(rdata).strip('"')

def organizational_domain(domain: str) -> str:
    labels = (domain or "").strip(".").lower().split(".")
    return ".".join(labels[-2:]) if len(labels) >= 2 else (domain or "").lower()


def domains_aligned(from_domain: str, auth_domain: str, mode: str) -> bool:
    if not from_domain or not auth_domain:
        return False

    a = from_domain.strip(".").lower()
    b = auth_domain.strip(".").lower()

    if a == b:
        return True
    if (mode or "r").lower() == "s":
        return False

    return organizational_domain(a) == organizational_domain(b)


def parse_dmarc_tags(record: str) -> dict:
    tags = {}

    for part in (record or "").split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        tags[name.strip().lower()] = value.strip()

    return tags


def check_dmarc_record(domain: str):
    candidate = (domain or "").strip(".")
    failed = False

    while candidate.count(".") >= 1:
        try:
            answers = get_resolver().resolve(f"_dmarc.{candidate}", "TXT")
            record = next((t for t in [_txt_to_str(r) for r in answers] if t.lower().startswith("v=dmarc1")), None)
            if record:
                return True, record, candidate
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            pass
        except Exception:
            failed = True

        if candidate.count(".") == 1:
            break
        candidate = candidate.split(".", 1)[1]

    return (None if failed else False), None, None


def check_dmarc(domain: str, spf_check: dict, dkim_check: dict) -> dict:
    found, record, record_domain = check_dmarc_record(domain)
    tags = parse_dmarc_tags(record)

    check = {"status": "ok" if found else "unknown" if found is None else "missing", "record": record, "domain": domain,
             "record_domain": record_domain, "policy": tags.get("p"), "subdomain_policy": tags.get("sp"),
             "percent": tags.get("pct"), "reports_to": tags.get("rua"),
             "adkim": (tags.get("adkim") or "r").lower(), "aspf": (tags.get("aspf") or "r").lower(),
             "spf_aligned": False, "dkim_aligned": False, "result": "fail"}

    spf_passed = (spf_check or {}).get("result") == "pass"
    dkim_passed = (dkim_check or {}).get("verified") is True

    if spf_passed:
        spf_domain = _sender_domain_of((spf_check or {}).get("checked_sender")) or domain
        check["spf_aligned"] = domains_aligned(domain, spf_domain, check["aspf"])

    if dkim_passed:
        check["dkim_aligned"] = domains_aligned(domain, (dkim_check or {}).get("signing_domain"), check["adkim"])

    evaluated = (spf_check or {}).get("result") is not None or (dkim_check or {}).get("verified") is not None

    if found is None:
        check["result"] = "unknown"
    elif not found:
        check["result"] = "none"
    elif check["spf_aligned"] or check["dkim_aligned"]:
        check["result"] = "pass"
    elif not evaluated:
        check["result"] = "unknown"

    return check


def _sender_domain_of(address: str):
    if not address or "@" not in address:
        return None
    return address.rsplit("@", 1)[-1].strip().lower()


def check_rdns(ip: str) -> dict:
    reversed_ip = ".".join(ip.split(".")[::-1]) + ".in-addr.arpa"

    try:
        answer = get_resolver().resolve(reversed_ip, "PTR")
        hostname = str(answer[0]).rstrip(".")
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return {"success": False, "hostname": None, "forward_ips": [], "matches": False, "error": None}
    except Exception as e:
        return {"success": None, "hostname": None, "forward_ips": [], "matches": None, "error": type(e).__name__}

    try:
        forward_ips = [str(r.address) for r in get_resolver().resolve(hostname, "A")]
        matches = ip in forward_ips
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        forward_ips, matches = [], False
    except Exception:
        forward_ips, matches = [], None

    return {"success": True, "hostname": hostname, "forward_ips": forward_ips, "matches": matches, "error": None}

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

def _domain_dnsbl_status(addresses: list) -> str:
    for address in addresses:
        if address.startswith("127.255.255.") or address == "127.0.0.1":
            return "blocked"
    for address in addresses:
        if address.startswith("127.0.1.") or address in ("127.0.0.2", "127.0.0.4", "127.0.0.8", "127.0.0.14"):
            return "listed"
    return "reputation"


DOMAIN_DNSBL_LISTS = [
    "dbl.spamhaus.org",
    "multi.uribl.com",
    "multi.surbl.org",
    "black.uribl.com",
]


def check_domain_blacklists(domains: list) -> dict:
    targets = [d for d in dict.fromkeys(domains or []) if d][:URIBL_MAX_DOMAINS]
    summary = dict(EMPTY_SUMMARY)

    if not targets:
        return {"checked": 0, "results": {}, "listed": [], "summary": summary}

    resolver = get_resolver(timeout=DNSBL_TIMEOUT, lifetime=DNSBL_LIFETIME)

    def query_one(domain: str, dnsbl: str):
        try:
            answer = resolver.resolve(f"{domain}.{dnsbl}", "A")
            return domain, dnsbl, _domain_dnsbl_status([str(r.address) for r in answer])
        except dns.resolver.NXDOMAIN:
            return domain, dnsbl, "not_listed"
        except (dns.resolver.Timeout, dns.exception.Timeout):
            return domain, dnsbl, "timeout"
        except Exception:
            return domain, dnsbl, "error"

    results = {}
    listed = []
    jobs = [(domain, dnsbl) for domain in targets for dnsbl in DOMAIN_DNSBL_LISTS]

    with ThreadPoolExecutor(max_workers=max(1, DNSBL_CONCURRENCY)) as ex:
        futures = [ex.submit(query_one, domain, dnsbl) for domain, dnsbl in jobs]
        for fut in as_completed(futures):
            domain, dnsbl, status = fut.result()
            results.setdefault(domain, {})[dnsbl] = status
            summary[status] += 1
            if status == "listed":
                listed.append({"domain": domain, "list": dnsbl})

    return {"checked": len(jobs), "results": results, "listed": listed, "summary": summary}


DNSBL_LISTED_CODES = {
    "zen.spamhaus.org": ("127.0.0.2", "127.0.0.3", "127.0.0.4", "127.0.0.5", "127.0.0.6",
                         "127.0.0.7", "127.0.0.9", "127.0.0.10", "127.0.0.11"),
    "cbl.abuseat.org": ("127.0.0.2", "127.0.0.4"),
}

DEFAULT_LISTED_CODES = ("127.0.0.2",)

EMPTY_SUMMARY = {"listed": 0, "not_listed": 0, "reputation": 0, "timeout": 0, "blocked": 0, "error": 0}


def _ip_dnsbl_status(dnsbl: str, addresses: list) -> str:
    for address in addresses:
        if address.startswith("127.255.255."):
            return "blocked"

    listed_codes = DNSBL_LISTED_CODES.get(dnsbl, DEFAULT_LISTED_CODES)

    for address in addresses:
        if address in listed_codes:
            return "listed"

    return "reputation"


def check_blacklists(ip: str) -> dict:
    if not ip:
        return {"checked": 0, "results": {}, "summary": dict(EMPTY_SUMMARY)}

    reversed_ip = ".".join(ip.split(".")[::-1])

    resolver = get_resolver(timeout=DNSBL_TIMEOUT, lifetime=DNSBL_LIFETIME)

    dnsbls = DNSBL_LISTS[:DNSBL_MAX_LISTS]
    results = {}
    answers = {}

    def query_one(dnsbl: str):
        q = f"{reversed_ip}.{dnsbl}"
        try:
            answer = resolver.resolve(q, "A")
            addresses = [str(rdata.address) for rdata in answer]
            return dnsbl, _ip_dnsbl_status(dnsbl, addresses), addresses
        except dns.resolver.NXDOMAIN:
            return dnsbl, "not_listed", []
        except (dns.resolver.Timeout, dns.exception.Timeout):
            return dnsbl, "timeout", []
        except Exception:
            return dnsbl, "error", []

    with ThreadPoolExecutor(max_workers=max(1, DNSBL_CONCURRENCY)) as ex:
        futures = [ex.submit(query_one, dnsbl) for dnsbl in dnsbls]
        for fut in as_completed(futures):
            dnsbl, status, addresses = fut.result()
            results[dnsbl] = status
            if addresses:
                answers[dnsbl] = addresses

    summary = dict(EMPTY_SUMMARY)
    for st in results.values():
        summary[st] += 1

    return {"checked": len(results), "results": results, "answers": answers, "summary": summary}

def get_mx_record(domain: str):
    try:
        mx_records = []
        answers = get_resolver().resolve(domain, "MX")
        for r in answers:
            host = str(r.exchange).rstrip(".")
            mx_records.append(host)
        return mx_records
    except Exception:
        return False


def check_a_record(domain: str) -> bool:
    try:
        records = get_resolver().resolve(domain, "A")
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

    for header in reversed(received_headers):
        ips = re.findall(r'\[(\d{1,3}(?:\.\d{1,3}){3})\]', header)

        for ip in ips:
            if is_public_ip(ip):
                return ip

    return None