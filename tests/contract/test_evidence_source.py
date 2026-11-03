"""Contract: EvidenceSource (kernel-interfaces.md 6.4).

Three answers, kept distinct: FOUND with a value, MISSING, FAILED with a code. The suite
exists mostly to stop a source collapsing them — returning MISSING when it actually timed
out reads as "we looked and there was nothing there", and the calculator would treat an
outage as a fact about the shipment.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from tests.contract.conftest import build_or_skip, params, pending

if TYPE_CHECKING:
    from dispute_agent.kernel.ports import EvidenceSource

ADAPTERS = [
    pending("synthetic_shipments", phase="Phase 2 (card P2-04)"),
]


@pytest.fixture(params=params(ADAPTERS))
def source(request: pytest.FixtureRequest) -> EvidenceSource:
    return cast("EvidenceSource", build_or_skip(request.param))


class TestFetching:
    def test_it_names_itself(self, source: EvidenceSource) -> None:
        assert isinstance(source.name, str)

    def test_a_known_requirement_comes_back_found_with_a_value(
        self, source: EvidenceSource
    ) -> None: ...

    def test_an_absent_requirement_comes_back_missing_with_no_value(
        self, source: EvidenceSource
    ) -> None: ...

    def test_a_timeout_comes_back_failed_with_a_code_not_missing(
        self, source: EvidenceSource
    ) -> None:
        """ "We could not find out" is not "there is nothing there"."""

    def test_only_module_unavailable_escapes_as_an_exception(self, source: EvidenceSource) -> None:
        """Every other failure is a FAILED result, so a flaky source degrades the evidence
        rather than crashing the case."""


class TestTrust:
    def test_free_text_inside_a_value_comes_back_as_untrusted_text(
        self, source: EvidenceSource
    ) -> None:
        """I5: a shipment note is written by a person, wherever it is stored."""

    def test_numbers_come_back_as_decimals(self, source: EvidenceSource) -> None:
        """I9: these feed the calculator."""


class TestDeterminism:
    def test_the_same_seed_gives_the_same_evidence(self, source: EvidenceSource) -> None:
        """I11 and CLAUDE.md section 10: synthetic data is reproducible, or a failing
        scenario cannot be re-run."""
