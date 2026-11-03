"""Contract: TicketingSystem (kernel-interfaces.md 6.4, integrations.md 2).

The asymmetry is the thing to get right. `find_issue` is best effort: a hit is reliable, a
miss is not proof of absence (integrations.md 2.2), so a miss alone must never let the
executor write again. A fake that answers definitively both ways would hide the only
behaviour that makes this port different from the others.

The second asymmetry is `idempotent_writes = False`. Jira's create endpoint takes no
idempotency key, so a repeat really does make a second issue. The suite does not pretend
otherwise — it checks that the adapter *says so*, because the executor reads that flag to
decide whether a retry is safe at all. An adapter that claimed True here would be lying in
the one place the lie causes a duplicate.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import pytest

from dispute_agent.kernel.domain.errors import (
    AmbiguousOutcome,
    AuthPortError,
    ExternalNotFound,
    PermanentPortError,
    TransientPortError,
)
from dispute_agent.kernel.domain.external import IssueRef, IssueSpec, OpsFinding
from dispute_agent.kernel.domain.ids import CaseId, IdempotencyKey
from dispute_agent.kernel.domain.values import UntrustedText
from dispute_agent.modules.ticketing.fake.ticketing import (
    SEARCH_CONSISTENCY_DELAY,
    FakeTicketingSystem,
)
from tests.contract.conftest import build_or_skip, params, ready
from tests.contract.harness import UnsupportedError, arrange

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
LATER = NOW + SEARCH_CONSISTENCY_DELAY + timedelta(seconds=1)
CASE = CaseId("case_contract_ticketing")
PROJECT = "WD"

KEY = IdempotencyKey("wd1:" + "c" * 64)
OTHER_KEY = IdempotencyKey("wd1:" + "d" * 64)

#: Awkward on purpose: CRLF and a blank-line run, so a round trip that silently reflows the
#: description is visible rather than assumed away.
DESCRIPTION = "Please remeasure SHP-000001.\r\n\r\n\r\nDeclared 20 x 15 x 10 cm.   "


def a_spec(summary: str = "Remeasure SHP-000001") -> IssueSpec:
    return IssueSpec(
        project_key=PROJECT,
        summary=summary,
        description=DESCRIPTION,
        labels=("dispute-agent",),
        case_id=CASE,
    )


def a_finding(at: datetime = NOW) -> OpsFinding:
    return OpsFinding(
        issue_key=f"{PROJECT}-1",
        finding_id="find-1",
        kind="remeasured",
        measured={"length_cm": Decimal("22"), "dead_grams": 1100},
        note=UntrustedText.create("Box was crushed.", "jira_comment", "find-1"),
        at=at,
    )


class FakeTicketHarness:
    """Implements TicketHarness for the in-memory fake."""

    name = "fake"

    def system(self, **conditions: str) -> Any:
        return FakeTicketingSystem(project_key=PROJECT, faults=dict(conditions), now=NOW)

    def at(self, system: Any, moment: datetime) -> None:
        system.now = moment

    def issue_count(self, system: Any) -> int:
        return cast("int", system.issue_count())


class JiraHarness:
    """The real adapter against the owner's test project.

    Runs only under `make test-integration`, and only when the credentials exist. It cannot
    arrange a fault or move a clock, so those cases skip with a reason - which is honest.
    A harness that faked them would be asserting things about a fake while claiming to test
    Jira.
    """

    name = "jira_cloud"

    def system(self, **conditions: str) -> Any:
        if conditions:
            raise UnsupportedError(f"a real Jira cannot be told to {sorted(conditions.values())}")
        from dispute_agent.infra.settings import Settings
        from dispute_agent.modules.ticketing.jira_cloud.ticketing import JiraCloudTicketing

        settings = Settings()
        token = settings.jira_api_token
        if not (settings.jira_base_url and settings.jira_email and token):
            raise UnsupportedError(
                "JIRA_BASE_URL, JIRA_EMAIL and JIRA_API_TOKEN are not set; see "
                "docs/SETUP_CHANNELS.md"
            )
        return JiraCloudTicketing(
            base_url=settings.jira_base_url,
            email=settings.jira_email,
            api_token=token.get_secret_value(),
            project_key=settings.jira_project_key or "WD",
            timeout_s=settings.http_timeout_s,
        )

    def at(self, system: Any, moment: datetime) -> None:
        raise UnsupportedError("a real Jira has its own clock and its own index lag")

    def issue_count(self, system: Any) -> int:
        raise UnsupportedError("counting issues would depend on the project history")


ADAPTERS = [
    ready("fake", FakeTicketHarness),
    ready("jira_cloud", JiraHarness, integration=True),
]


@pytest.fixture(params=params(ADAPTERS))
def harness(request: pytest.FixtureRequest) -> Any:
    return build_or_skip(request.param)


@pytest.fixture
def ticketing(harness: Any) -> Any:
    return arrange(harness.system)


class TestCapabilities:
    def test_it_declares_its_capabilities(self, ticketing: Any) -> None:
        assert isinstance(ticketing.idempotent_writes, bool)
        assert isinstance(ticketing.lookup_supported, bool)

    def test_it_does_not_claim_idempotent_writes_it_cannot_deliver(
        self, harness: Any, ticketing: Any
    ) -> None:
        """The flag has to be earned. If an adapter says True, calling twice must really
        leave one issue; if it says False, the executor will never retry a create blind.
        Either answer is fine. Claiming True and producing two is the failure."""
        ticketing.upsert_issue(a_spec(), KEY)
        try:
            ticketing.upsert_issue(a_spec(), KEY)
        except AmbiguousOutcome:
            pytest.skip("this adapter refuses a repeat rather than answering")
        count = arrange(harness.issue_count, ticketing)
        if ticketing.idempotent_writes:
            assert count == 1, "claims idempotent writes but created a second issue"
        else:
            assert count == 2, "says writes are not idempotent, so a repeat must show"


class TestUpsert:
    def test_an_upsert_returns_a_reference(self, ticketing: Any) -> None:
        ref = ticketing.upsert_issue(a_spec(), KEY)
        assert ref.key
        assert ref.id

    def test_different_keys_make_different_issues(self, harness: Any, ticketing: Any) -> None:
        ticketing.upsert_issue(a_spec(), KEY)
        ticketing.upsert_issue(a_spec("Another"), OTHER_KEY)
        assert arrange(harness.issue_count, ticketing) == 2

    def test_a_refusal_raises_permanent_and_is_never_a_timeout(self, harness: Any) -> None:
        """Scenario S12c. A 400 from Jira is an argument we got wrong; retrying it just
        gets the same answer more slowly."""
        system = arrange(harness.system, upsert_issue="permanent")
        with pytest.raises(PermanentPortError) as refused:
            system.upsert_issue(a_spec(), KEY)
        assert not isinstance(refused.value, TransientPortError)

    def test_bad_credentials_raise_auth(self, harness: Any) -> None:
        system = arrange(harness.system, upsert_issue="auth")
        with pytest.raises(AuthPortError):
            system.upsert_issue(a_spec(), KEY)

    def test_an_issue_created_but_not_marked_is_ambiguous_and_names_itself(
        self, harness: Any
    ) -> None:
        """integrations.md 2.2: the issue exists and only the marker is missing. Reporting
        failure would invite a second create; reporting success would lose the issue. The
        key travels with the error so a person can go and look at it."""
        system = arrange(harness.system, upsert_issue="marker_fails")
        with pytest.raises(AmbiguousOutcome) as ambiguous:
            system.upsert_issue(a_spec(), KEY)
        assert ambiguous.value.external_ref, "a person needs the issue key"
        assert arrange(harness.issue_count, system) == 1


class TestLookup:
    def test_a_hit_is_reliable(self, harness: Any, ticketing: Any) -> None:
        created = ticketing.upsert_issue(a_spec(), KEY)
        arrange(harness.at, ticketing, LATER)
        found = ticketing.find_issue(KEY)
        assert found is not None
        assert found.key == created.key

    def test_a_miss_is_not_proof_of_absence(self, harness: Any, ticketing: Any) -> None:
        """integrations.md 2.2: search indexing lags, so a miss means "do not know".

        The issue is created and the search cannot see it yet. Any code that reads this
        None as "nothing is there, safe to create" will produce a duplicate the moment
        indexing is slow, which is the failure ADR 0020 exists to prevent.
        """
        ticketing.upsert_issue(a_spec(), KEY)
        assert ticketing.find_issue(KEY) is None
        assert arrange(harness.issue_count, ticketing) == 1

        arrange(harness.at, ticketing, LATER)
        assert ticketing.find_issue(KEY) is not None

    def test_an_unused_key_also_misses(self, ticketing: Any) -> None:
        """Which is exactly why a miss settles nothing: it looks identical to the case
        above, and the adapter cannot tell them apart."""
        assert ticketing.find_issue(OTHER_KEY) is None


class TestReadBack:
    def test_get_issue_returns_plain_text_from_the_stored_adf(
        self, harness: Any, ticketing: Any
    ) -> None:
        """integrations.md 2.3: normalised, so the read-back comparison is like for like.

        Jira may restructure the document on storage, so the comparison is on extracted
        text rather than on the ADF JSON, and both sides are normalised.
        """
        from dispute_agent.modules.ticketing.fake.ticketing import normalise

        ref = ticketing.upsert_issue(a_spec(), KEY)
        snapshot = ticketing.get_issue(ref)
        assert normalise(snapshot.description_text) == normalise(DESCRIPTION)
        assert "\r" not in snapshot.description_text

    def test_get_issue_returns_what_was_asked_for(self, ticketing: Any) -> None:
        """Checked against the stored intent, which is what the executor compares (I2)."""
        spec = a_spec()
        snapshot = ticketing.get_issue(ticketing.upsert_issue(spec, KEY))
        assert snapshot.summary == spec.summary
        assert snapshot.project_key == spec.project_key

    def test_get_issue_carries_the_idempotency_key_back(self, ticketing: Any) -> None:
        """The marker is what proves this issue is ours rather than one that happens to
        look like it."""
        snapshot = ticketing.get_issue(ticketing.upsert_issue(a_spec(), KEY))
        assert snapshot.idempotency_key == KEY

    def test_the_agent_label_is_present(self, ticketing: Any) -> None:
        snapshot = ticketing.get_issue(ticketing.upsert_issue(a_spec(), KEY))
        assert "dispute-agent" in snapshot.labels

    def test_an_issue_that_does_not_exist_raises_external_not_found(self, ticketing: Any) -> None:
        with pytest.raises(ExternalNotFound):
            ticketing.get_issue(IssueRef(key=f"{PROJECT}-9999", id="0"))


class TestFindings:
    def test_fetch_findings_returns_untrusted_notes(self, ticketing: Any) -> None:
        """I5: an ops note is written by a person at a hub, so it is outside text."""
        if not hasattr(ticketing, "post_finding"):
            pytest.skip("this adapter cannot arrange a finding")
        ticketing.post_finding(a_finding(NOW + timedelta(minutes=1)))
        findings = ticketing.fetch_findings(NOW)
        assert len(findings) == 1
        assert isinstance(findings[0].note, UntrustedText)

    def test_measurements_come_back_as_decimals_not_floats(self, ticketing: Any) -> None:
        """I9: a remeasurement feeds the calculator, and a float there is a seller's money
        rounded by binary arithmetic."""
        if not hasattr(ticketing, "post_finding"):
            pytest.skip("this adapter cannot arrange a finding")
        ticketing.post_finding(a_finding(NOW + timedelta(minutes=1)))
        measured = ticketing.fetch_findings(NOW)[0].measured
        for value in measured.values():
            assert not isinstance(value, float)
        assert isinstance(measured["length_cm"], Decimal)

    def test_a_transient_read_failure_is_transient(self, harness: Any) -> None:
        system = arrange(harness.system, fetch_findings="transient")
        with pytest.raises(TransientPortError):
            system.fetch_findings(NOW)
