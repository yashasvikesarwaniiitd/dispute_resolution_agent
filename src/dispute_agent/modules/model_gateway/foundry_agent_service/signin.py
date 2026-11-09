"""Signing in to Foundry without the Azure CLI (I14: Entra ID only, no keys).

The owner's work laptop cannot install the Azure CLI, so `az login` is not available. This
signs in through the browser instead, once, with `make foundry-login`, and every later run
signs in silently from an encrypted token cache - the same shape as the Outlook sign-in
(ADR 0023).

Three decisions, each for a reason:

**The tenant is named, never guessed.** On a work laptop the browser and Windows are already
signed in to a work account. A credential allowed to pick "whoever is signed in" could pick
that one, and calls would fail as a confusing 403 against the wrong directory - or, worse,
succeed against something that is not the test project. `AZURE_TENANT_ID` pins the
directory the pilot subscription lives in.

**Encrypted or nothing.** The token cache uses the platform keystore (DPAPI on Windows);
`allow_unencrypted_storage` stays False, so a machine without one fails rather than writing
a readable refresh token.

**The runtime never opens a browser.** Only `make foundry-login` signs in interactively.
Everywhere else `disable_automatic_authentication` is set, so an expired sign-in becomes an
`AuthPortError` ("run make foundry-login") and the case goes to a person, instead of a
background run hanging on a browser window nobody is watching.

The authentication record written to `data/` names the account and tenant; it holds no
token. The tokens live only in the encrypted cache.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from dispute_agent.kernel.domain.errors import AuthPortError, ConfigError

if TYPE_CHECKING:
    from pathlib import Path

#: The Foundry data-plane audience; the SDK asks for the same.
SCOPE = "https://ai.azure.com/.default"
CACHE_NAME = "dispute_agent.foundry"
TENANT = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def check_tenant(tenant_id: str | None) -> str:
    if not tenant_id or not TENANT.match(tenant_id.strip()):
        raise ConfigError(
            "AZURE_TENANT_ID is not set to a directory id (a GUID). Find it in the Azure "
            "portal: Microsoft Entra ID > Overview > Tenant ID (docs/SETUP_FOUNDRY.md)."
        )
    return tenant_id.strip()


def _cache() -> Any:
    from azure.identity import TokenCachePersistenceOptions

    return TokenCachePersistenceOptions(name=CACHE_NAME, allow_unencrypted_storage=False)


def app_credential(tenant_id: str | None, client_id: str, client_secret: str) -> Any:
    """The live site's sign-in (ADR 0049): a Microsoft Entra app registration.

    Still Entra ID only (I14), named to the same pinned directory. The owner creates the
    registration and gives it a Foundry role; its secret lives only in the host's secret store
    and reaches here through settings (I12). No browser, no cache.
    """
    from azure.identity import ClientSecretCredential

    return ClientSecretCredential(
        tenant_id=check_tenant(tenant_id), client_id=client_id, client_secret=client_secret
    )


def runtime_credential(tenant_id: str | None, record_path: Path) -> Any:
    """A credential that signs in silently from the cache, or raises AuthPortError."""
    from azure.identity import AuthenticationRecord, InteractiveBrowserCredential

    tenant = check_tenant(tenant_id)
    if not record_path.is_file():
        raise AuthPortError("not signed in to Foundry yet; run `make foundry-login`", status=401)
    record = AuthenticationRecord.deserialize(record_path.read_text(encoding="utf-8"))
    if record.tenant_id != tenant:
        raise AuthPortError(
            "the saved Foundry sign-in is for a different directory than AZURE_TENANT_ID; "
            "run `make foundry-login`",
            status=401,
        )
    return InteractiveBrowserCredential(
        tenant_id=tenant,
        cache_persistence_options=_cache(),
        authentication_record=record,
        disable_automatic_authentication=True,
    )


def sign_in(tenant_id: str | None, record_path: Path, *, device_code: bool = False) -> str:
    """The once-in-a-while interactive sign-in. Returns the signed-in account's name.

    `device_code` is for a machine where the browser redirect back to localhost is
    blocked: it prints a short code to type at microsoft.com/devicelogin instead.
    """
    from azure.identity import DeviceCodeCredential, InteractiveBrowserCredential

    tenant = check_tenant(tenant_id)
    credential: Any
    if device_code:
        credential = DeviceCodeCredential(tenant_id=tenant, cache_persistence_options=_cache())
    else:
        credential = InteractiveBrowserCredential(
            tenant_id=tenant, cache_persistence_options=_cache()
        )
    record = credential.authenticate(scopes=[SCOPE])
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(record.serialize(), encoding="utf-8")
    return str(record.username)


__all__ = ["SCOPE", "app_credential", "check_tenant", "runtime_credential", "sign_in"]
