"""The scenario catalogue, read in full, with every script resolved to timed steps (P6-01).

`docs/spec/eval-scenarios.yaml` writes scripts in a compact, human form. This module is the
one reading of that form:

- `name@3h`, `name(args)@0.5h`, `name@11d` - a step at an offset from the start;
- `name@+1h` - an offset from the previous step;
- `name@(73h + 5d)` - a sum of offsets;
- `name@(S01 end + 10d)` - an offset from the last step of an included script;
- `S08 script` - include another scenario's steps first;
- `approve(credit) duplicate@1h` - the same step sent twice (a duplicate event).

YAML's flow lists split on commas, so a step whose arguments contain a comma arrives in two
pieces (S24); they are joined back while their parentheses are unbalanced.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from pathlib import Path

#: Phase 7's scenarios: cross-case, not runnable until the socket is filled.
PHASE_7 = 7.0

_UNIT = {"h": timedelta(hours=1), "d": timedelta(days=1), "m": timedelta(minutes=1)}
_AMOUNT = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([hdm])\s*$")


@dataclass(frozen=True)
class Step:
    name: str
    #: The text inside the parentheses, or "" ("credit", "notify_seller=true", ...).
    argument: str
    at: timedelta
    duplicate: bool = False

    def args(self) -> dict[str, str]:
        """`key=value` pairs; a bare first value is under the key "" ."""
        found: dict[str, str] = {}
        for part in (p.strip() for p in self.argument.split(",") if p.strip()):
            key, sep, value = part.partition("=")
            if sep:
                found[key.strip()] = value.strip()
            else:
                found.setdefault("", part)
        return found


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    phase: float
    fixture: str | None
    config: dict[str, Any]
    faults: dict[str, str]
    steps: tuple[Step, ...]
    expect: dict[str, Any]
    invariants: tuple[str, ...] = field(default_factory=tuple)
    #: Scenarios whose scripts run first (`S08 script`), in order, nested ones included.
    includes: tuple[str, ...] = ()

    @property
    def actions(self) -> list[str] | None:
        """The expected proposal actions in order; None when the catalogue says n/a."""
        actions = self.expect.get("actions")
        return [str(a) for a in actions] if isinstance(actions, list) else None

    @property
    def runnable(self) -> bool:
        return self.phase < PHASE_7 and self.fixture is not None


def recorded_actions(scenario: Scenario, by_id: dict[str, Scenario]) -> list[str]:
    """Every proposal the scripted run will ask for: included scripts' rounds come first."""
    actions: list[str] = []
    for sid in scenario.includes:
        actions.extend(by_id[sid].actions or [])
    own = list(scenario.actions or [])
    # A script that approves an evidence request before its listed rounds implies the round
    # that asked for it (S14a, S18b, S22, S23b list only what follows the reply).
    asks = any(s.name == "approve" and s.argument == "evidence_request" for s in scenario.steps)
    if asks and not scenario.includes and (not own or own[0] != "request_seller_evidence"):
        own.insert(0, "request_seller_evidence")
    actions.extend(own)
    return actions


def phase_number(raw: object) -> float:
    text = str(raw)
    return float(text[:-1]) + 0.5 if text.endswith("b") else float(text)


def load(path: Path) -> list[Scenario]:
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    entries = {str(e["id"]): e for e in raw.get("scenarios", [])}
    resolved: dict[str, tuple[Step, ...]] = {}

    def steps_of(sid: str) -> tuple[Step, ...]:
        if sid not in resolved:
            resolved[sid] = _resolve(entries[sid].get("script") or [], steps_of)
        return resolved[sid]

    def includes_of(sid: str) -> tuple[str, ...]:
        found: list[str] = []
        for token in entries[sid].get("script") or []:
            match = re.fullmatch(r"(S\w+) script", str(token).strip())
            if match:
                found.extend((*includes_of(match.group(1)), match.group(1)))
        return tuple(found)

    scenarios = []
    for sid, entry in entries.items():
        scenarios.append(
            Scenario(
                id=sid,
                title=str(entry.get("title", "")),
                phase=phase_number(entry.get("phase", 99)),
                fixture=entry.get("fixture"),
                config=dict(entry.get("config") or {}),
                faults={str(k): str(v) for k, v in (entry.get("faults") or {}).items()},
                steps=steps_of(sid) if phase_number(entry.get("phase", 99)) < PHASE_7 else (),
                expect=dict(entry.get("expect") or {}),
                invariants=tuple(entry.get("invariants") or ()),
                includes=includes_of(sid),
            )
        )
    return scenarios


def _resolve(tokens: list[Any], steps_of: Any) -> tuple[Step, ...]:
    steps: list[Step] = []
    ends: dict[str, timedelta] = {}
    for token in _joined([str(t) for t in tokens]):
        include = re.fullmatch(r"(S\w+) script", token.strip())
        if include:
            included = steps_of(include.group(1))
            steps.extend(included)
            ends[include.group(1)] = included[-1].at if included else timedelta(0)
            continue
        steps.append(_step(token, steps, ends))
    return tuple(steps)


def _joined(tokens: list[str]) -> list[str]:
    out: list[str] = []
    for token in tokens:
        if out and out[-1].count("(") > out[-1].count(")"):
            out[-1] = f"{out[-1]}, {token}"
        else:
            out.append(token)
    return out


def _step(token: str, previous: list[Step], ends: dict[str, timedelta]) -> Step:
    head, _, when = token.partition("@")
    duplicate = head.strip().endswith(" duplicate")
    head = head.strip().removesuffix(" duplicate").strip()
    name, _, rest = head.partition("(")
    argument = rest.rsplit(")", 1)[0] if rest else ""
    return Step(
        name=name.strip(),
        argument=argument.strip(),
        at=_when(when.strip(), previous, ends),
        duplicate=duplicate,
    )


def _when(text: str, previous: list[Step], ends: dict[str, timedelta]) -> timedelta:
    last = previous[-1].at if previous else timedelta(0)
    if not text:
        return last
    if text.startswith("+"):
        return last + _amount(text[1:])
    inner = text.strip("()")
    total = timedelta(0)
    for part in (p.strip() for p in inner.split("+")):
        anchor = re.fullmatch(r"(S\w+) end", part)
        total += ends.get(anchor.group(1), last) if anchor else _amount(part)
    return total


def _amount(text: str) -> timedelta:
    match = _AMOUNT.match(text)
    if not match:
        raise ValueError(f"cannot read the time {text!r} in a scenario script")
    return float(match.group(1)) * _UNIT[match.group(2)]


__all__ = ["PHASE_7", "Scenario", "Step", "load", "phase_number", "recorded_actions"]
