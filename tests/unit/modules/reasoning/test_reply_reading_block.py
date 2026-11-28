"""Audit AUD-05: the seller reply's reading reaches v4's own `untrusted_reply_reading` block.

Scripted replays through the real station, reply reader and investigator (no model, no cost):
the seller replies in a later round, the scripted reply reader reads it, and the investigator's
prompt for that round is captured. The reading appears once, in its own block, marked
untrusted, with its evidence id still citable; the released v3 prompt is unchanged.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from dispute_agent.api import scenario_runner as runner
from dispute_agent.api.decision_quality import at_round
from dispute_agent.app import build_investigation_models, build_scenario_models
from dispute_agent.kernel.orchestrator.toolbox import Toolbox
from dispute_agent.modules.evals.scenarios.answers import investigator as draft_for
from dispute_agent.modules.evals.scenarios.catalogue import Scenario, script

FIXTURE = "kurta_box_larger"  # the approved scenarios' seller-reply fixture (S02, S09a, S34)
ASKED = ["dispute_raised@0h", "approve(evidence_request)@1h"]


def prompts_for(steps: list[str], asks: int = 1, engine: str = "investigator") -> list[str]:
    """Every investigator prompt, in order. The scripted investigator asks the seller for
    evidence in its first `asks` rounds (as the approved scenarios' recorded answers do), then
    escalates; the approvals and replies in `steps` follow those requests."""
    seen: list[str] = []

    def oracle(answer: Any) -> Any:
        def respond(req: Any) -> Any:
            if req.prompt.name != "case_investigator_agent":
                return answer(req)  # the scripted reply reader
            seen.append(
                req.prompt.messages[-1].content
            )  # the case (the system prompt names the block)
            action = "request_seller_evidence" if len(seen) <= asks else "escalate_to_human"
            draft = draft_for(action, FIXTURE)
            if engine == "agent":
                return {"step": "decide", "argument": "", "draft": draft}
            return {"step": "decide", "arguments": {}, "draft": draft}

        return respond

    def factory(answer: Any, policy: Any, clock: Any, ids: Any) -> Any:
        if engine == "agent":  # v3: the engine REASONING_ENGINE selects (set by the test)
            return build_scenario_models(oracle(answer), policy, clock, ids, engine=engine)
        return build_investigation_models(
            oracle(answer),
            policy,
            clock,
            ids,
            engine=engine,
            tools=Toolbox(()),
            record=lambda t: None,
        )

    scenario = Scenario(
        id="AUD-05",
        title="reply reading",
        phase=5,
        fixture=FIXTURE,
        config={},
        faults={},
        steps=script(steps),
        expect={},
    )
    runner._run(scenario, None, at_round(factory, 1)).world.close()
    return seen


def block(prompt: str) -> str:
    found = re.search(r"<untrusted_reply_reading>\n(.*?)\n</untrusted_reply_reading>", prompt, re.S)
    assert found, "no reply-reading block"
    return found.group(1)


def evidence_id(reading: str) -> str:
    found = re.search(r'"evidence_id": "([^"]+)"', reading)
    assert found
    return found.group(1)


@pytest.mark.parametrize(
    ("label", "intent", "status"),
    [
        ("photo_shows_large_box", "provides_requested_evidence", "provided"),  # complete
        ("partial", "partial_evidence", "partial"),
        ("contradictory_dims", "partial_evidence", "partial"),
    ],
)
def test_a_reply_reading_is_shown_once_in_its_own_untrusted_block(
    label: str, intent: str, status: str
) -> None:
    prompts = prompts_for([*ASKED, f"seller_reply({label})@10h"])
    assert len(prompts) == 2 and "<untrusted_reply_reading>" not in prompts[0]  # no reply yet
    prompt = prompts[-1]
    reading = block(prompt)
    assert f'"intent": "{intent}"' in reading and f'"status": "{status}"' in reading
    assert "model_id" not in reading and "prompt_hash" not in reading  # the reading, not its maker
    ref = evidence_id(reading)
    # Once: not also listed or rendered among the untrusted evidence (AUD-05's duplicate).
    assert f'<untrusted_evidence id="{ref}"' not in prompt
    assert f"- id: {ref} |" not in prompt
    # Still citable: code accepts it, and the prompt lists it.
    citable = re.search(r"<citable_ids>\n(.*?)\n", prompt)
    assert citable and ref in citable.group(1)


def test_a_manipulation_attempt_keeps_its_flags_in_the_untrusted_block() -> None:
    prompts = prompts_for([*ASKED, "seller_reply(injection_corpus_item_1)@10h"])[1:]
    assert prompts  # the flagged reply still reaches the investigator, as outside text
    reading = block(prompts[-1])
    assert '"intent": "contains_instruction_attempt"' in reading
    assert '"manipulation_flags": ["instruction_like"]' in reading
    assert "<untrusted_reply_reading>" in prompts[-1]  # outside text, labelled so


def test_a_later_round_shows_the_latest_reply_not_an_earlier_one() -> None:
    steps = [
        *ASKED,
        "seller_reply(irrelevant)@10h",
        "approve(clarification_request)@11h",
        "seller_reply(contradictory_dims)@20h",
    ]
    prompts = prompts_for(steps, asks=2)
    assert len(prompts) == 3
    assert '"intent": "irrelevant"' in block(prompts[1])  # round 2 shows round 2's reply
    prompt = prompts[-1]
    reading = block(prompt)
    assert '"intent": "partial_evidence"' in reading  # the contradictory reply of round 3
    assert '"intent": "irrelevant"' not in prompt  # round 2's reading is not carried in


def test_the_released_v3_prompt_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """v3 is the evaluated default engine: its block stays empty and the reading stays among
    the untrusted evidence, exactly as before."""
    monkeypatch.setenv("REASONING_ENGINE", "agent")
    prompts = prompts_for([*ASKED, "seller_reply(photo_shows_large_box)@10h"], engine="agent")
    assert len(prompts) == 2
    prompt = prompts[-1]
    assert "<untrusted_reply_reading>" not in prompt
    assert re.search(r'<untrusted_evidence id="[^"]+" source="seller_email">', prompt)
    json.dumps(prompt)  # rendered text only
