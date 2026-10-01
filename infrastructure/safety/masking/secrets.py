"""Pure obvious-secret scrubbing for text released to a model or chat."""

import re

_SECRET_SUBS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"-----BEGIN[ A-Z]*PRIVATE KEY-----.*?-----END[ A-Z]*PRIVATE KEY-----", re.DOTALL
        ),
        "[REDACTED PRIVATE KEY]",
    ),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"), "[REDACTED]"),
    (re.compile(r"\bxapp-[A-Za-z0-9-]{10,}"), "[REDACTED]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED]"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{16,}=*"), "Bearer [REDACTED]"),
    (
        re.compile(r"(?i)\b(api[_-]?key|secret|password|passwd|token)(\s*[=:]\s*)\S{6,}"),
        r"\1\2[REDACTED]",
    ),
)


def scrub_secrets(text: str) -> str:
    """Redact obvious credentials from text before it reaches the model."""
    for pattern, replacement in _SECRET_SUBS:
        text = pattern.sub(replacement, text)
    return text
