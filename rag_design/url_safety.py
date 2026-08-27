"""Validation helpers for public citations.

API request URLs are never citation URLs. Secret-like query parameters are removed
before a URL can cross the RAG/UI contract boundary.
"""

from __future__ import annotations

from typing import Iterable
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit


OFFICIAL_DOMAINS = ("data.go.kr", "gov.kr", "law.go.kr")
SECRET_QUERY_NAMES = frozenset(
    {
        "apikey",
        "api_key",
        "auth",
        "authorization",
        "authkey",
        "key",
        "oc",  # 국가법령정보센터 Open API 사용자 식별자
        "servicekey",
        "token",
    }
)
SAFE_PUBLIC_QUERY_NAMES = frozenset({"efyd", "lsiseq"})


def _decode_repeated(value: str) -> str:
    decoded = value
    while True:
        next_value = unquote(decoded)
        if next_value == decoded:
            return decoded
        decoded = next_value


def _normalized_query_name(value: str) -> str:
    return _decode_repeated(value).casefold()


def is_official_hostname(hostname: str | None) -> bool:
    """Return whether a hostname is an approved official domain or subdomain."""

    if not hostname:
        return False
    normalized = hostname.rstrip(".").lower()
    return any(
        normalized == domain or normalized.endswith(f".{domain}")
        for domain in OFFICIAL_DOMAINS
    )


def sanitize_official_url(url: str) -> str:
    """Return a HTTPS official URL with credentials and secret query keys removed."""

    if not isinstance(url, str) or not url.strip():
        raise ValueError("public URL must be a non-empty string")
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in {"http", "https"}:
        raise ValueError("public URL must use HTTP(S)")
    if parts.username is not None or parts.password is not None:
        raise ValueError("public URL must not contain user information")
    if not is_official_hostname(parts.hostname):
        raise ValueError("public URL must use an approved official domain")

    safe_query = []
    for name, value in parse_qsl(parts.query, keep_blank_values=True):
        normalized_name = _normalized_query_name(name)
        if normalized_name in SECRET_QUERY_NAMES:
            continue
        # A public citation needs only stable law lookup identifiers. Dropping all
        # other query fields prevents a secret hidden behind an unexpected name.
        if normalized_name in SAFE_PUBLIC_QUERY_NAMES:
            safe_query.append((name, value))
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("public URL has an invalid port") from exc
    if port not in (None, 443):
        raise ValueError("public URL must not use a non-standard port")

    hostname = (parts.hostname or "").lower()
    netloc = hostname
    return urlunsplit(
        ("https", netloc, parts.path or "/", urlencode(safe_query, doseq=True), "")
    )


def contains_secret_query_name(url: str) -> bool:
    """Fail closed for malformed URLs or secret-like query parameter names."""

    try:
        pairs = parse_qsl(urlsplit(url).query, keep_blank_values=True)
    except (TypeError, ValueError):
        return True
    return any(_normalized_query_name(name) in SECRET_QUERY_NAMES for name, _ in pairs)


def contains_secret_value(url: str, secret_values: Iterable[str]) -> bool:
    """Detect raw, once-encoded, or repeatedly encoded known secret values."""

    decoded_url = _decode_repeated(url)
    for value in secret_values:
        if not value:
            continue
        decoded_secret = _decode_repeated(str(value))
        if value in url or decoded_secret in decoded_url:
            return True
    return False
