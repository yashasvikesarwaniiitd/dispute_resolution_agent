"""ADR 0053: one Gmail mailbox, however its address is written; everyone else exactly.
ADR 0055: a reply is read without the quoted email below it."""

from __future__ import annotations

import pytest

from dispute_agent.kernel.domain.mailbox import new_text, reply_only, same_mailbox
from dispute_agent.kernel.domain.values import UntrustedText


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


# -- ADR 0055: only the seller's own words are read ------------------------------------------

OWN = "Here is the photo of the packed box beside a tape measure. It is 20 x 15 x 10 cm."
#: Gmail's plain-text reply, header wrapped the way Gmail wraps a long name and address.
GMAIL = (
    f"{OWN}\r\n\r\nOn Sun, 23 Nov 2026 at 11:10, Dispute desk <\r\n"
    "dispute.desk+agent@gmail.com> wrote:\r\n\r\n> Hello,\r\n>\r\n"
    "> Please reply to this email with a photo of the packed box.\r\n"
)


@pytest.mark.parametrize(
    "body",
    [
        GMAIL,
        f"{OWN}\n\nOn Sun, 23 Nov 2026 at 11:10, Desk <desk@gmail.com> wrote:\n> Hello",
        f"{OWN}\n\n-----Original Message-----\nFrom: Desk <desk@example.com>\nSent: today",
        f"{OWN}\n\nFrom: Dispute desk <desk@example.com>\nSent: Sunday\nSubject: SHP-000001",
        f"{OWN}\n> Please reply with a photo.",
    ],
)
def test_the_quoted_email_is_left_out(body: str) -> None:
    assert new_text(body) == OWN


@pytest.mark.parametrize(
    "body",
    [
        f"From: our Pune warehouse\n{OWN}",  # a seller's own "From:", with no address
        f"{OWN}\nOn Monday we packed it again.",  # "On ..." with no "wrote:"
        OWN,
    ],
)
def test_the_seller_s_own_words_are_kept(body: str) -> None:
    assert new_text(body) == body.strip()


def test_a_quote_with_nothing_above_it_is_kept_whole() -> None:
    assert new_text("> just a quote") == "> just a quote"


def test_reply_only_keeps_an_unquoted_body_as_it_was() -> None:
    body = UntrustedText.create(OWN, "seller_email", "m1")
    assert reply_only(body) is body


def test_reply_only_reads_complete_when_the_cut_fell_in_the_quote() -> None:
    body = UntrustedText.create(GMAIL + "> more\n" * 5000, "seller_email", "m1")
    assert body.truncated  # the cap cut the quoted part
    only = reply_only(body)
    assert only.text == OWN
    assert not only.truncated
    assert (only.origin, only.source_ref) == ("seller_email", "m1")


def test_a_gmail_reply_no_longer_trips_the_input_screen() -> None:
    # The live fault: the "<address> wrote:" line read as markup, so instruction_like.
    from dispute_agent.modules.input_screen.rules_based import RulesBasedInputScreen

    assert "instruction_like" in RulesBasedInputScreen.flags_for(GMAIL)
    assert RulesBasedInputScreen.flags_for(new_text(GMAIL)) == set()
