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
    return policy.model_copy(update={"llm_primary": primary})
