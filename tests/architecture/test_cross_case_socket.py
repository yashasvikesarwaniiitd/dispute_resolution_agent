"""Card P0-10: D3 — the cross-case socket is connectable without touching `kernel/`.

Phase 7 is six phases away, which is exactly why this test exists now. "We will be able to
plug it in later" is the kind of claim that is cheap to make in Phase 0 and expensive to
discover is false in Phase 7, by which point the kernel has grown around the assumption that
there are no patterns.

So the claim is made falsifiable today. A stub provider that returns a real pattern is
substituted the way Phase 7 will substitute the real one — through the composition root's
Adapters record — and the checks are that it satisfies the port unchanged, that the kernel
contains no mention of the module, and that the socket's I7 guarantee survives the swap.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from dispute_agent.kernel.domain.case import CaseFacts
from dispute_agent.kernel.domain.enums import VerdictKind
from dispute_agent.kernel.domain.ids import CaseId, PatternId
from dispute_agent.kernel.domain.patterns import CautionSignal, PatternRecord
from dispute_agent.modules.cross_case.null import NullPatternContextProvider

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dispute_agent.kernel.ports import CaseOutcomeObserver, PatternContextProvider

ROOT = Path(__file__).resolve().parents[2]
KERNEL = ROOT / "src" / "dispute_agent" / "kernel"
KERNEL_SOURCES = sorted(KERNEL.rglob("*.py"))

NOW = datetime(2026, 3, 1, 9, 30, tzinfo=UTC)

FACTS = CaseFacts(
    shipment_ref="SHP-0001",
    claim_ref="CHG-0001",
    seller_ref="SELLER-0001",
    seller_contact="seller@example.invalid",
    raised_at=NOW,
)

PATTERN = PatternRecord(
    id=PatternId("pat_01HQ"),
    status="UNCONFIRMED",
    summary="Seven disputes at HUB-07 share a volumetric re-measure above the declared dims.",
    matched_on={"hub": "HUB-07"},
    related_case_ids=(CaseId("case_01HQ"),),
    first_seen_at=NOW,
    last_seen_at=NOW,
)


class StubPatternContextProvider:
    """What Phase 7's adapter will look like from the kernel's side: patterns, nothing else."""

    def patterns_for(self, facts: CaseFacts) -> Sequence[PatternRecord]:
        return (PATTERN,)


class RecordingOutcomeObserver:
    """And the write side, which must never be able to affect the case."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    def on_outcome(self, outcome: object) -> None:
        self.seen.append(getattr(outcome, "id", "?"))


class TestTheSocketIsConnectable:
    def test_a_stub_satisfies_the_port_with_no_change_to_it(self) -> None:
        """Static conformance: mypy checks these two bindings."""
        provider: PatternContextProvider = StubPatternContextProvider()
        observer: CaseOutcomeObserver = RecordingOutcomeObserver()
        assert provider.patterns_for(FACTS) == (PATTERN,)
        assert observer is not None

    def test_swapping_the_adapter_changes_what_the_socket_returns(self) -> None:
        """The null adapter and a real one differ only in what comes back."""
        assert NullPatternContextProvider().patterns_for(FACTS) == ()
        assert StubPatternContextProvider().patterns_for(FACTS) == (PATTERN,)

    def test_the_swap_happens_in_the_composition_root_record(self, tmp_path: Path) -> None:
        """Phase 7 replaces one field of Adapters. Nothing else in the system moves."""
        from dispute_agent.app import build_application

        (tmp_path / "modules.yaml").write_text(
            (ROOT / "config" / "modules.yaml").read_text(encoding="utf-8"), encoding="utf-8"
        )
        application = build_application(config_dir=tmp_path, app_env="test")

        # Phase 7 ships the real adapter (P7-12). A known shipment with no pattern has none;
        # a shipment it cannot place is "we do not know" - caution, never a silent "none".
        from dispute_agent.kernel.domain.errors import TransientPortError

        known = FACTS.model_copy(update={"shipment_ref": "SHP-000001", "claim_ref": "CLM-000001"})
        assert application.adapters.patterns.patterns_for(known) == ()
        with pytest.raises(TransientPortError):
            application.adapters.patterns.patterns_for(FACTS)
        connected = dataclasses.replace(application.adapters, patterns=StubPatternContextProvider())
        assert connected.patterns.patterns_for(FACTS) == (PATTERN,)
        # Every other port is the same object: connecting cross-case rewires one field.
        for field in dataclasses.fields(connected):
            if field.name != "patterns":
                assert getattr(connected, field.name) is getattr(application.adapters, field.name)


class TestTheKernelDoesNotKnowAboutIt:
    @pytest.mark.parametrize("path", KERNEL_SOURCES, ids=lambda p: str(p.relative_to(KERNEL)))
    def test_no_kernel_file_imports_a_module_or_names_an_adapter_class(self, path: Path) -> None:
        """D3: connecting cross-case intelligence changes zero lines in kernel/.

        The first version of this test also forbade the string "sql_context", and it failed
        on the registry catalogue — correctly pointing at a distinction worth being precise
        about rather than at a violation.

        Naming `sql_context` as an available adapter is what *keeps* D3. If the catalogue
        listed only the adapters that exist today, Phase 7 would have to come back and edit a
        kernel file to add the new one, which is the exact change D3 says must not be needed.
        The name is configuration data in a table; the kernel never imports it, never
        constructs it, and does not care whether it has been written yet.

        What would be a violation is a dependency: importing a module package, or naming a
        concrete adapter class, either of which ties the kernel to an implementation.
        """
        source = path.read_text(encoding="utf-8")
        assert "dispute_agent.modules" not in source, (
            f"{path.relative_to(KERNEL)} imports a module; the kernel depends on ports only"
        )
        for adapter_class in (
            "NullPatternContextProvider",
            "NullOutcomeObserver",
            "SqlPatternContextProvider",
        ):
            assert adapter_class not in source, (
                f"{path.relative_to(KERNEL)} names the adapter class {adapter_class!r}"
            )

    def test_the_catalogue_already_knows_the_phase_7_adapter(self) -> None:
        """The positive half: Phase 7 adds no row, because the row is already there."""
        from dispute_agent.kernel.registry.module_spec import MODULE_SPECS

        cross_case = MODULE_SPECS["cross_case"]
        assert set(cross_case.adapters) == {"null", "sql_context"}
        assert cross_case.adapters["sql_context"].depends_on == ("ticketing",)
        assert cross_case.off_adapter == "null"

    def test_the_catalogue_holds_names_not_objects(self) -> None:
        """A factory here would make the kernel construct an adapter, and "Kernel is pure"
        would have to be weakened to let it import one."""
        from dispute_agent.kernel.registry.module_spec import AdapterSpec

        assert set(AdapterSpec.__dataclass_fields__) == {
            "name",
            "depends_on",
            "preview",
            "needs_env",
        }
        assert all(
            field.type in ("str", "tuple[str, ...]", "bool")
            for field in AdapterSpec.__dataclass_fields__.values()
        )


class TestTheSocketKeepsItsGuarantee:
    def test_a_connected_provider_still_cannot_lower_a_verdict(self) -> None:
        """I7 survives the swap because it is carried by the type, not by the null adapter.

        Worth stating plainly: the reason Phase 7 is safe is not that today's adapter returns
        nothing. It is that a pattern can only ever be turned into a CautionSignal, and a
        CautionSignal has no way to say "allow".
        """
        signal = CautionSignal(
            kind="pattern_match",
            min_verdict=VerdictKind.REQUIRE_APPROVAL,
            reason=f"matches pattern {PATTERN.id}",
            source_ref=PATTERN.id,
        )
        assert signal.min_verdict > VerdictKind.ALLOW
        assert "max_verdict" not in CautionSignal.model_fields

    def test_a_pattern_summary_is_not_a_fact(self) -> None:
        """A pattern is a hypothesis about other cases. The summary may be model-written, so
        nothing may treat it as evidence — it is capped, carried, and labelled by the
        briefing, never promoted into the assessment."""
        from dispute_agent.kernel.domain.evidence import ChargeAssessment

        assert "pattern" not in ChargeAssessment.model_fields
        assert "summary" not in ChargeAssessment.model_fields
