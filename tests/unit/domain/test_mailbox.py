"""ADR 0053: one Gmail mailbox, however its address is written; everyone else exactly."""

from __future__ import annotations

import pytest

from dispute_agent.kernel.domain.mailbox import same_mailbox


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("name+seller@gmail.com", "name@gmail.com"),  # the test seller replying from Gmail
        ("Name@Gmail.com", "name@gmail.com"),
        ("n.a.m.e@gmail.com", "name@gmail.com"),
        ("name+seller@googlemail.com", "name@gmail.com"),
        ("seller@example.invalid", " SELLER@example.invalid "),
    ],
)
def test_the_same_mailbox(a: str, b: str) -> None:
    assert same_mailbox(a, b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("name@gmail.com", "other@gmail.com"),
        ("name+seller@example.com", "name@example.com"),  # not Gmail: the plus part counts
        ("n.ame@example.com", "name@example.com"),  # not Gmail: the dot counts
        ("name@gmail.com.evil.test", "name@gmail.com"),
        ("", ""),
        ("name@gmail.com", ""),
    ],
)
def test_different_mailboxes(a: str, b: str) -> None:
    assert not same_mailbox(a, b)
