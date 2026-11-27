"""Audit AUD-13: a reviewer sees what the investigation looked up and what its findings cite."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from dispute_agent.ui import data

if TYPE_CHECKING:
    from pathlib import Path


def test_one_case_s_turns_in_order_with_the_reference_each_finding_cites(tmp_path: Path) -> None:
    log = tmp_path / "turns.jsonl"
    turns = [
        {
            "case_id": "case_A",
            "gather_round": 1,
            "step": "search_past_shipments",
            "ref": "T1.1",
            "arguments": '{"packaging_id": "any"}',
            "tool_ok": True,
            "reused": False,
            "result": '{"ok": true}',
            "input_tokens": 900,
        },
        {"case_id": "case_B", "gather_round": 1, "step": "decide", "ref": ""},
        {"case_id": "case_A", "gather_round": 1, "step": "decide", "ref": "", "tool_ok": None},
    ]
    log.write_text(
        "\n".join(json.dumps(t) for t in turns) + '\n{"case_id": "case_A", "cut sho', "utf-8"
    )
    shown = data.investigation("case_A", log)
    assert [(t["ref"], t["step"]) for t in shown] == [
        ("T1.1", "search_past_shipments"),
        ("", "decide"),
    ]
    assert "input_tokens" not in shown[0]  # what a reviewer needs, not the meter
    assert data.investigation("case_A", tmp_path / "none.jsonl") == []
