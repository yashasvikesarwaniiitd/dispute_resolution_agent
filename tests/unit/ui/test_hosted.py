"""The hosted console's safeguards (ADR 0045): the gate, the secrets, the configuration.

The hosted console sends real email and opens real Jira issues, so each safeguard is tested
for the way it must fail: closed. No password configured opens nothing; a wrong one opens
nothing; a missing account stops the service from starting and names what is missing; the
Gmail token given as a secret never shows in a repr; and the local console stays on loopback.
"""

from __future__ import annotations

import secrets
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import SecretStr
from streamlit.testing.v1 import AppTest

from dispute_agent.app import DEFAULT_CONFIG_DIR, _build_gmail, build_application, load_modules_file
from dispute_agent.infra.settings import Settings
from dispute_agent.kernel.domain.errors import ConfigError
from dispute_agent.modules.email.gmail_api.auth import SecretRefreshStore
from dispute_agent.ui.gate import opens

if TYPE_CHECKING:
    from collections.abc import Iterator

ROOT = Path(__file__).resolve().parents[3]
HOSTED = "modules.hosted.yaml"


@pytest.fixture
def clean_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """No `.env`, no stray secrets: only what a test sets."""
    for name in ("CONSOLE_PASSWORD", "GMAIL_REFRESH_TOKEN", "MODULES_FILE", "JIRA_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    yield tmp_path


class TestTheGate:
    def test_only_the_configured_password_opens(self) -> None:
        password = secrets.token_hex(8)
        assert opens(password, password)
        assert not opens(password + "x", password)
        assert not opens("", password) and not opens(None, password)

    def test_no_password_configured_opens_nothing(self) -> None:
        assert not opens("anything", None) and not opens("", "")


def _hosted() -> None:
    from dispute_agent.ui.app import main

    main(hosted=True)


class TestTheHostedConsole:
    def test_without_a_password_set_it_stays_closed(self, clean_env: Path) -> None:
        at = AppTest.from_function(_hosted, default_timeout=60).run()
        assert at.error and "no password" in at.error[0].value
        assert not at.text_input  # no form, no screens

    def test_a_wrong_password_shows_no_screen(
        self, clean_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CONSOLE_PASSWORD", secrets.token_hex(8))
        at = AppTest.from_function(_hosted, default_timeout=60).run()
        at.text_input[0].input("not-it")
        at.button[0].click().run()
        assert at.error and "not correct" in at.error[0].value
        assert not any(t.value == "Raise a ticket" for t in at.title)

    def test_the_right_password_opens_the_screens(
        self, clean_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        password = secrets.token_hex(8)
        monkeypatch.setenv("CONSOLE_PASSWORD", password)
        at = AppTest.from_function(_hosted, default_timeout=60).run()
        at.text_input[0].input(password)
        at.button[0].click().run()
        assert not at.exception
        assert any(t.value == "Raise a ticket" for t in at.title)


class TestSecrets:
    def test_the_gmail_token_from_a_secret_is_held_in_memory_and_never_shown(self) -> None:
        token = "1//" + secrets.token_urlsafe(24)
        store = SecretRefreshStore(token)
        assert token in store.load() and token not in repr(store)
        store.save("ignored")  # never written anywhere
        assert token in store.load()

    def test_gmail_uses_the_secret_when_one_is_given(self) -> None:
        settings = Settings(
            _env_file=None,
            gmail_client_id="client.apps.example",
            gmail_client_secret=SecretStr("not-a-real-secret"),
            gmail_mailbox="pilot@gmail.example",
            gmail_refresh_token=SecretStr("1//not-a-real-token"),
        )
        channel = _build_gmail(settings)
        auth = channel.auth  # type: ignore[attr-defined]
        assert isinstance(auth.store, SecretRefreshStore) and auth.signed_in()

    def test_new_secrets_are_redacted_from_logs(self) -> None:
        settings = Settings(
            _env_file=None,
            console_password=SecretStr("a-long-console-password"),
            gmail_refresh_token=SecretStr("1//a-long-refresh-token"),
        )
        assert {"a-long-console-password", "1//a-long-refresh-token"} <= settings.secret_values()


class TestConfiguration:
    def test_the_hosted_file_is_real_gmail_and_jira_with_the_rules_engine(self) -> None:
        modules = load_modules_file(DEFAULT_CONFIG_DIR, HOSTED).modules
        assert modules["email"].adapter == "gmail_api"
        assert modules["ticketing"].adapter == "jira_cloud"
        assert modules["reasoning"].adapter == "rules_only"

    def test_a_named_file_that_is_missing_is_an_error_not_a_fallback(self) -> None:
        with pytest.raises(ConfigError, match="MODULES_FILE"):
            load_modules_file(DEFAULT_CONFIG_DIR, "modules.nowhere.yaml")

    def test_hosted_without_the_accounts_refuses_to_start_and_names_them(
        self, clean_env: Path
    ) -> None:
        settings = Settings(_env_file=None, modules_file=HOSTED)
        with pytest.raises(ConfigError) as refused:
            build_application(config_dir=DEFAULT_CONFIG_DIR, app_env="test", settings=settings)
        assert "needs the owner's test account" in str(refused.value)

    def test_the_local_console_stays_on_loopback(self) -> None:
        config = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
        assert "address" not in config.get("server", {})  # the host decides, hosted
        make = (ROOT / "scripts" / "make.py").read_text(encoding="utf-8")
        assert '"--server.address", "127.0.0.1"' in make  # locally: loopback only (T09)

    def test_the_hosted_entry_composes_outside_the_package(self) -> None:
        entry = (ROOT / "streamlit_app.py").read_text(encoding="utf-8")
        assert "main(hosted=True" in entry and "create_app" in entry
        assert (ROOT / "requirements.txt").read_text(encoding="utf-8").strip().endswith(".")


class TestTheSecretsBlock:
    """`make hosted-secrets` builds what Streamlit's Secrets box takes; nothing is printed."""

    def _module(self) -> object:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "hosted_secrets", ROOT / "scripts" / "hosted_secrets.py"
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_the_block_is_valid_toml_with_every_needed_name(self) -> None:
        hs: object = self._module()
        # A quote and a backslash in each value: the block must escape both.
        values = {name: "v-" + name + '"' + "\\" for name in hs.NEEDED}  # type: ignore[attr-defined]
        parsed = tomllib.loads(hs.toml_block(values))  # type: ignore[attr-defined]
        assert parsed == values

    def test_missing_accounts_are_named_not_copied(self) -> None:
        hs: object = self._module()
        values, missing = hs.collect(Settings(_env_file=None), None, "pw")  # type: ignore[attr-defined]
        assert values["MODULES_FILE"] == "modules.hosted.yaml"
        assert "GMAIL_REFRESH_TOKEN" in missing and "JIRA_API_TOKEN" in missing
        assert "CONSOLE_PASSWORD" not in missing


class TestTheServiceAcrossReloads:
    """Streamlit unloads every watched module when a file changes - a push to GitHub does
    that on Streamlit Cloud - and imports fresh copies. A service kept from before would hold
    the old classes, and the strict domain models then refuse objects made from the new ones
    (a pydantic ValidationError on raising a ticket, seen live on 2026-11-08). So the hosted
    entry keeps one service per loaded copy of the code, and stops the old one first."""

    def _entry(self) -> Any:
        import importlib.util

        spec = importlib.util.spec_from_file_location("hosted_entry", ROOT / "streamlit_app.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # must not start the console when imported
        return module

    def test_one_service_per_copy_of_the_code_and_the_old_one_stopped(self) -> None:
        entry = self._entry()
        started: list[Any] = []

        class Client:
            def __init__(self) -> None:
                self.stopped = False
                started.append(self)

            def __exit__(self, *_: object) -> None:
                self.stopped = True

        services = entry.Services()
        before, after = object(), object()  # two loaded copies of the code
        first = services.get(before, Client)
        assert services.get(before, Client) is first and len(started) == 1  # kept
        second = services.get(after, Client)  # the code was reloaded
        assert second is not first and first.stopped and not second.stopped
