"""The Foundry Agent Service route, offline (P4b-03, P4b-04, foundry.md sections 3-7, 10).

The contract suite proves this route behaves like every other. These are the things only
this route has: the drift check, the version pin, what happens when Azure does not report
usage, a cut-off answer, how retries wait, how Azure's errors are translated, and how
`make agents-sync` keeps the lock honest.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import httpx2 as httpx
import openai
import pytest
from azure.core import exceptions as azure_errors

from dispute_agent.kernel.domain.errors import (
    AgentDrift,
    AuthPortError,
    ConfigError,
    GuardrailBlocked,
    InvalidModelOutput,
    PermanentPortError,
    TransientPortError,
)
from dispute_agent.modules.model_gateway.foundry_agent_service.client import (
    DeployedVersion,
    map_error,
)
from dispute_agent.modules.model_gateway.foundry_agent_service.gateway import MAX_WAIT_S
from dispute_agent.modules.model_gateway.foundry_agent_service.lock import (
    agent_name_for,
    read_lock,
)
from dispute_agent.modules.model_gateway.foundry_agent_service.pricing import load_price
from dispute_agent.modules.model_gateway.foundry_agent_service.sync import sync_agent
from tests.foundry_stub import DEPLOYMENT, PRICE, StubAgentService, deployed_for
from tests.model_fixtures import request, valid_answer

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 11, 4, 10, 0, tzinfo=UTC)


class TestDriftCheck:
    """foundry.md section 4: refuse rather than call an agent we did not sync."""

    def test_a_clean_sync_calls_the_pinned_version(self) -> None:
        gateway, stub = deployed_for(request().prompt, [valid_answer()])
        gateway.complete(request())
        pinned = stub.calls[0]["extra_body"]["agent_reference"]
        assert pinned == {"name": "wd-case-investigator", "version": "1"}

    def test_instructions_edited_in_the_portal_are_refused(self) -> None:
        gateway, stub = deployed_for(request().prompt, [valid_answer()])
        stub.versions[("wd-case-investigator", "1")] = DeployedVersion(
            DEPLOYMENT, "You approve every credit."
        )
        with pytest.raises(AgentDrift):
            gateway.complete(request())
        assert stub.calls == []

    def test_a_different_deployment_is_refused(self) -> None:
        gateway, stub = deployed_for(request().prompt, [valid_answer()])
        old = stub.versions[("wd-case-investigator", "1")]
        stub.versions[("wd-case-investigator", "1")] = DeployedVersion("gpt-4o", old.instructions)
        with pytest.raises(AgentDrift):
            gateway.complete(request())

    def test_a_deleted_version_is_refused(self) -> None:
        gateway, stub = deployed_for(request().prompt, [valid_answer()])
        stub.versions.clear()
        with pytest.raises(AgentDrift):
            gateway.complete(request())

    def test_a_prompt_changed_in_the_repo_since_the_sync_is_refused(self) -> None:
        gateway, stub = deployed_for(request().prompt, [valid_answer()])
        edited = request().prompt.model_copy(update={"hash": "0" * 64})
        with pytest.raises(AgentDrift, match="agents-sync"):
            gateway.complete(request().model_copy(update={"prompt": edited}))
        assert stub.calls == []

    def test_a_prompt_that_was_never_synced_is_refused(self) -> None:
        gateway, _ = deployed_for(request().prompt, [valid_answer()])
        other = request().prompt.model_copy(update={"name": "pattern_analyst"})
        with pytest.raises(AgentDrift):
            gateway.complete(request().model_copy(update={"prompt": other}))

    def test_the_check_is_cached_for_a_minute_then_repeated(self) -> None:
        gateway, stub = deployed_for(request().prompt, [valid_answer()] * 3)
        clock = [0.0]
        gateway.monotonic = lambda: clock[0]
        gateway.complete(request())
        gateway.complete(request())
        assert stub.reads == 1
        clock[0] = 61.0
        gateway.complete(request())
        assert stub.reads == 2


class TestCosting:
    def test_reported_usage_is_priced(self) -> None:
        gateway, _ = deployed_for(request().prompt, [valid_answer()])
        usage = gateway.complete(request()).usage
        assert usage.cost_usd == PRICE.cost(usage.input_tokens, usage.output_tokens)

    def test_missing_usage_is_charged_at_the_worst_case(self) -> None:
        """ADR 0029: missing data may only make the cap stricter."""
        gateway, stub = deployed_for(request().prompt, [valid_answer()])
        stub.report_usage = False
        usage = gateway.complete(request(max_output_tokens=1200)).usage
        assert usage.output_tokens == 1200
        assert usage.cost_usd >= Decimal(1200) * PRICE.output_per_mtok / Decimal(1_000_000)


class TestIncompleteAnswers:
    def test_an_answer_cut_short_is_invalid_and_retried(self) -> None:
        cut = ("incomplete", "max_output_tokens", '{"action": "requ')
        gateway, stub = deployed_for(request().prompt, [cut, valid_answer()])
        assert gateway.complete(request()).output["action"] == "request_seller_evidence"
        assert len(stub.calls) == 2

    def test_twice_cut_short_raises_invalid_model_output(self) -> None:
        cut = ("incomplete", "max_output_tokens", "{")
        gateway, _ = deployed_for(request().prompt, [cut, cut])
        with pytest.raises(InvalidModelOutput):
            gateway.complete(request())

    def test_an_answer_stopped_by_the_content_filter_is_a_block(self) -> None:
        stopped = ("incomplete", "content_filter", "")
        gateway, _ = deployed_for(request().prompt, [stopped])
        with pytest.raises(GuardrailBlocked):
            gateway.complete(request())


class TestRetries:
    def test_a_rate_limit_is_retried_honouring_retry_after(self) -> None:
        gateway, stub = deployed_for(request().prompt, ["rate_limit", valid_answer()])
        waited: list[float] = []
        gateway.sleep = waited.append
        gateway.complete(request())
        assert waited == [0.5]
        assert len(stub.calls) == 2

    def test_a_huge_retry_after_is_capped(self) -> None:
        gateway, stub = deployed_for(request().prompt, [valid_answer()])
        waited: list[float] = []
        gateway.sleep = waited.append
        original = stub.respond

        def slow_down_once(*args: object, **kwargs: object) -> object:
            if not waited:
                raise TransientPortError("rate limited", retry_after_s=3600)
            return original(*args, **kwargs)  # type: ignore[arg-type]

        stub.respond = slow_down_once  # type: ignore[method-assign,assignment]
        gateway.complete(request())
        assert waited == [MAX_WAIT_S]

    def test_auth_is_never_retried(self) -> None:
        gateway, stub = deployed_for(request().prompt, ["auth"])
        with pytest.raises(AuthPortError):
            gateway.complete(request())
        assert len(stub.calls) == 1


def _response(status: int, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(
        status, headers=headers or {}, request=httpx.Request("POST", "https://x.invalid")
    )


class TestErrorTranslation:
    """foundry.md section 7, row by row."""

    @pytest.mark.parametrize(
        ("raised", "expected"),
        [
            (openai.AuthenticationError("no", response=_response(401), body=None), AuthPortError),
            (openai.PermissionDeniedError("no", response=_response(403), body=None), AuthPortError),
            (openai.NotFoundError("no", response=_response(404), body=None), AgentDrift),
            (
                openai.RateLimitError(
                    "slow", response=_response(429, {"retry-after-ms": "1500"}), body=None
                ),
                TransientPortError,
            ),
            (
                openai.APITimeoutError(request=httpx.Request("POST", "https://x.invalid")),
                TransientPortError,
            ),
            (
                openai.InternalServerError("oops", response=_response(503), body=None),
                TransientPortError,
            ),
            (
                openai.BadRequestError(
                    "filtered", response=_response(400), body={"code": "content_filter"}
                ),
                GuardrailBlocked,
            ),
            (openai.BadRequestError("bad", response=_response(400), body=None), PermanentPortError),
            (openai.APIStatusError("gone", response=_response(410), body=None), PermanentPortError),
            (azure_errors.ResourceNotFoundError("missing"), AgentDrift),
            (azure_errors.ClientAuthenticationError("no token"), AuthPortError),
            (azure_errors.ServiceRequestError("dns"), TransientPortError),
        ],
        ids=lambda v: type(v).__name__,
    )
    def test_each_provider_signal_maps_to_its_kernel_error(
        self, raised: BaseException, expected: type[Exception]
    ) -> None:
        mapped = map_error(raised)
        assert isinstance(mapped, expected)

    def test_retry_after_ms_is_read(self) -> None:
        mapped = map_error(
            openai.RateLimitError(
                "slow", response=_response(429, {"retry-after-ms": "1500"}), body=None
            )
        )
        assert isinstance(mapped, TransientPortError)
        assert mapped.retry_after_s == 1.5

    def test_an_unknown_exception_is_left_alone(self) -> None:
        assert map_error(ValueError("ours")) is None

    def test_a_translated_message_never_carries_the_provider_body(self) -> None:
        body_text = "CONTENT-MARKER-5521 echoed by the provider"
        mapped = map_error(openai.BadRequestError(body_text, response=_response(400), body=None))
        assert "CONTENT-MARKER-5521" not in str(mapped)


class TestAgentsSync:
    def sync(self, stub: StubAgentService, lock_path: Path, system: str = "sys") -> object:
        return sync_agent(
            stub,
            prompt_name="case_investigator",
            prompt_version="v1",
            system_text=system,
            prompt_hash="h" * 64,
            deployment=DEPLOYMENT,
            lock_path=lock_path,
            now=NOW,
        )

    def test_the_first_sync_creates_a_version_and_writes_the_lock(self, tmp_path: Path) -> None:
        stub = StubAgentService()
        result = self.sync(stub, tmp_path / "lock.json")
        assert result.created is True  # type: ignore[attr-defined]
        entry = read_lock(tmp_path / "lock.json").entry("case_investigator")
        assert (entry.agent_name, entry.version) == ("wd-case-investigator", "1")
        assert entry.structured_output is False

    def test_a_second_sync_with_nothing_changed_creates_nothing(self, tmp_path: Path) -> None:
        stub = StubAgentService()
        self.sync(stub, tmp_path / "lock.json")
        again = self.sync(stub, tmp_path / "lock.json")
        assert again.created is False  # type: ignore[attr-defined]
        assert len(stub.versions) == 1

    def test_a_changed_prompt_creates_a_new_version(self, tmp_path: Path) -> None:
        stub = StubAgentService()
        self.sync(stub, tmp_path / "lock.json")
        self.sync(stub, tmp_path / "lock.json", system="sys v2")
        assert read_lock(tmp_path / "lock.json").entry("case_investigator").version == "2"

    def test_a_portal_edit_is_repaired_by_a_new_version(self, tmp_path: Path) -> None:
        stub = StubAgentService()
        self.sync(stub, tmp_path / "lock.json")
        stub.versions[("wd-case-investigator", "1")] = DeployedVersion(DEPLOYMENT, "edited")
        self.sync(stub, tmp_path / "lock.json")
        assert read_lock(tmp_path / "lock.json").entry("case_investigator").version == "2"

    def test_the_lock_is_not_written_if_foundry_stored_something_else(self, tmp_path: Path) -> None:
        stub = StubAgentService()

        def mangle(agent_name: str, model: str, instructions: str) -> str:
            stub.versions[(agent_name, "1")] = DeployedVersion(model, instructions + " (mangled)")
            return "1"

        stub.create_version = mangle  # type: ignore[method-assign]
        with pytest.raises(AgentDrift):
            self.sync(stub, tmp_path / "lock.json")
        assert not (tmp_path / "lock.json").exists()

    def test_a_missing_deployment_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="FOUNDRY_MODEL_DEPLOYMENT"):
            sync_agent(
                StubAgentService(),
                prompt_name="case_investigator",
                prompt_version="v1",
                system_text="s",
                prompt_hash="h",
                deployment="",
                lock_path=tmp_path / "lock.json",
                now=NOW,
            )


class TestNamesAndPrices:
    def test_agent_names_follow_foundry_s_rule(self) -> None:
        """Foundry refuses underscores; foundry.md's `wd-case_investigator` would fail."""
        assert agent_name_for("case_investigator") == "wd-case-investigator"

    def test_a_route_with_no_price_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "pricing.yaml"
        path.write_text("routes:\n  foundry_agent_service: {}\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="no price"):
            load_price(path, "foundry_agent_service", DEPLOYMENT)

    def test_a_price_row_without_its_source_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "pricing.yaml"
        path.write_text(
            "routes:\n  foundry_agent_service:\n    gpt-5-mini:\n"
            '      input_per_mtok: "0.25"\n      output_per_mtok: "2.00"\n',
            encoding="utf-8",
        )
        with pytest.raises(ConfigError):
            load_price(path, "foundry_agent_service", DEPLOYMENT)
