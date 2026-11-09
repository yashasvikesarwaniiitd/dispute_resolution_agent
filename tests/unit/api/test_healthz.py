"""Card P0-08: /healthz says what is actually running.

The test the card asks for is the interesting one: a fixture `modules.yaml` with `email`
switched off, and /healthz reporting the **Disabled** adapter rather than the one named in
the file. That difference is the whole point of the endpoint — the file records an intent,
the endpoint records what got wired, and when a module is off those are not the same string.

Every app here is built from an explicit config directory, so no test touches os.environ and
the order they run in does not matter.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dispute_agent.app import build_application, create_app
from dispute_agent.kernel.domain.errors import ConfigError

ROOT = Path(__file__).resolve().parents[3]
SHIPPED_CONFIG = ROOT / "config"

PHASE_0_FILE = """
modules:
  evidence:      {enabled: false, adapter: synthetic_shipments}
  model_gateway: {enabled: false, adapter: fake}
  reasoning:     {enabled: false, adapter: rules_only}
  input_screen:  {enabled: true,  adapter: rules_based}
  tracing:       {enabled: true,  adapter: noop}
  image_check:   {enabled: false, adapter: recorded}
  email:         {enabled: false, adapter: outlook_graph}
  ticketing:     {enabled: false, adapter: fake}
  ledger:        {enabled: false, adapter: simulated}
  approvals:     {enabled: false, adapter: store_backed}
  cross_case:    {enabled: false, adapter: "null"}
  evals:         {enabled: true,  adapter: default}
"""


@pytest.fixture
def config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "modules.yaml").write_text(PHASE_0_FILE, encoding="utf-8")
    # Run where no `data/cases.sqlite3` exists: `create_app` uses one if it finds it, and an
    # owner who has run the app locally has one, which made these tests depend on the folder.
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def client(config_dir: Path) -> TestClient:
    return TestClient(create_app(config_dir=config_dir, app_env="test"))


def modules_by_name(payload: dict[str, object]) -> dict[str, dict[str, object]]:
    rows = payload["modules"]
    assert isinstance(rows, list)
    return {row["name"]: row for row in rows}


class TestHealthz:
    def test_it_answers(self, client: TestClient) -> None:
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_a_disabled_module_names_its_disabled_adapter(self, client: TestClient) -> None:
        """The file says outlook_graph; what is wired is the Disabled adapter (I6)."""
        rows = modules_by_name(client.get("/healthz").json())
        assert rows["email"] == {"name": "email", "enabled": False, "adapter": "disabled"}

    def test_an_enabled_module_names_its_real_adapter(self, client: TestClient) -> None:
        rows = modules_by_name(client.get("/healthz").json())
        assert rows["input_screen"] == {
            "name": "input_screen",
            "enabled": True,
            "adapter": "rules_based",
        }

    def test_cross_case_and_tracing_name_their_own_off_adapters(self, client: TestClient) -> None:
        """Not every module off means Disabled (orchestrator.md section 5)."""
        rows = modules_by_name(client.get("/healthz").json())
        assert rows["cross_case"]["adapter"] == "null"
        assert rows["tracing"]["adapter"] == "noop"

    def test_all_twelve_modules_are_reported(self, client: TestClient) -> None:
        rows = modules_by_name(client.get("/healthz").json())
        assert len(rows) == 12

    def test_it_reports_no_settings_or_secrets(self, client: TestClient) -> None:
        """I12. Which adapter runs is operational fact; what it was configured with is not."""
        body = client.get("/healthz").text
        for row in client.get("/healthz").json()["modules"]:
            assert set(row) == {"name", "enabled", "adapter"}
        assert "seed" not in body
        assert "settings" not in body


class TestTheAppFactory:
    def test_two_apps_can_differ_in_one_process(self, config_dir: Path) -> None:
        """An app factory taking a path is what makes this possible without os.environ."""
        other = config_dir / "other"
        other.mkdir()
        (other / "modules.yaml").write_text(
            PHASE_0_FILE.replace(
                "input_screen:  {enabled: true,  adapter: rules_based}",
                "input_screen:  {enabled: false, adapter: rules_based}",
            ),
            encoding="utf-8",
        )
        first = TestClient(create_app(config_dir=config_dir, app_env="test"))
        second = TestClient(create_app(config_dir=other, app_env="test"))

        assert modules_by_name(first.get("/healthz").json())["input_screen"]["enabled"] is True
        assert modules_by_name(second.get("/healthz").json())["input_screen"]["enabled"] is False

    def test_the_shipped_config_builds(self) -> None:
        """If this fails, `make run-api` fails."""
        application = build_application(config_dir=SHIPPED_CONFIG, app_env="local")
        # Phase 7: the whole line runs offline with cross_case on; model_gateway stays off.
        assert set(application.registry.enabled_names()) == {
            "approvals",
            "cross_case",
            "email",
            "evals",
            "evidence",
            "image_check",
            "input_screen",
            "ledger",
            "reasoning",
            "ticketing",
            "tracing",
        }

    def test_the_shipped_config_builds_under_ci(self) -> None:
        build_application(config_dir=SHIPPED_CONFIG, app_env="ci")

    def test_a_missing_file_fails_fast_and_says_where(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match=r"modules.yaml"):
            build_application(config_dir=tmp_path / "nowhere", app_env="test")

    def test_an_unsafe_combination_never_builds_an_app(self, tmp_path: Path) -> None:
        """Validation runs before anything is constructed (CLAUDE.md section 6)."""
        (tmp_path / "modules.yaml").write_text(
            PHASE_0_FILE.replace(
                "ledger:        {enabled: false, adapter: simulated}",
                "ledger:        {enabled: true,  adapter: simulated}",
            ),
            encoding="utf-8",
        )
        with pytest.raises(ConfigError, match="approvals"):
            create_app(config_dir=tmp_path, app_env="test")

    def test_a_real_channel_without_the_owner_s_account_fails_fast(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A real channel is never built from defaults (I11): caught at startup, by name.

        `.env` is ignored here: on the owner's machine it holds real Jira settings, and then
        the app rightly starts - which is not the case this test is about.
        """
        from dispute_agent.infra.settings import Settings

        monkeypatch.setattr(
            "dispute_agent.app.Settings", lambda: Settings(_env_file=None, app_env="test")
        )
        (tmp_path / "modules.yaml").write_text(
            PHASE_0_FILE.replace(
                "ticketing:     {enabled: false, adapter: fake}",
                "ticketing:     {enabled: true,  adapter: jira_cloud}",
            ),
            encoding="utf-8",
        )
        with pytest.raises(ConfigError, match="needs the owner's test account"):
            create_app(config_dir=tmp_path, app_env="test")


class TestTheWiring:
    def test_disabled_modules_get_adapters_that_raise(self, config_dir: Path) -> None:
        """I6 end to end: the registry chose Disabled, and Disabled is what was built."""
        from dispute_agent.modules.email.disabled import DisabledMessageChannel
        from dispute_agent.modules.evidence.disabled import DisabledEvidenceSource

        adapters = build_application(config_dir=config_dir, app_env="test").adapters
        assert isinstance(adapters.email, DisabledMessageChannel)
        assert isinstance(adapters.evidence, DisabledEvidenceSource)

    def test_cross_case_gets_the_null_pair(self, config_dir: Path) -> None:
        from dispute_agent.modules.cross_case.null import (
            NullOutcomeObserver,
            NullPatternContextProvider,
        )

        adapters = build_application(config_dir=config_dir, app_env="test").adapters
        assert isinstance(adapters.patterns, NullPatternContextProvider)
        assert isinstance(adapters.outcomes, NullOutcomeObserver)

    def test_every_port_is_wired(self, config_dir: Path) -> None:
        """A field left None would be a port nobody noticed was missing."""
        adapters = build_application(config_dir=config_dir, app_env="test").adapters
        assert all(getattr(adapters, field) is not None for field in adapters.__dataclass_fields__)
        assert len(adapters.__dataclass_fields__) == 13
