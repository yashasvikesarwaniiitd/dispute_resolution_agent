"""`make eval-image`: the image check's live evaluation (ADR 0049). Costs money; opt-in.

Sends each of the 20 evaluation photos once to the Foundry gpt-5-mini deployment through the
guarded gateway (I14: logged, counted against the spending caps), scores the answers against
the answer key, and judges them by `image_check_gates` in config/release_gates.yaml.

Spend is capped by this script as well as by the gateway: it stops before a call once the run
has spent `--cap` US dollars (default and maximum 1.00, the owner's approval for this eval).

It saves the valid answers to `modules/evals/parcel_photos/recorded_v1.json`. Those are what
the `recorded` adapter replays and what the offline test re-scores in `make verify`, so a demo
shows real verdicts with no model call. Photos and prompts are never written to the log.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import yaml

from dispute_agent.app import (
    DEFAULT_CONFIG_DIR,
    PROMPTS_DIR,
    build_model_gateway,
    build_model_policy,
)
from dispute_agent.infra.clock import SystemClock
from dispute_agent.infra.ids import UlidIdGenerator
from dispute_agent.infra.settings import Settings
from dispute_agent.kernel.domain.errors import KernelError
from dispute_agent.kernel.domain.ids import CaseId
from dispute_agent.kernel.domain.image import ImageAttachment, ImageCheckRequest
from dispute_agent.modules.evals.image_check_eval import RECORDED_PATH, judge, load_key, score
from dispute_agent.modules.image_check.vision import VisionImageChecker
from dispute_agent.modules.reasoning.prompt_registry import FilePromptRegistry

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.reasoning import ModelRequest, ModelResponse
    from dispute_agent.kernel.ports import ModelGateway

APPROVED_CAP = Decimal("1.00")


class OverCapError(KernelError):
    code = "eval_spend_cap"


@dataclass
class Capped:
    """The guarded gateway, refusing a call once this run has spent its cap."""

    inner: ModelGateway
    cap: Decimal
    spent: Decimal = Decimal(0)
    calls: int = 0
    latencies: list[int] = field(default_factory=list)

    def complete(self, req: ModelRequest) -> ModelResponse:
        if self.spent >= self.cap:
            raise OverCapError(f"the eval has spent US${self.spent:.4f} of its US${self.cap} cap")
        response = self.inner.complete(req)
        self.calls += 1
        self.spent += Decimal(str(response.usage.cost_usd))
        self.latencies.append(int(response.usage.latency_ms))
        return response


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cap", type=Decimal, default=APPROVED_CAP)
    args = parser.parse_args()
    cap = min(args.cap, APPROVED_CAP)

    settings = Settings()
    policy = build_model_policy(DEFAULT_CONFIG_DIR, settings)
    guarded = build_model_gateway(
        "foundry_openai",
        settings=settings,
        policy=policy,
        clock=SystemClock(),
        ids=UlidIdGenerator(),
    )
    gateway = Capped(guarded, cap)
    # The eval is how an inactive prompt earns `active`, so it may load one (prompts/README).
    checker = VisionImageChecker(gateway, FilePromptRegistry(PROMPTS_DIR, allow_inactive=True))
    photos, declared, measured = load_key()

    answers: dict[str, dict[str, Any] | None] = {}
    for photo in photos:
        request = ImageCheckRequest(
            case_id=CaseId(f"eval-{photo.ref.lower()}"),
            shipment_ref=photo.ref,
            photo=ImageAttachment(media_type="image/jpeg", data=photo.path.read_bytes()),
            declared=declared,
            measured=measured,
        )
        try:
            got = checker.check(request).model_dump(mode="json")
            answers[photo.ref] = got
            print(
                f"{photo.ref:<13} {got['parcel_count']:<14} {got['packaging']:<12} "
                f"{got['size_from_photo']}"
            )
        except OverCapError as stopped:
            print(f"stopped: {stopped}")
            break
        except KernelError as failed:
            answers[photo.ref] = None
            print(f"{photo.ref:<13} no valid answer ({failed.code})")

    report = score(photos, answers)
    gates = yaml.safe_load((DEFAULT_CONFIG_DIR / "release_gates.yaml").read_text("utf-8"))
    results = judge(report, gates["image_check_gates"])
    print(f"\n{gateway.calls} calls, US${gateway.spent:.4f} spent (cap US${cap}).")
    if gateway.latencies:
        print(f"slowest call {max(gateway.latencies)} ms")
    for name, passed, rule in results:
        print(f"{'PASS' if passed else 'FAIL'}  {name}: {rule}")
    for miss in report.misses:
        print(f"  miss  {miss}")

    valid = {ref: answer for ref, answer in answers.items() if answer is not None}
    RECORDED_PATH.write_text(json.dumps(valid, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"saved {len(valid)} answers to {RECORDED_PATH.name}")
    return 0 if all(passed for _, passed, _ in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
