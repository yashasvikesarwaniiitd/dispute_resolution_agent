"""Signing in to one personal mailbox, and keeping the token safe (ADR 0023, I12).

A public client with no client secret, signed in once by device code, refreshing silently
afterwards. There is nothing here that could leak a long-lived credential from the
repository, because there is no credential in the repository: the only secret is the token
cache on disk, and it is encrypted at rest.

Three decisions are worth knowing.

**Encrypted or nothing.** `msal-extensions` can fall back to a plain-text cache when the
platform's keystore is unavailable. That fallback is never enabled here. A refresh token in
a readable file is a credential anyone with the disk can use for ninety days, and "it still
worked on that machine" is not worth it.

**A failed refresh is an auth error, not a transient one.** When `acquire_token_silent`
returns None the token is gone — revoked, expired, or the password changed — and retrying
produces the same answer every time while the case sits there. The adapter raises
`AuthPortError`, the case reaches a person, and the owner runs `make email-login`.

**Nothing here returns a token to a caller that logs.** `token()` hands back the string, and
every caller puts it straight into a header. It is never formatted into an error, a span or
a log line; the settings redaction processor is the backstop, not the plan.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dispute_agent.kernel.domain.errors import AuthPortError, ConfigError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

log = logging.getLogger(__name__)

#: Delegated, least privilege, and nothing more (ADR 0023). `offline_access` is added by
#: MSAL itself, so asking for it here would be refused as a reserved scope.
SCOPES: tuple[str, ...] = (
    "Mail.ReadWrite",  # create the draft and set the idempotency marker
    "Mail.Send",  # send it
    "Mail.Read",  # read it back from Sent Items, and poll for replies
)

#: What the owner is told to do when the refresh token is gone. The code travels in the
#: error so `/healthz` and the case's review reason can both show the same word.
REAUTH_REQUIRED = "reauth_required"


@dataclass
class GraphAuth:
    """Holds the MSAL app and the encrypted cache. One mailbox, one account."""

    client_id: str
    authority: str = "https://login.microsoftonline.com/consumers/"
    cache_path: str = "data/msal_cache.bin"
    scopes: Sequence[str] = SCOPES
    #: Injected so tests do not need MSAL. Production leaves it None.
    app_factory: Callable[[], Any] | None = None

    _app: Any = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.client_id:
            raise ConfigError("GRAPH_CLIENT_ID is not set; see docs/SETUP_CHANNELS.md section 1")

    # -- the token ---------------------------------------------------------------------------

    def token(self) -> str:
        """A valid access token, refreshed silently. Raises `AuthPortError` when it cannot.

        Never logs the value, and never includes it in the error it raises.
        """
        app = self._application()
        accounts = app.get_accounts()
        if not accounts:
            raise AuthPortError(
                f"{REAUTH_REQUIRED}: no account is signed in. Run `make email-login`."
            )

        result = app.acquire_token_silent(list(self.scopes), account=accounts[0])
        if not result or "access_token" not in result:
            # Deliberately not a TransientPortError. Retrying a revoked token gets the same
            # answer every time, and the case waits instead of reaching someone who can fix
            # it (integrations.md section 1.1).
            raise AuthPortError(
                f"{REAUTH_REQUIRED}: the cached token could not be refreshed. "
                "Run `make email-login`."
            )
        return str(result["access_token"])

    def signed_in(self) -> bool:
        """For `/healthz`. Never raises: a health check that throws is not one."""
        try:
            return bool(self._application().get_accounts())
        except (AuthPortError, ConfigError, OSError):
            return False

    # -- signing in ---------------------------------------------------------------------------

    def device_login(self, *, prompt: Callable[[str], None]) -> None:
        """The once-per-90-days interactive step, run by `make email-login`.

        `prompt` is handed the message MSAL produced — it contains a URL and a short code,
        and no secret. Passing it in rather than printing here keeps this importable from a
        test without a console.
        """
        app = self._application()
        flow = app.initiate_device_flow(scopes=list(self.scopes))
        if "user_code" not in flow:
            raise AuthPortError(
                "the device code flow could not be started; check that the app "
                "registration allows public client flows (docs/SETUP_CHANNELS.md 1b.5)"
            )
        prompt(str(flow["message"]))

        result = app.acquire_token_by_device_flow(flow)
        if "access_token" not in result:
            raise AuthPortError(f"sign-in did not complete: {_safe_error(result)}")
        if "refresh_token" not in result and not app.get_accounts():
            # Without a refresh token the next unattended send fails, and it would fail
            # hours later with nobody watching. Better to say so now.
            raise AuthPortError(
                "signed in but no refresh token was cached; unattended sending will not "
                "work. Check that offline_access was consented."
            )

    # -- the application ------------------------------------------------------------------------

    def _application(self) -> Any:
        if self._app is None:
            self._app = (self.app_factory or self._build)()
        return self._app

    def _build(self) -> Any:
        import msal
        from msal_extensions import PersistedTokenCache, build_encrypted_persistence

        path = Path(self.cache_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        try:
            persistence = build_encrypted_persistence(str(path))
        except Exception as unavailable:
            # No plain-text fallback, ever. msal-extensions offers one; a refresh token in
            # a readable file is a credential anyone with the disk can use (I12).
            raise ConfigError(
                "no encrypted token cache is available on this machine, and this project "
                "does not fall back to a plain-text one. The cache holds a refresh token."
            ) from unavailable

        return msal.PublicClientApplication(
            self.client_id,
            authority=self.authority,
            token_cache=PersistedTokenCache(persistence),
        )


def _safe_error(result: dict[str, Any]) -> str:
    """An MSAL failure, without the parts that might carry a token.

    MSAL puts `error` and `error_description` on a failure and the token on success, so
    naming the two fields explicitly is what keeps a stray `access_token` out of a log line
    that gets pasted into an issue.
    """
    code = result.get("error", "unknown_error")
    detail = result.get("error_description", "")
    return f"{code}: {str(detail).splitlines()[0]}" if detail else str(code)


__all__ = ["REAUTH_REQUIRED", "SCOPES", "GraphAuth"]
