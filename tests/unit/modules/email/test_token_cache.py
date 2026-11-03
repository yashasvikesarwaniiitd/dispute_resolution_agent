"""Card P3-02: the token cache is a secret, and is treated like one (ADR 0023, I12).

The cache holds a refresh token. By default that is good for ninety days, which makes the
file on disk the most valuable thing this project stores — more valuable than any single
message, because it is the ability to send any number of them as the owner.

So the tests here are about refusals rather than features:

- **no plain-text fallback.** `msal-extensions` offers one when the platform keystore is
  unavailable, and taking it would turn a ninety-day credential into a readable file. The
  adapter raises instead, and "it still worked on that machine" is not a good enough reason
  to change that.
- **a failed refresh is not retried.** It is an `AuthPortError`, so the case reaches a
  person who can sign in rather than spinning against a revoked token.
- **nothing leaks.** MSAL puts the token in the same dictionary as the error fields, so an
  error message built by formatting that dictionary would publish it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from dispute_agent.kernel.domain.errors import AuthPortError, ConfigError
from dispute_agent.modules.email.outlook_graph.auth import REAUTH_REQUIRED, SCOPES, GraphAuth

if TYPE_CHECKING:
    from pathlib import Path

TOKEN = "an-access-token-nobody-should-ever-see"
REFRESH = "a-refresh-token-worth-ninety-days"


class FakeApp:
    """Stands in for `msal.PublicClientApplication`."""

    def __init__(
        self,
        accounts: list[str] | None = None,
        silent: dict[str, Any] | None = None,
        flow: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        self._accounts = accounts if accounts is not None else ["the-pilot-account"]
        self._silent = silent
        self._flow = flow or {"user_code": "ABCD-EFGH", "message": "Go to ... and enter ABCD-EFGH"}
        self._result = result or {"access_token": TOKEN, "refresh_token": REFRESH}
        self.asked_for: list[Any] = []

    def get_accounts(self) -> list[str]:
        return self._accounts

    def acquire_token_silent(self, scopes: list[str], account: object) -> dict[str, Any] | None:
        self.asked_for.append(scopes)
        return self._silent

    def initiate_device_flow(self, scopes: list[str]) -> dict[str, Any]:
        self.asked_for.append(scopes)
        return self._flow

    def acquire_token_by_device_flow(self, flow: dict[str, Any]) -> dict[str, Any]:
        return self._result


def auth(app: FakeApp) -> GraphAuth:
    return GraphAuth(client_id="client-id", app_factory=lambda: app)


class TestGettingAToken:
    def test_a_cached_account_refreshes_silently(self) -> None:
        app = FakeApp(silent={"access_token": TOKEN})
        assert auth(app).token() == TOKEN

    def test_it_asks_only_for_the_scopes_it_needs(self) -> None:
        """Least privilege, asserted rather than trusted to the setup guide. Delegated
        access cannot be narrowed to part of a mailbox, so the scope list is the whole of
        the blast radius."""
        app = FakeApp(silent={"access_token": TOKEN})
        auth(app).token()
        assert app.asked_for == [list(SCOPES)]

    def test_the_scope_list_is_exactly_the_three_documented_ones(self) -> None:
        assert set(SCOPES) == {"Mail.ReadWrite", "Mail.Send", "Mail.Read"}

    def test_offline_access_is_not_requested_explicitly(self) -> None:
        """MSAL adds it, and asking for it directly is refused as a reserved scope."""
        assert "offline_access" not in SCOPES


class TestARefreshFailureStops:
    def test_no_signed_in_account_is_an_auth_error(self) -> None:
        with pytest.raises(AuthPortError, match=REAUTH_REQUIRED):
            auth(FakeApp(accounts=[])).token()

    def test_a_silent_refresh_that_fails_is_an_auth_error(self) -> None:
        """Not transient. Retrying a revoked token gets the same answer every time while
        the case sits there waiting."""
        with pytest.raises(AuthPortError, match=REAUTH_REQUIRED):
            auth(FakeApp(silent=None)).token()

    def test_a_result_with_no_token_is_an_auth_error(self) -> None:
        with pytest.raises(AuthPortError):
            auth(FakeApp(silent={"error": "invalid_grant"})).token()

    def test_the_error_tells_the_owner_what_to_do(self) -> None:
        """A failure a person cannot act on is barely a failure report."""
        with pytest.raises(AuthPortError) as failed:
            auth(FakeApp(accounts=[])).token()
        assert "email-login" in str(failed.value)


class TestSigningIn:
    def test_the_device_code_message_is_shown_to_the_owner(self) -> None:
        shown: list[str] = []
        auth(FakeApp()).device_login(prompt=shown.append)
        assert shown and "ABCD-EFGH" in shown[0]

    def test_a_flow_that_will_not_start_names_the_likely_cause(self) -> None:
        """ "Allow public client flows" being off is the usual reason, and the error saying
        so saves an hour in the Entra portal."""
        app = FakeApp(flow={"error": "invalid_client"})
        with pytest.raises(AuthPortError, match="public client"):
            auth(app).device_login(prompt=lambda _: None)

    def test_signing_in_without_a_refresh_token_is_refused(self) -> None:
        """Otherwise the next unattended send fails hours later with nobody watching."""
        app = FakeApp(accounts=[], result={"access_token": TOKEN})
        with pytest.raises(AuthPortError, match="refresh token"):
            app_auth = auth(app)
            app_auth.device_login(prompt=lambda _: None)


class TestNothingLeaks:
    """I12, at the two places a token could escape."""

    def test_a_failed_sign_in_quotes_only_the_two_error_fields(self) -> None:
        """MSAL returns one dictionary for both outcomes, so an error built by formatting
        the whole of it would publish whatever else is in there. The formatter reads
        `error` and `error_description` by name and nothing else - this puts a secret in a
        third field and checks it stays put."""
        app = FakeApp(
            result={
                "error": "expired_token",
                "error_description": "the code expired",
                "refresh_token": REFRESH,
            }
        )
        with pytest.raises(AuthPortError) as failed:
            auth(app).device_login(prompt=lambda _: None)
        message = str(failed.value)
        assert REFRESH not in message
        assert "expired_token" in message

    def test_a_multiline_description_is_trimmed_to_one_line(self) -> None:
        """Entra error descriptions carry correlation ids and trace text over several
        lines. A log line that swallows all of it is a log line nobody reads."""
        app = FakeApp(
            result={
                "error": "invalid_grant",
                "error_description": "first line\nsecond line " + REFRESH,
            }
        )
        with pytest.raises(AuthPortError) as failed:
            auth(app).device_login(prompt=lambda _: None)
        assert REFRESH not in str(failed.value)

    def test_a_refresh_failure_does_not_quote_the_cache(self) -> None:
        app = FakeApp(silent={"error": "invalid_grant", "refresh_token": REFRESH})
        with pytest.raises(AuthPortError) as failed:
            auth(app).token()
        assert REFRESH not in str(failed.value)

    def test_the_repr_carries_no_token(self) -> None:
        app = FakeApp(silent={"access_token": TOKEN})
        holder = auth(app)
        holder.token()
        assert TOKEN not in repr(holder)


class TestTheCacheIsEncryptedOrAbsent:
    def test_no_plain_text_fallback_is_taken(self, tmp_path: Path, monkeypatch: Any) -> None:
        """The decision that matters most in this file. A refresh token in a readable file
        is a credential anyone with the disk can use for ninety days."""
        import dispute_agent.modules.email.outlook_graph.auth as module

        def no_keystore(path: str) -> Any:
            raise OSError("no keystore on this machine")

        monkeypatch.setattr(
            "msal_extensions.build_encrypted_persistence", no_keystore, raising=False
        )
        holder = module.GraphAuth(client_id="client-id", cache_path=str(tmp_path / "cache.bin"))
        with pytest.raises(ConfigError, match="plain-text"):
            holder.token()

    def test_the_error_says_why_rather_than_just_failing(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        import dispute_agent.modules.email.outlook_graph.auth as module

        monkeypatch.setattr(
            "msal_extensions.build_encrypted_persistence",
            lambda path: (_ for _ in ()).throw(OSError("nope")),
            raising=False,
        )
        holder = module.GraphAuth(client_id="c", cache_path=str(tmp_path / "c.bin"))
        with pytest.raises(ConfigError) as refused:
            holder.token()
        assert "refresh token" in str(refused.value)

    def test_a_missing_client_id_fails_at_construction(self) -> None:
        """Fail fast on configuration rather than at the first send."""
        with pytest.raises(ConfigError, match="GRAPH_CLIENT_ID"):
            GraphAuth(client_id="")


class TestHealth:
    def test_signed_in_is_false_rather_than_raising(self) -> None:
        """A health check that throws is not a health check."""
        assert GraphAuth(client_id="c", app_factory=lambda: FakeApp(accounts=[])).signed_in() is (
            False
        )

    def test_signed_in_is_true_with_an_account(self) -> None:
        assert GraphAuth(client_id="c", app_factory=lambda: FakeApp()).signed_in() is True
