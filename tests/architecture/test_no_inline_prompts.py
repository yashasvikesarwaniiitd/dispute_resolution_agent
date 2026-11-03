"""Card P0-10: runtime prompts live in `prompts/`, never in `src/` (prompts/README.md).

The reason is not tidiness. A prompt in `prompts/<name>/<version>/` is versioned, hashed and
recorded on every proposal it produced, so "which instructions caused this decision?" has an
answer months later. A prompt pasted into a Python file has none of that: it changes with the
code, leaves no hash, and a released version can be edited in place — which CLAUDE.md section
9 forbids precisely because it destroys the audit trail.

The test looks for the reserved tags listed in prompts/README.md. They are deliberately
distinctive, so this catches a real prompt without flagging an f-string that happens to
contain a less-than sign.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
PROMPTS_README = ROOT / "prompts" / "README.md"

#: prompts/README.md section "Reserved tags (the architecture test looks for these in src/)".
RESERVED_TAGS = (
    "<role>",
    "<objective>",
    "<trust_model>",
    "<reasoning_procedure>",
    "<output_contract>",
)
UNTRUSTED_TAG = re.compile(r"<untrusted_\w+")

SOURCES = sorted(SRC.rglob("*.py"))


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(SRC)))
def test_no_reserved_prompt_tag_in_source(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    found = [tag for tag in RESERVED_TAGS if tag in source]
    assert not found, f"{path.relative_to(SRC)} contains prompt text: {found}"


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(SRC)))
def test_no_untrusted_block_in_source(path: Path) -> None:
    """An `<untrusted_*>` block in code would mean untrusted text being framed outside the
    prompt registry, which is where the neutralise filter lives (I5)."""
    match = UNTRUSTED_TAG.search(path.read_text(encoding="utf-8"))
    assert match is None, f"{path.relative_to(SRC)} frames untrusted text: {match.group(0)!r}"


def test_the_tag_list_matches_the_standard() -> None:
    """Reads prompts/README.md, so adding a tag there without adding it here fails."""
    section = PROMPTS_README.read_text(encoding="utf-8").split("## Reserved tags")[1]
    quoted = set(re.findall(r"`(<[a-z_]+>?)`", section))
    documented = {tag for tag in quoted if tag.endswith(">")}
    assert documented == set(RESERVED_TAGS)
    assert "<untrusted_" in section


def test_the_prompt_directory_is_where_prompts_live() -> None:
    """A positive control: if this found nothing, the test above would pass vacuously."""
    prompt_files = list((ROOT / "prompts").rglob("system.md"))
    assert prompt_files, "no prompts found at all; the standard is not being followed"
    with_tags = [
        p
        for p in prompt_files
        if any(tag in p.read_text(encoding="utf-8") for tag in RESERVED_TAGS)
    ]
    assert with_tags, "no prompt uses the reserved tags, so the test above proves nothing"
