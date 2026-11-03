"""Card P3-08: how many times this system may write to one person (D7, R04, R05).

The limits exist because an agent that can write without bound can harass someone by
accident, and from the inside that failure looks exactly like normal operation — every
message individually justified, every send verified, nobody ever deciding to do it.

`TestTheCountersAreActuallyGathered` is the test that matters most, and it is here because
the limits were dead when this card started. Both rules read `messages_sent_total` and
`messages_sent_today` off the policy context; nothing anywhere populated either, so they
defaulted to zero and no fourth message was ever refused. A rule that believes it has
checked something is worse than no rule, because the gap does not show up in a test run or
in a log — exactly the shape of the Phase 2 finding about credits.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from dispute_agent.kernel.domain.enums import PolicyStage, VerdictKind
from dispute_agent.kernel.domain.errors import ConfigError
from dispute_agent.kernel.policy.config import ContactConfig, MessagesConfig, PolicyConfig
from dispute_agent.kernel.policy.rules import ContactLimitCase, ContactLimitDaily
from dispute_agent.kernel.policy.verdicts import PolicyContext
from tests.unit.policy.test_rules import CASE, SELLER, credit_intent, message_intent

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
POLICY = PolicyConfig(messages=MessagesConfig(allowed_recipients=frozenset({SELLER})))


def ctx(**overrides: object) -> PolicyContext:
    fields: dict[str, object] = {
        "case": CASE,
        "stage": PolicyStage.PLANNING,
        "intent": message_intent(),
        "policy": POLICY,
        "now": NOW,
    }
    fields.update(overrides)
    return PolicyContext(**fields)


class TestPerCase:
    """D7: three messages about one dispute, then stop."""

    @pytest.mark.parametrize("already", [0, 1, 2])
    def test_it_allows_up_to_the_limit(self, already: int) -> None:
        result = ContactLimitCase().evaluate(ctx(messages_sent_total=already))
        assert result.verdict is VerdictKind.ALLOW

    def test_a_fourth_message_is_denied_with_its_reason(self) -> None:
        result = ContactLimitCase().evaluate(ctx(messages_sent_total=3))
        assert result.verdict is VerdictKind.DENY
        assert result.reason == "contact_limit_case"

    def test_the_limit_comes_from_configuration(self) -> None:
        """A number in the code would be a product decision nobody can change without a
        release."""
        policy = PolicyConfig(contact=ContactConfig(max_messages_per_case=1))
        allowed = ContactLimitCase().evaluate(ctx(policy=policy, messages_sent_total=0))
        denied = ContactLimitCase().evaluate(ctx(policy=policy, messages_sent_total=1))
        assert allowed.verdict is VerdictKind.ALLOW
        assert denied.verdict is VerdictKind.DENY

    def test_it_does_not_apply_to_a_credit(self) -> None:
        result = ContactLimitCase().evaluate(ctx(intent=credit_intent(), messages_sent_total=99))
        assert result.verdict is VerdictKind.ALLOW
        assert result.reason == "not_a_message"


class TestPerDay:
    def test_a_second_message_the_same_day_is_denied(self) -> None:
        result = ContactLimitDaily().evaluate(ctx(messages_sent_today=1))
        assert result.verdict is VerdictKind.DENY
        assert result.reason == "contact_limit_daily"

    def test_the_first_of_the_day_is_allowed(self) -> None:
        assert ContactLimitDaily().evaluate(ctx(messages_sent_today=0)).verdict is (
            VerdictKind.ALLOW
        )

    def test_the_case_limit_and_the_daily_limit_are_separate(self) -> None:
        """Two messages on a case, none today: the case limit is fine and the day is free."""
        context = ctx(messages_sent_total=2, messages_sent_today=0)
        assert ContactLimitCase().evaluate(context).verdict is VerdictKind.ALLOW
        assert ContactLimitDaily().evaluate(context).verdict is VerdictKind.ALLOW


class TestTheCountersAreActuallyGathered:
    """The regression test for the bug this card found.

    Both limits are pure functions of two numbers. If nothing computes those numbers, every
    rule test above still passes and no seller is ever protected. These check the executor
    gathers them the same way it gathers the credit counts.
    """

    def test_the_executor_asks_for_both_counts(self) -> None:
        from tests.unit.executor.test_executor import plan_a_message

        store, _ = plan_a_message(messages_total=0, messages_today=0)
        # The only claim here that can fail: the daily count is bounded by a day. The two
        # refusal tests below are what prove the counts actually reach the rules - an
        # earlier assertion on the caller's own context was 0 whatever the executor did.
        assert store.counted_since is not None, "the daily count must be bounded by a day"

    def test_a_case_over_its_limit_is_refused_at_plan_time(self) -> None:
        """Not merely "the rule would deny it" - the executor refuses to create the action."""
        from dispute_agent.kernel.domain.errors import PolicyRefused
        from tests.unit.executor.test_executor import plan_a_message

        with pytest.raises(PolicyRefused, match="contact_limit_case"):
            plan_a_message(messages_total=3, messages_today=0)

    def test_a_case_already_written_to_today_is_refused(self) -> None:
        from dispute_agent.kernel.domain.errors import PolicyRefused
        from tests.unit.executor.test_executor import plan_a_message

        with pytest.raises(PolicyRefused, match="contact_limit_daily"):
            plan_a_message(messages_total=0, messages_today=1)


# Removed after the Phase 3 safety review (finding 6): `TestARetryIsNotASecondMessage`.
# Both of its tests asked a stub for the number the stub had been configured to return, so
# they asserted 1 == 1 and 2 == 2 while claiming to prove a retry is not double-counted.
# That claim is about the real count over action rows - SENT, VERIFIED and UNKNOWN counted,
# a re-executed row counted once - and it can only be tested against the SQLite ActionStore,
# which does not exist yet. It is listed in BACKLOG.md as the first test that store needs.
# A test that cannot fail is worse than no test, because it reads as coverage.


class TestTheDayBoundary:
    def test_the_day_is_the_sellers_not_the_servers(self) -> None:
        """A machine running in UTC would split an Indian evening in half, and a seller
        would be written to twice in one of their days."""
        from dispute_agent.kernel.executor.executor import _start_of_day

        # 02:00 UTC on 2 March is 07:30 on 2 March in Kolkata, so the day began at 18:30
        # UTC on 1 March - not at midnight UTC.
        moment = datetime(2026, 3, 2, 2, 0, tzinfo=UTC)
        start = _start_of_day(moment, "Asia/Kolkata")
        assert start == datetime(2026, 3, 1, 18, 30, tzinfo=UTC)
        assert start.astimezone(ZoneInfo("Asia/Kolkata")).hour == 0

    def test_a_moment_just_after_local_midnight_starts_a_new_day(self) -> None:
        from dispute_agent.kernel.executor.executor import _start_of_day

        just_after = datetime(2026, 3, 1, 18, 31, tzinfo=UTC)
        just_before = just_after - timedelta(minutes=2)
        assert _start_of_day(just_after, "Asia/Kolkata") != _start_of_day(
            just_before, "Asia/Kolkata"
        )


class TestTheZoneIsCheckedAtStartUp:
    """Fail fast on configuration, fail safe at runtime (CLAUDE.md section 6)."""

    def test_an_unknown_zone_is_refused_when_the_config_loads(self) -> None:
        with pytest.raises(ConfigError, match="day_boundary_tz"):
            ContactConfig(day_boundary_tz="Mars/Olympus_Mons")

    def test_the_shipped_zone_resolves(self) -> None:
        """Which on Windows means the `tzdata` package is installed. It was not, and
        nothing noticed, because nothing had ever resolved the zone."""
        assert ContactConfig().day_boundary_tz == "Asia/Kolkata"
        assert ZoneInfo(ContactConfig().day_boundary_tz) is not None
