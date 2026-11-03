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
