"""The autonomy golden set (scripts/golden_set.py): the workbook, and reading answers back.

The workbook is for a person; the spec it produces is for the agent. These tests hold the
promises between them: facts come from code and never from the sheet, every answer is one of
the listed options, a half-filled workbook writes nothing, and contradictions are named.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml
from openpyxl import load_workbook

if TYPE_CHECKING:
    from collections.abc import Callable

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def golden() -> Any:
    """scripts/ is not a package: load the script by path (registered, for its dataclasses)."""
    spec = importlib.util.spec_from_file_location("golden_set", ROOT / "scripts" / "golden_set.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["golden_set"] = module
    spec.loader.exec_module(module)
    return module


def _fill(golden: Any, path: Path, answer: Callable[[str], tuple[str, str, str, str, str]]) -> None:
    wb = load_workbook(path)
    ws = wb["Shipments"]
    first = len(golden.FACT_COLUMNS) + 1
    for row in range(golden.FIRST_ROW + 1, golden.FIRST_ROW + 1 + len(golden.CASES)):
        values = answer(str(ws.cell(row=row, column=1).value))
        for offset, value in enumerate(values):
            ws.cell(row=row, column=first + offset, value=value)
    rules = wb["Rules"]
    for r, rule_answer in enumerate((200, 20, 2000, "Yes", "Yes"), start=4):
        rules.cell(row=r, column=2, value=rule_answer)
    wb.save(path)


def _as_agent(golden: Any) -> Callable[[str], tuple[str, str, str, str, str]]:
    by_id = {c.id: c for c in golden.CASES}

    def answer(case_id: str) -> tuple[str, str, str, str, str]:
        step = golden.label(by_id[case_id].proposes)
        return (
            step,
            "Very sure (85%)",
            "Medium: costs money or annoys the seller",
            "No: a person reads it first",
            f"{case_id} reason",
        )

    return answer


def test_the_ten_cases_carry_code_s_amounts(golden: Any) -> None:
    amounts = {c.id: golden.facts(c).credit_paise for c in golden.CASES}
    # The fixtures' own documented arithmetic (synthetic_shipments/fixtures.py).
    assert amounts == {
        "GS-01": None,
        "GS-02": None,
        "GS-03": 3800,  # billed 5 slabs, rate card 4
        "GS-04": 15200,  # the second, duplicate debit
        "GS-05": 7600,  # the hub's re-measurement: 190 billed, 114 on the rate card
        "GS-06": 182000,  # keyed 20,000 g in zone E
        "GS-07": None,  # two errors: code cannot pick one amount
        "GS-08": None,
        "GS-09": None,
        "GS-10": None,
    }
    assert len({c.id for c in golden.CASES}) == 10


def test_each_case_shows_the_old_amount_and_what_is_left_after_the_credit(golden: Any) -> None:
    shown = {
        c.id: (f.expected, f.initially_charged, f.billed, f.after_credit)
        for c in golden.CASES
        for f in [golden.facts(c)]
    }
    assert shown["GS-03"] == ("₹76.00", "₹190.00", "₹190.00", "₹152.00")
    assert shown["GS-04"] == ("₹76.00", "₹152.00, then ₹152.00 again", "₹304.00", "₹152.00")
    assert shown["GS-05"] == ("₹76.00", "₹190.00", "₹190.00", "₹114.00")
    assert shown["GS-06"] == ("₹980.00", "₹2,800.00", "₹2,800.00", "₹980.00")
    assert shown["GS-07"] == ("₹980.00", "₹2,800.00, then ₹2,800.00 again", "₹5,600.00", "-")


def test_the_workbook_has_the_cases_dropdowns_and_an_example(golden: Any, tmp_path: Path) -> None:
    path = golden.build(tmp_path / "golden.xlsx")
    wb = load_workbook(path)
    assert wb.sheetnames == ["Start here", "Lists", "Shipments", "Rules"]
    ws = wb["Shipments"]
    ids = [ws.cell(row=r, column=1).value for r in range(golden.FIRST_ROW, ws.max_row + 1)]
    assert ids == [golden.EXAMPLE_ID, *(c.id for c in golden.CASES)]
    # Every row, the example included, fills exactly the case columns and then the answers.
    n_fact = len(golden.FACT_COLUMNS)
    header = [ws.cell(row=golden.HEADER_ROW, column=c).value for c in range(1, n_fact + 1)]
    example = {h: ws.cell(row=golden.FIRST_ROW, column=c).value for c, h in enumerate(header, 1)}
    assert example["Who decides today"] == "A person approves the evidence request email"
    assert example["The agent proposes"] == "Ask the seller for evidence"
    assert ws.cell(row=golden.FIRST_ROW, column=n_fact + 1).value == "Ask the seller for evidence"
    lists = {str(v.formula1) for v in ws.data_validations.dataValidation}
    assert len(lists) == 4 and all(f.startswith("=Lists!") for f in lists)
    # The answer cells start empty: nothing is pre-filled for the person to rubber-stamp.
    first = len(golden.FACT_COLUMNS) + 1
    assert all(
        ws.cell(row=r, column=c).value is None
        for r in range(golden.FIRST_ROW + 1, ws.max_row + 1)
        for c in range(first, first + 5)
    )


def test_ingest_writes_the_spec_from_code_not_from_the_sheet(golden: Any, tmp_path: Path) -> None:
    path = golden.build(tmp_path / "golden.xlsx")
    _fill(golden, path, _as_agent(golden))
    wb = load_workbook(path)
    credit_col = 1 + [n for n, _ in golden.FACT_COLUMNS].index("Credit code would post")
    gs03_row = golden.FIRST_ROW + 3
    wb["Shipments"].cell(row=gs03_row, column=credit_col, value="₹999,999.00")  # tampered
    wb.save(path)

    out = tmp_path / "autonomy-golden.yaml"
    spec = golden.ingest(path, out)

    assert yaml.safe_load(out.read_text(encoding="utf-8")) == spec
    gs03 = next(c for c in spec["cases"] if c["id"] == "GS-03")
    assert gs03["code"]["credit_inr"] == "38"  # code's figure, not the typed-over cell
    assert gs03["answer"] == {
        "next_step": "propose_credit",
        "solo_threshold": 0.85,
        "harm_if_wrong": "medium",
        "email_without_review": False,
        "reason": "GS-03 reason",
    }
    assert all(c["agrees_with_agent"] for c in spec["cases"])
    assert spec["rules"]["max_solo_credit_inr"] == 200
    # GS-06's ₹1,820 is above the ₹200 solo limit: named, not silently allowed.
    assert any(w.startswith("GS-06") and "limit" in w for w in spec["warnings"])


def test_a_half_filled_workbook_writes_nothing(golden: Any, tmp_path: Path) -> None:
    path = golden.build(tmp_path / "golden.xlsx")
    out = tmp_path / "autonomy-golden.yaml"
    with pytest.raises(golden.UnansweredError, match="GS-01 question 1: not answered"):
        golden.ingest(path, out)
    assert not out.exists()


def test_an_answer_that_is_not_an_option_is_refused(golden: Any, tmp_path: Path) -> None:
    path = golden.build(tmp_path / "golden.xlsx")
    agent = _as_agent(golden)
    _fill(
        golden, path, lambda i: ("Refund everything", *agent(i)[1:]) if i == "GS-02" else agent(i)
    )
    with pytest.raises(
        golden.UnansweredError, match="'Refund everything' is not one of the options"
    ):
        golden.ingest(path, tmp_path / "out.yaml")


def test_a_credit_where_code_proved_no_amount_is_named(golden: Any, tmp_path: Path) -> None:
    path = golden.build(tmp_path / "golden.xlsx")
    agent = _as_agent(golden)
    _fill(
        golden,
        path,
        lambda i: ("Credit the seller", *agent(i)[1:]) if i == "GS-07" else agent(i),
    )
    spec = golden.ingest(path, tmp_path / "out.yaml")
    gs07 = next(c for c in spec["cases"] if c["id"] == "GS-07")
    assert gs07["agrees_with_agent"] is False
    assert any(w.startswith("GS-07") and "code did not prove" in w for w in spec["warnings"])
