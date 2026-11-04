"""`make foundry-spend`: estimated spend per route against its cap, and the credit window (P4b-08).

Reads the model-call log only. Warns at 50% and 80% of each cap. The figures are the app's
own estimate (reported tokens times config/pricing.yaml), which the spending caps use; the
Azure portal's figure lags by up to a day and is the bill.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from dispute_agent.app import DEFAULT_CONFIG_DIR, MODEL_CALL_LOG, build_model_policy
from dispute_agent.infra.settings import Settings

if TYPE_CHECKING:
    from decimal import Decimal

ROUTES = ("foundry_agent_service", "foundry_openai", "anthropic_direct", "fake")


def report(spent: dict[str, Decimal], caps: dict[str, Decimal]) -> list[str]:
    lines = []
    for route in ROUTES:
        amount = spent.get(route)
        if amount is None:
            continue
        cap = caps.get(route)
        if cap is None:
            lines.append(f"  {route:24} ${amount:.4f}  (no cap: test route)")
            continue
        share = amount / cap * 100
        warn = (
            "  << over 80% of cap" if share >= 80 else "  < over 50% of cap" if share >= 50 else ""
        )
        lines.append(f"  {route:24} ${amount:.4f} of ${cap}  ({share:.1f}%){warn}")
    return lines


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    settings = Settings()
    policy = build_model_policy(DEFAULT_CONFIG_DIR, settings)
    if not MODEL_CALL_LOG.is_file():
        print("No model calls recorded yet.")
        return 0
    from dispute_agent.infra.sqlite.model_call_log import open_model_call_log

    log = open_model_call_log(MODEL_CALL_LOG)
    spent = {route: log.spend_usd(route) for route in ROUTES}
    spent = {route: amount for route, amount in spent.items() if amount > 0}
    caps = {
        "foundry_agent_service": policy.llm_primary.spend_cap_usd,
        "foundry_openai": policy.llm_primary.spend_cap_usd,
        "anthropic_direct": policy.llm_secondary.spend_cap_usd,
    }
    print("Estimated model spend (app's own estimate; the Azure bill lags up to a day)\n")
    print("\n".join(report(spent, caps)) or "  nothing spent yet")
    expires = policy.llm_primary.credit_expires_on
    if expires is not None:
        days = (expires - datetime.now(UTC).date()).days
        state = "ENDED" if days < 0 else f"{days} days left"
        paid = "ALLOWED" if policy.llm_primary.allow_paid_usage else "off"
        print(f"\nAzure credit window: ends {expires} ({state}); paid usage {paid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
