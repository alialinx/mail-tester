import re
from html.parser import HTMLParser
from urllib.parse import urlparse

SHORTENER_HOSTS = {
    "bit.ly", "t.co", "tinyurl.com", "goo.gl", "ow.ly", "is.gd", "buff.ly",
    "rebrand.ly", "cutt.ly", "shorturl.at", "rb.gy", "t.ly", "s.id", "lnkd.in",
}

UNSUBSCRIBE_WORDS = ("unsubscribe", "abonelikten", "listeden çık", "listeden cik", "opt out", "opt-out")

URL_PATTERN = re.compile(r"https?://[^\s<>\"')]+", re.IGNORECASE)


class BodyParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text_parts = []
        self.links = []
        self.images = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)

        if tag in ("script", "style"):
            self.skip += 1
        elif tag == "a" and attributes.get("href"):
            self.links.append(attributes["href"].strip())
        elif tag == "img":
            self.images.append({"src": (attributes.get("src") or "").strip(), "alt": attributes.get("alt")})

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.text_parts.append(data)

    def text(self):
        return re.sub(r"\s+", " ", "".join(self.text_parts)).strip()


def decode_part(part) -> str:
    try:
        payload = part.get_payload(decode=True) or b""
        return payload.decode(part.get_content_charset() or "utf-8", errors="ignore")
    except Exception:
        return ""


def extract_bodies(msg):
    plain = ""
    html = ""
    attachments = []

    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_maintype() == "multipart":
            continue

        disposition = str(part.get("Content-Disposition") or "")
        content_type = part.get_content_type()

        if "attachment" in disposition:
            attachments.append({"name": part.get_filename(), "type": content_type,
                                "size": len(part.get_payload(decode=True) or b"")})
            continue

        if content_type == "text/plain" and not plain:
            plain = decode_part(part)
        elif content_type == "text/html" and not html:
            html = decode_part(part)

    return plain, html, attachments


def bare_host(host: str) -> str:
    return host[4:] if host.startswith("www.") else host


def link_hosts(urls: list) -> list:
    hosts = []

    for url in urls:
        try:
            host = (urlparse(url).hostname or "").lower().strip(".")
        except Exception:
            host = ""
        if host and host not in hosts:
            hosts.append(host)

    return hosts


def inspect_content(msg, subject: str = None) -> dict:
    plain, html, attachments = extract_bodies(msg)

    parser = BodyParser()
    if html:
        try:
            parser.feed(html)
        except Exception:
            pass

    html_text = parser.text()
    visible_text = plain.strip() or html_text
    urls = URL_PATTERN.findall(plain) + [u for u in parser.links if u.lower().startswith("http")]
    hosts = link_hosts(urls)

    text_length = len(visible_text)
    html_length = len(html)
    images_without_alt = [i for i in parser.images if not (i.get("alt") or "").strip()]
    shorteners = [h for h in hosts if bare_host(h) in SHORTENER_HOSTS]

    body_lower = (plain + " " + html_text).lower()
    subject_value = (subject or "").strip()
    letters = [c for c in subject_value if c.isalpha()]

    return {
        "preview": visible_text[:2000],
        "has_plain": bool(plain.strip()),
        "has_html": bool(html.strip()),
        "text_length": text_length,
        "html_length": html_length,
        "text_ratio": round(text_length / html_length, 3) if html_length else None,
        "link_count": len(urls),
        "link_hosts": hosts,
        "shortened_links": shorteners,
        "image_count": len(parser.images),
        "images_without_alt": len(images_without_alt),
        "attachment_count": len(attachments),
        "attachments": attachments,
        "has_unsubscribe_link": any(word in body_lower for word in UNSUBSCRIBE_WORDS),
        "subject_length": len(subject_value),
        "subject_all_caps": bool(letters) and all(c.isupper() for c in letters),
        "subject_exclamations": subject_value.count("!"),
    }
