"""The Feishu verifier: a live tenant-token probe over chat-app credentials."""

from __future__ import annotations

from integrations.feishu.verifier import verify_feishu


def test_missing_credentials_report_missing_without_a_network_call(monkeypatch) -> None:
    """An unconfigured record is 'missing', not a transport failure."""

    def _explode(**_kwargs):
        raise AssertionError("the probe must not run without credentials")

    monkeypatch.setattr("lark_oapi.Client.builder", _explode)

    result = verify_feishu("local store", {"app_id": "", "app_secret": ""})

    assert result == {
        "service": "feishu",
        "source": "local store",
        "status": "missing",
        "detail": "Missing app_id or app_secret.",
    }


def test_a_good_token_reports_passed(monkeypatch) -> None:
    def _noop(_config):
        return "t-123"

    monkeypatch.setattr("lark_oapi.core.token.TokenManager.get_self_tenant_token", _noop)

    result = verify_feishu("local store", {"app_id": "cli_1", "app_secret": "s"})

    assert result["status"] == "passed"
    assert result["service"] == "feishu"
    assert "o-…" not in result["detail"]


def test_bad_credentials_report_failed_and_redact_the_secret(monkeypatch) -> None:
    """The SDK embeds the app secret in its auth error; it must not reach verify output."""

    def _reject(_config):
        raise RuntimeError("obtain access token failed for app_secret=super-secret-value")

    monkeypatch.setattr("lark_oapi.core.token.TokenManager.get_self_tenant_token", _reject)

    result = verify_feishu("local store", {"app_id": "cli_1", "app_secret": "super-secret-value"})

    assert result["status"] == "failed"
    assert "super-secret-value" not in result["detail"]
