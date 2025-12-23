import os
import re
import socket

from src.config import SPAMD_TIMEOUT, SPAMD_PORT, SPAMD_HOST


def spamd_check(raw_email: bytes, host: str = None, port: int = None, timeout: float = None) -> dict:
    host = host or SPAMD_HOST
    port = int(port or SPAMD_PORT)
    timeout = float(timeout or SPAMD_TIMEOUT)

    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.settimeout(timeout)

        req = (
            b"CHECK SPAMC/1.5\r\n"
            b"Content-length: " + str(len(raw_email)).encode() + b"\r\n"
            b"\r\n" + raw_email
        )
        s.sendall(req)

        data = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
        s.close()

        report = data.decode(errors="ignore")
        parsed = _parse_report(report)

        return {"status": "ok","is_spam": parsed.get("is_spam"),"score": parsed.get("score"),"threshold": parsed.get("threshold"),"rules": parsed.get("rules", []),"report": report,"error": None,}


    except Exception as e:
        return {"status": "error","is_spam": None,"score": None,"threshold": None,"rules": [],"report": "","error": repr(e),}

def _parse_report(report: str) -> dict:
    out = {"is_spam": None, "score": None, "threshold": None, "rules": []}
    if not report:
        return out

    m = re.search(r"Spam:\s*(True|False)\s*;\s*([-\d.]+)\s*/\s*([-\d.]+)", report)
    if m:
        out["is_spam"] = (m.group(1) == "True")
        out["score"] = float(m.group(2))
        out["threshold"] = float(m.group(3))

    lines = report.splitlines()
    for line in lines:
        line = line.strip()
        if not line or line.startswith("pts ") or line.startswith("----"):
            continue
        rm = re.match(r"^([-\d.]+)\s+([A-Z0-9_]+)\s+(.*)$", line)
        if rm:
            out["rules"].append({
                "points": float(rm.group(1)),
                "name": rm.group(2),
                "description": rm.group(3).strip()
            })

    return out
