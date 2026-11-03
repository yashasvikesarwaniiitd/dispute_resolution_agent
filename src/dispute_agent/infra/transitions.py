"""Load `transitions.yaml` and hand the parsed document to the kernel.

The kernel reads no files, so this is where the disk is touched. The kernel owns the only
interpretation of what the document *means* (`kernel/statemachine/transitions.py`); this owns
only where it lives and how it is parsed.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from dispute_agent.kernel.domain.errors import ConfigError
from dispute_agent.kernel.statemachine.transitions import TransitionTable, build_table


def transitions_path() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "docs" / "spec" / "transitions.yaml"
        if candidate.is_file():
            return candidate
    raise ConfigError("cannot find docs/spec/transitions.yaml from " + str(here))


def load_transition_table(path: Path | None = None) -> TransitionTable:
    """Parse and build. Raises ConfigError through `build_table` on an unusable table."""
    source = transitions_path() if path is None else path
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    return build_table(raw)


@lru_cache(maxsize=1)
def default_table() -> TransitionTable:
    """The shipped table, parsed once.

    Cached because it is immutable and parsing it on every step would be waste, not because
    anything mutates it — `TransitionTable` is frozen all the way down.
    """
    return load_transition_table()
