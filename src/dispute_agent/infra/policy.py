"""Load `config/policy.yaml` into the frozen `PolicyConfig` (config.md section 3).

The kernel reads no files, so this is where the disk is touched; `kernel/policy/config.py`
owns what the document means.

Phase 1 shipped the file and never read it, which the safety review caught. The defaults
happened to be stricter than the file, so nothing was unsafe — but `rules_version` was
hashing the defaults, which made policy.yaml's own promise ("every change here changes
rules_version") false, and the allowlist had two sources that could disagree.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

from dispute_agent.kernel.domain.errors import ConfigError
from dispute_agent.kernel.policy.config import PolicyConfig

if TYPE_CHECKING:
    from datetime import date
    from decimal import Decimal
    from pathlib import Path


def load_policy(config_dir: Path, *, allowed_recipients: tuple[str, ...] = ()) -> PolicyConfig:
    """Parse the file, with the recipient allowlist resolved from settings.

    `messages.allowed_recipients` is deliberately empty in the file: the real addresses come
    from `TEST_SELLER_EMAIL`, so a checked-in config can never name a mailbox (I11). They are
    merged here, which is also the one place the two readers of the allowlist agree.
    """
    path = config_dir / "policy.yaml"
    if not path.is_file():
        raise ConfigError(f"no policy.yaml at {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    # Read by `pattern_call_limit`, not by the kernel's LlmConfig (config.md section 3).
    llm = dict(raw.get("llm") or {})
    llm.pop("max_calls_per_case_with_patterns", None)
    raw["llm"] = llm
    messages = dict(raw.get("messages") or {})
    merged = {*(messages.get("allowed_recipients") or []), *allowed_recipients}
    messages["allowed_recipients"] = sorted(entry for entry in merged if entry.strip())
    raw["messages"] = messages
    return PolicyConfig.model_validate(raw)


def with_llm_overrides(
    policy: PolicyConfig,
    *,
    credit_expires_on: date | None,
    allow_paid_usage: bool,
    spend_cap_usd: Decimal | None,
    secondary_cap_usd: Decimal | None = None,
) -> PolicyConfig:
    """Apply the environment's spending settings (config.md section 7).

    Applied before anything reads `rules_version`, so every verdict records the effective
    values. The cap may only come from the environment *lower* or higher - it is the
    owner's money - but paid usage is never on unless the environment says so explicitly.
    """
    current = policy.llm_primary
    try:
        # Validated, not copied: a cap of 0 or a negative cap is an error, not a quiet
        # fall-back to the file's value (review, LOW).
        primary = type(current).model_validate(
            {
                "credit_expires_on": credit_expires_on or current.credit_expires_on,
                "allow_paid_usage": allow_paid_usage,
                "spend_cap_usd": current.spend_cap_usd if spend_cap_usd is None else spend_cap_usd,
            }
        )
    except ValueError as bad:
        raise ConfigError(f"LLM spending settings are invalid: {bad}") from None
    secondary = policy.llm_secondary
    if secondary_cap_usd is not None:
        try:
            secondary = type(secondary).model_validate(
                {**secondary.model_dump(), "spend_cap_usd": secondary_cap_usd}
            )
        except ValueError as bad:
            raise ConfigError(f"LLM_SECONDARY_SPEND_CAP_USD is invalid: {bad}") from None
    return policy.model_copy(update={"llm_primary": primary, "llm_secondary": secondary})


#: config.md section 3: two-pass reasoning doubles the investigator calls of a patterned round.
DEFAULT_PATTERN_CALL_LIMIT = 10


def pattern_call_limit(config_dir: Path) -> int:
    """`llm.max_calls_per_case_with_patterns` from policy.yaml (default 10)."""
    raw = yaml.safe_load((config_dir / "policy.yaml").read_text(encoding="utf-8")) or {}
    value = (raw.get("llm") or {}).get(
        "max_calls_per_case_with_patterns", DEFAULT_PATTERN_CALL_LIMIT
    )
    if not isinstance(value, int) or value <= 0:
        raise ConfigError(
            f"llm.max_calls_per_case_with_patterns must be a positive int, not {value!r}"
        )
    return value


def with_pattern_budget(policy: PolicyConfig, *, cross_case_on: bool, limit: int) -> PolicyConfig:
    """Start-up picks the larger per-case call limit when cross_case is on (P7-06).

    Without it every patterned case would run out of calls and escalate llm_budget_exceeded.
    Never lowers a limit that is already higher.
    """
    if not cross_case_on:
        return policy
    llm = policy.llm.model_copy(
        update={"max_calls_per_case": max(policy.llm.max_calls_per_case, limit)}
    )
    return policy.model_copy(update={"llm": llm})
