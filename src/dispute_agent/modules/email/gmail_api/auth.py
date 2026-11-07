"""Signing in to one personal Gmail mailbox, and keeping the token safe (ADR 0038, I12).

Google's flow for desktop apps (developers.google.com/identity/protocols/oauth2/native-app):
the browser opens Google's consent page, Google redirects to a one-shot listener on
`http://127.0.0.1:<random port>` with a code, and the code is exchanged for tokens with PKCE
(S256). The copy-paste ("out of band") flow is retired, and the device flow does not allow
Gmail scopes, so this is the only route.

The same three decisions as the Outlook adapter (`outlook_graph/auth.py`):

**Encrypted or nothing.** The refresh token is stored with `msal-extensions`' encrypted
persistence (DPAPI on Windows). It does not fall back to plain text.

**A refresh that fails is an auth error, not a transient one.** Google answers `invalid_grant`
when the refresh token is gone - and for an app in "Testing" status it goes after **7 days**
(developers.google.com/identity/protocols/oauth2). Retrying gets the same answer, so the
adapter raises `AuthPortError` with `reauth_required`, the case reaches a person, and the owner
runs `make gmail-login`.

**No token is ever formatted into an error or a log line.** Errors carry Google's error
*code* only.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from dispute_agent.kernel.domain.errors import AuthPortError, ConfigError, TransientPortError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"

#: Least privilege (developers.google.com/gmail/api/auth/scopes): send, and read. Nothing that
#: can modify or delete mail. `gmail.readonly` is a restricted scope, which is fine for the
#: owner's own app in Testing status; a published app would need Google's assessment.
SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
)

REAUTH_REQUIRED = "reauth_required"

#: Refresh a little early, so a token does not expire between the check and the call.
EARLY_S = 60


@dataclass
class GmailAuth:
    """Holds the client, the encrypted refresh token and a short-lived access token."""

    client_id: str
    client_secret: str
    cache_path: str = "data/gmail_token.bin"
    scopes: Sequence[str] = SCOPES
    timeout_s: float = 30.0
    #: Injected for tests: an object with `load() -> str` and `save(str)`.
    store: Any = field(default=None, repr=False)
    client: httpx.Client | None = field(default=None, repr=False)

    _access: str | None = field(default=None, init=False, repr=False)
    _expires_at: float = field(default=0.0, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.client_id or not self.client_secret:
            raise ConfigError(
                "GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET are not set; see docs/SETUP_CHANNELS.md"
            )

    # -- the token ---------------------------------------------------------------------------

    def token(self) -> str:
        """A valid access token, refreshed when needed. Raises `AuthPortError` when it cannot."""
        if self._access and time.time() < self._expires_at - EARLY_S:
            return self._access
        refresh = self._load_refresh()
        if not refresh:
            raise AuthPortError(f"{REAUTH_REQUIRED}: not signed in. Run `make gmail-login`.")
        answer = self._token_request(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh,
            }
        )
        self._remember(answer)
        assert self._access is not None
        return self._access

    def signed_in(self) -> bool:
        """For `/healthz` and the live tests. Never raises."""
        try:
            return bool(self._load_refresh())
        except (AuthPortError, ConfigError, OSError):
            return False

    # -- signing in (make gmail-login) --------------------------------------------------------

    def browser_login(
        self,
        *,
        open_browser: Callable[[str], object],
        login_hint: str | None = None,
        wait_s: float = 300.0,
    ) -> None:
        """The loopback flow with PKCE. `open_browser` gets the consent URL (no secret in it)."""
        verifier = _b64(secrets.token_bytes(48))
        challenge = _b64(hashlib.sha256(verifier.encode("ascii")).digest())
        state = secrets.token_urlsafe(24)
        received: dict[str, str] = {}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                query = parse_qs(urlparse(self.path).query)
                received.update({k: v[0] for k, v in query.items() if v})
                ok = "code" in received and received.get("state") == state
                self.send_response(200 if ok else 400)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                message = (
                    "Signed in. You can close this tab and go back to the terminal."
                    if ok
                    else "Sign-in did not complete. Go back to the terminal."
                )
                self.wfile.write(message.encode("utf-8"))

            def log_message(self, *args: Any) -> None:  # the URL carries the code: never log it
                return

        server = HTTPServer(("127.0.0.1", 0), Handler)
        server.timeout = 1.0
        redirect = f"http://127.0.0.1:{server.server_address[1]}"
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect,
            "response_type": "code",
            "scope": " ".join(self.scopes),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "access_type": "offline",
            "prompt": "consent",  # a fresh refresh token every time the owner signs in
        }
        if login_hint:
            params["login_hint"] = login_hint
        open_browser(f"{AUTH_URL}?{urlencode(params)}")

        deadline = time.monotonic() + wait_s
        try:
            while not received and time.monotonic() < deadline:
                server.handle_request()
        finally:
            server.server_close()

        if received.get("state") != state:
            raise AuthPortError("sign-in did not complete: the browser answer did not match")
        if "code" not in received:
            raise AuthPortError(f"sign-in did not complete: {received.get('error', 'timed out')}")

        answer = self._token_request(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": received["code"],
                "code_verifier": verifier,
                "grant_type": "authorization_code",
                "redirect_uri": redirect,
            }
        )
        refresh = answer.get("refresh_token")
        if not refresh:
            raise AuthPortError(
                "signed in but Google returned no refresh token; unattended sending will not "
                "work. Remove the app's access at myaccount.google.com/permissions and retry."
            )
        self._save_refresh(str(refresh))
        self._remember(answer)

    # -- internals ------------------------------------------------------------------------------

    def _token_request(self, form: dict[str, str]) -> dict[str, Any]:
        client = self.client or httpx.Client(timeout=self.timeout_s)
        try:
            response = client.post(TOKEN_URL, data=form)
        except httpx.TransportError as lost:
            # A token request changes nothing at Google that matters to us: safe to retry.
            raise TransientPortError(f"token request failed: {type(lost).__name__}") from lost
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code >= 500 or response.status_code == 429:
            raise TransientPortError(f"Google token endpoint answered {response.status_code}")
        if response.status_code >= 400 or "access_token" not in body:
            code = str(body.get("error", response.status_code)) if isinstance(body, dict) else ""
            raise AuthPortError(f"{REAUTH_REQUIRED}: Google refused the token ({code}).")
        return dict(body)

    def _remember(self, answer: dict[str, Any]) -> None:
        self._access = str(answer["access_token"])
        self._expires_at = time.time() + float(answer.get("expires_in", 3600))

    def _store(self) -> Any:
        if self.store is None:
            from msal_extensions import build_encrypted_persistence

            path = Path(self.cache_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                self.store = build_encrypted_persistence(str(path))
            except Exception as unavailable:
                raise ConfigError(
                    "no encrypted token store is available on this machine, and this project "
                    "does not fall back to a plain-text one. The file holds a refresh token."
                ) from unavailable
        return self.store

    def _load_refresh(self) -> str | None:
        try:
            raw = self._store().load()
        except FileNotFoundError:
            return None
        except Exception as unreadable:  # a corrupt or foreign file: sign in again
            raise AuthPortError(f"{REAUTH_REQUIRED}: the token file cannot be read") from unreadable
        if not raw:
            return None
        data = json.loads(raw)
        value = data.get("refresh_token") if isinstance(data, dict) else None
        return str(value) if value else None

    def _save_refresh(self, refresh: str) -> None:
        self._store().save(json.dumps({"refresh_token": refresh}))


@dataclass
class SecretRefreshStore:
    """A refresh token given as a secret, held in memory only (ADR 0045).

    For a host where the encrypted file cannot travel. It never writes anywhere: a sign-in
    there is impossible anyway (the loopback flow needs a browser on the same machine), and
    Google keeps the same refresh token across refreshes, so `save` has nothing to keep.
    """

    refresh_token: str = field(repr=False)

    def load(self) -> str:
        return json.dumps({"refresh_token": self.refresh_token})

    def save(self, _value: str) -> None:
        return None


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


__all__ = ["REAUTH_REQUIRED", "SCOPES", "GmailAuth", "SecretRefreshStore"]
