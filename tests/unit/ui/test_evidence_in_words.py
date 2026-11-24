"""The AI's reasoning names evidence in words, and the exact recorded text stays one click away."""

from __future__ import annotations

from dispute_agent.ui.review_view import recommends
from dispute_agent.ui.trail import Recommendation, plain

HUB = "evi_01M87E5XWR3N7ANYWGNQ61TTYY"
DECLARED = "evi_01M87E5XWR3N7ANYWGNQ61TTYX"
NAMES = {HUB: "hub measurement", DECLARED: "declared size and weight"}
SAID = f"The measured box ({HUB}) is far larger than the declared box ({DECLARED})."


def test_each_id_becomes_what_it_is() -> None:
    assert plain(SAID, NAMES) == (
        "The measured box (hub measurement) is far larger than the declared box "
        "(declared size and weight)."
    )


def test_an_id_the_case_does_not_hold_is_said_so_not_named() -> None:
    made_up = "evi_01ZZZZZZZZZZZZZZZZZZZZZZZZ"
    assert plain(f"see {made_up}", NAMES) == "see an evidence id not in this case"


def test_text_without_ids_is_left_exactly_as_recorded() -> None:
    rules = "B7: measured size exceeds declared by more than 5%"
    assert plain(rules, NAMES) == rules
    shown = recommends(Recommendation("Ask the seller", "high", "Rules engine", rules), NAMES)
    assert "Reasoning, exactly as recorded:" in shown


def test_the_review_card_shows_words_first_and_keeps_the_exact_text() -> None:
    rec = Recommendation("Ask the hub to re-measure", "medium", "AI (haiku)", SAID)
    shown = recommends(rec, NAMES)
    assert "Reasoning, with evidence named in words:" in shown
    assert "(hub measurement)" in shown
    assert "<summary>Exactly as recorded</summary>" in shown and HUB in shown
