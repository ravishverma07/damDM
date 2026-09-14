"""Logging filters that keep secrets out of log output."""

import logging
import re

# Anything that looks like a Meta/Stripe token or an Authorization header.
_PATTERNS = [
    re.compile(r"(access_token=)[^&\s\"']+", re.IGNORECASE),
    re.compile(r"(\"access_token\"\s*:\s*\")[^\"]+", re.IGNORECASE),
    re.compile(r"(client_secret=)[^&\s\"']+", re.IGNORECASE),
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE),
    re.compile(r"\bIG[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]+\b"),
    re.compile(r"\bwhsec_[A-Za-z0-9]+\b"),
]

REDACTION = "[REDACTED]"


def scrub(text: str) -> str:
    """Replace anything token-shaped in ``text`` with a redaction marker."""
    for pattern in _PATTERNS:
        if pattern.groups:
            # The group is a keeper prefix (e.g. "access_token="); the rest goes.
            text = pattern.sub(lambda m: f"{m.group(1)}{REDACTION}", text)
        else:
            # The whole match is the secret.
            text = pattern.sub(REDACTION, text)
    return text


def _scrub_arg(value):
    """Scrub a log argument. Objects (e.g. httpx.URL) are scrubbed via str()."""
    if isinstance(value, str):
        return scrub(value)
    if value is None or isinstance(value, bool | int | float):
        return value
    text = str(value)
    cleaned = scrub(text)
    return cleaned if cleaned != text else value


class SensitiveDataFilter(logging.Filter):
    """Scrub access tokens and API secrets from every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = scrub(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: _scrub_arg(v) for k, v in record.args.items()}
            else:
                record.args = tuple(_scrub_arg(a) for a in record.args)
        return True
