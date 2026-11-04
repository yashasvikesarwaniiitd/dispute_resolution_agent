"""P7-05: the analyzer holds no write port and no executor (I2).

Inspected structurally: every field the analyzer and the observer are built with is checked
against the things that can write to the outside world. A future edit that hands either of
them a ticketing system, a channel, a ledger or the executor fails here.
"""

from __future__ import annotations

import dataclasses
import inspect

import pytest

from dispute_agent.modules.cross_case.analyzer import PatternAnalyzer
from dispute_agent.modules.cross_case.analyzer.observer import SqlOutcomeObserver
from dispute_agent.modules.cross_case.sql_context import SqlPatternContextProvider

WRITERS = (
    "ActionExecutor",
    "WritePort",
    "TicketingSystem",
    "MessageChannel",
    "Ledger",
    "IssueWritePort",
    "Investigations",
)
WRITE_METHODS = ("upsert_issue", "send", "post_credit", "execute", "plan")


@pytest.mark.parametrize("cls", [PatternAnalyzer, SqlOutcomeObserver, SqlPatternContextProvider])
def test_it_is_built_without_any_way_to_write_outside(cls: type) -> None:
    for f in dataclasses.fields(cls):
        annotation = str(f.type)  # the declared text (postponed annotations)
        for writer in WRITERS:
            assert writer not in annotation, f"{cls.__name__}.{f.name} holds a {writer}"


@pytest.mark.parametrize("cls", [PatternAnalyzer, SqlOutcomeObserver, SqlPatternContextProvider])
def test_it_calls_no_write_method(cls: type) -> None:
    source = inspect.getsource(inspect.getmodule(cls))  # type: ignore[arg-type]
    for method in WRITE_METHODS:
        assert f".{method}(" not in source, f"{cls.__name__}'s module calls .{method}("
