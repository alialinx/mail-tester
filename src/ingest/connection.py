import re


HELO_PATTERN = re.compile(r"^from\s+(\S+)", re.IGNORECASE)
CLIENT_IP_PATTERN = re.compile(r"\[(\d{1,3}(?:\.\d{1,3}){3})\]")
TLS_PATTERN = re.compile(r"using\s+(\S+)\s+with\s+cipher\s+(\S+)", re.IGNORECASE)
PROTOCOL_PATTERN = re.compile(r"with\s+(E?SMTPS?A?)\s", re.IGNORECASE)


def get_connection_info(msg) -> dict:
    received = msg.get_all("Received") or []
    top = " ".join(str(received[0]).split()) if received else ""

    info = {
        "client_ip": None,
        "helo": None,
        "protocol": None,
        "tls": False,
        "tls_protocol": None,
        "tls_cipher": None,
        "source": "received_header" if top else "missing",
    }

    if not top:
        return info

    helo = HELO_PATTERN.search(top)
    if helo:
        info["helo"] = helo.group(1)

    client_ip = CLIENT_IP_PATTERN.search(top)
    if client_ip:
        info["client_ip"] = client_ip.group(1)

    protocol = PROTOCOL_PATTERN.search(top)
    if protocol:
        info["protocol"] = protocol.group(1).upper()
        info["tls"] = info["protocol"].endswith("S") or info["protocol"].endswith("SA")

    tls = TLS_PATTERN.search(top)
    if tls:
        info["tls"] = True
        info["tls_protocol"] = tls.group(1)
        info["tls_cipher"] = tls.group(2)

    return info
