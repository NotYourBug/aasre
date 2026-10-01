"""Characterize obvious-secret replacements before moving the shared scrubber."""

from infrastructure.safety.masking.secrets import scrub_secrets


def test_scrubber_preserves_existing_secret_replacements() -> None:
    cases = (
        ("**status: healthy**", "**status: healthy**"),
        (
            "-----BEGIN PRIVATE KEY-----\nprivate-material\n-----END PRIVATE KEY-----",
            "[REDACTED PRIVATE KEY]",
        ),
        ("xoxb-abcdefghijklmnop", "[REDACTED]"),
        ("xapp-abcdefghijklmnop", "[REDACTED]"),
        ("AKIA1234567890ABCDEF", "[REDACTED]"),
        ("Bearer abcdefghijklmnopqrst", "Bearer [REDACTED]"),
        ("token=abcdefgh", "token=[REDACTED]"),
    )
    for raw, expected in cases:
        assert scrub_secrets(raw) == expected
