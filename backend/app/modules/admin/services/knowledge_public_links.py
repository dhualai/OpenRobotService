import ipaddress
import re
from html import unescape
from urllib.parse import parse_qsl, unquote, urlsplit

from app.core.config import settings


LINKS = re.compile(
    r'!?\[(?P<label>[^\[\]\n]*)\]\(\s*(?P<destination><[^<>\n]*>|(?:[^()\s]|\([^()\s]*\))+)(?:\s+["\'][^"\']*["\'])?\s*\)'
    r'|(?P<url>https?://[^\s<>"\'，。；！？、（）]+)',
    re.IGNORECASE,
)
SENSITIVE_PARAMETERS = {
    "token", "accesstoken", "refreshtoken", "idtoken", "apikey", "password", "passwd",
    "secret", "authorization", "auth", "session", "sessionid", "openid", "unionid",
    "phone", "mobile", "email", "xamzcredential", "xamzsignature", "xamzsecuritytoken",
    "securitytoken", "signature",
}
CLOSING_DELIMITERS = {")": "(", "]": "[", "}": "{"}


def is_public_reference(value):
    try:
        value = unescape(value)
        if re.search(r"[\s\\]", value):
            return False
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme.lower() not in {"http", "https"} or not host or parsed.username is not None or parsed.password is not None:
            return False
        if parsed.port is not None and not 0 < parsed.port <= 65535:
            return False
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if "." not in host or host.endswith((".localhost", ".local", ".internal", ".lan", ".corp", ".home.arpa")):
                return False
            labels = host.encode("idna").decode("ascii").split(".")
            if all(label.isdigit() for label in labels) or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels):
                return False
        else:
            if not address.is_global:
                return False
        frontend_host = urlsplit(settings.FRONTEND_BASE_URL).hostname
        tracker_host = urlsplit(settings.ZENTAO_BASE_URL).hostname
        if tracker_host and host == tracker_host.lower().rstrip("."):
            return False
        if frontend_host and host == frontend_host.lower().rstrip("."):
            for path in (unquote(parsed.path), unquote(parsed.fragment).lstrip("#")):
                if re.match(r"^/(?:(?:t|p)/)?(?:app/)?(?:tasks|admin|api)(?:/|$)", path, re.IGNORECASE):
                    return False
        queries = (parsed.query, parsed.fragment, parsed.fragment.partition("?")[2])
        for query in queries:
            if any(re.sub(r"[^a-z0-9]", "", key.lower()) in SENSITIVE_PARAMETERS for key, _ in parse_qsl(query)):
                return False
        return True
    except (ValueError, UnicodeError):
        return False


def redact_with_public_links(text, redact):
    parts = []
    previous = 0
    for match in LINKS.finditer(text):
        parts.append(redact(text[previous:match.start()]))
        destination = match.group("destination")
        value = destination.strip("<>") if destination is not None else match.group("url")
        suffix = ""
        if destination is None:
            while value and (value[-1] in ".,;:!?" or (
                value[-1] in CLOSING_DELIMITERS and value.count(value[-1]) > value.count(CLOSING_DELIMITERS[value[-1]])
            )):
                suffix = value[-1] + suffix
                value = value[:-1]
        decoded = unquote(unescape(value))
        allowed = is_public_reference(value) and redact(decoded) == decoded
        if destination is not None:
            label = redact(match.group("label"))
            parts.append(f"[{label}]({value})" if allowed else f"{label}[链接已隐藏]")
        else:
            parts.append(value if allowed else "[链接已隐藏]")
        parts.append(suffix)
        previous = match.end()
    parts.append(redact(text[previous:]))
    return "".join(parts)
