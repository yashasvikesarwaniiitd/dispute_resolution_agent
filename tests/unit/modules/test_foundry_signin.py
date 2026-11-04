"""Browser sign-in for Foundry, without the Azure CLI (signin.py, I14).

No test signs in for real: these check the decisions around the sign-in - the tenant is
pinned, the runtime never opens a browser, a missing or mismatched sign-in is an auth error
naming the fix, and the cache is never allowed to fall back to plain text.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from azure.identity import AuthenticationRecord, AuthenticationRequiredError

from dispute_agent.kernel.domain.errors import AuthPortError, ConfigError
from dispute_agent.modules.model_gateway.foundry_agent_service import signin
from dispute_agent.modules.model_gateway.foundry_agent_service.client import map_error

if TYPE_CHECKING:
    from pathlib import Path

TENANT = "11111111-2222-3333-4444-555555555555"
OTHER = "99999999-8888-7777-6666-555555555555"


def record_for(tenant: str, path: Path) -> Path:
    record = AuthenticationRecord(
        tenant, "client-id", "login.microsoftonline.com", "home.account", "owner@example.com"
    )
    path.write_text(record.serialize(), encoding="utf-8")
    return path


class TestTheTenantIsPinned:
    @pytest.mark.parametrize("bad", [None, "", "contoso.onmicrosoft.com", "not-a-guid"])
    def test_a_missing_or_malformed_tenant_is_refused(self, bad: str | None) -> None:
        with pytest.raises(ConfigError, match="AZURE_TENANT_ID"):
            signin.check_tenant(bad)

    def test_a_directory_id_is_accepted(self) -> None:
        assert signin.check_tenant(f"  {TENANT} ") == TENANT

    def test_a_saved_sign_in_for_another_directory_is_refused(self, tmp_path: Path) -> None:
        """A work account's sign-in must never be reused for the pilot subscription."""
        path = record_for(OTHER, tmp_path / "record.json")
        with pytest.raises(AuthPortError, match="foundry-login"):
            signin.runtime_credential(TENANT, path)


class TestTheRuntimeNeverOpensABrowser:
    def test_no_saved_sign_in_is_an_auth_error_naming_the_fix(self, tmp_path: Path) -> None:
        with pytest.raises(AuthPortError, match="make foundry-login"):
            signin.runtime_credential(TENANT, tmp_path / "missing.json")

    def test_the_runtime_credential_is_silent_only(self, tmp_path: Path) -> None:
        credential = signin.runtime_credential(TENANT, record_for(TENANT, tmp_path / "r.json"))
        assert credential._disable_automatic_authentication is True

    def test_an_expired_sign_in_maps_to_an_auth_error(self) -> None:
        mapped = map_error(AuthenticationRequiredError(scopes=[signin.SCOPE]))
        assert isinstance(mapped, AuthPortError)
        assert "foundry-login" in str(mapped)


class TestTheCacheIsEncryptedOrNothing:
    def test_plain_text_storage_is_never_allowed(self) -> None:
        assert signin._cache().allow_unencrypted_storage is False
