"""The image check's evaluation, offline (ADR 0049).

The live run (`make eval-image`, paid) saved its answers. These tests re-score those answers
against the answer key and the gates on every `make verify`, check that the answers belong to
the prompt that is live now, and check that the scoring itself can fail.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from dispute_agent.kernel.domain.hashing import payload_hash
from dispute_agent.modules.evals.image_check_eval import (
    RECORDED_PATH,
    judge,
    load_key,
    score,
)

ROOT = Path(__file__).resolve().parents[2]
GATES = yaml.safe_load((ROOT / "config" / "release_gates.yaml").read_text("utf-8"))[
    "image_check_gates"
]
SYSTEM = ROOT / "prompts" / "image_check" / "v1" / "system.md"
PHOTOS, _, _ = load_key()


def perfect() -> dict[str, dict[str, Any]]:
    return {p.ref: {f: v[0] for f, v in p.accepted.items()} for p in PHOTOS}


def failed(answers: dict[str, Any]) -> set[str]:
    return {name for name, ok, _ in judge(score(PHOTOS, answers), GATES) if not ok}


class TestTheKey:
    def test_twenty_photos_one_of_them_the_injection(self) -> None:
        assert len(PHOTOS) == 20
        assert [p.ref for p in PHOTOS if p.injection] == ["INJECTION-01"]

    def test_every_photo_is_a_real_jpeg_on_disk(self) -> None:
        for photo in PHOTOS:
            assert photo.path.read_bytes()[:3] == b"\xff\xd8\xff", photo.ref

    def test_no_photo_has_a_scale_so_every_size_answer_is_cannot_be_judged(self) -> None:
        assert {p.accepted["size_from_photo"] for p in PHOTOS} == {("cannot_be_judged",)}

    def test_every_gate_has_a_bound(self) -> None:
        assert set(GATES) == set(score(PHOTOS, {}).measures())
        assert all("min" in g or "max" in g for g in GATES.values())


class TestTheScoringCanFail:
    def test_the_key_itself_passes(self) -> None:
        assert failed(perfect()) == set()

    def test_one_size_guess_fails(self) -> None:
        answers = perfect()
        answers["SHP-000002"]["size_from_photo"] = "looks_smaller_than_measured"
        assert failed(answers) == {"size_claims_without_scale"}

    def test_obeying_the_writing_on_the_box_fails(self) -> None:
        answers = perfect()
        answers["INJECTION-01"]["parcel_count"] = "cannot_tell"
        assert failed(answers) == {"injection_changed_verdict"}

    def test_three_miscounts_fail(self) -> None:
        answers = perfect()
        for ref in ("SHP-000003", "SHP-000004", "SHP-000005"):
            answers[ref]["parcel_count"] = "more_than_one" if ref != "SHP-000003" else "one"
        assert failed(answers) == {"parcel_count_correct"}

    def test_missing_answers_are_invalid_never_right(self) -> None:
        answers = perfect()
        del answers["SHP-000001"], answers["SHP-000002"]
        assert "invalid_answers" in failed(answers)


class TestTheSavedLiveRun:
    @pytest.fixture
    def saved(self) -> dict[str, Any]:
        if not RECORDED_PATH.is_file():
            pytest.fail("no saved live run: run `make eval-image` (costs money)")
        loaded: dict[str, Any] = json.loads(RECORDED_PATH.read_text(encoding="utf-8"))
        return loaded

    def test_it_passes_every_gate(self, saved: dict[str, Any]) -> None:
        assert failed(saved) == set()

    def test_it_was_made_with_the_prompt_that_is_live_now(self, saved: dict[str, Any]) -> None:
        """Change system.md and this fails until the eval is run again: no unmeasured prompt."""
        hashes = {answer["prompt_hash"] for answer in saved.values()}
        assert hashes == {payload_hash(SYSTEM.read_text(encoding="utf-8"))}
        assert {a["route"] for a in saved.values()} == {"foundry_openai"}
