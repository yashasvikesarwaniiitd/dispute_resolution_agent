"""Contract: PromptRegistry (kernel-interfaces.md 6.4, prompts/README.md, I5).

This is the only door untrusted text goes through on its way into a prompt, which makes the
`neutralise` cases the load-bearing ones. test-charter.md names six, one per attack shape,
and the positive control is as important as the five attacks: a benign `<` must survive
readably, because a filter that mangles ordinary text gets removed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from tests.contract.conftest import build_or_skip, params, pending

if TYPE_CHECKING:
    from dispute_agent.kernel.ports import PromptRegistry

ADAPTERS = [
    pending("file_backed", phase="Phase 4 (card P4-01)"),
]


@pytest.fixture(params=params(ADAPTERS))
def registry(request: pytest.FixtureRequest) -> PromptRegistry:
    return cast("PromptRegistry", build_or_skip(request.param))


class TestLoading:
    def test_a_known_prompt_renders(self, registry: PromptRegistry) -> None: ...

    def test_an_unknown_name_or_version_raises_not_found(
        self, registry: PromptRegistry
    ) -> None: ...

    def test_a_missing_variable_raises_rather_than_rendering_blank(
        self, registry: PromptRegistry
    ) -> None:
        """StrictUndefined: a silently empty block is a prompt that quietly lost its data."""

    def test_the_hash_covers_the_rendered_messages(self, registry: PromptRegistry) -> None:
        """Recorded on every proposal, so the hash has to change when the prompt does."""


class TestTrustBoundary:
    def test_untrusted_values_render_only_inside_delimited_blocks(
        self, registry: PromptRegistry
    ) -> None:
        """I5. The separate `untrusted` argument is what makes this checkable."""

    def test_a_trusted_variable_cannot_be_passed_as_untrusted_text_by_accident(
        self, registry: PromptRegistry
    ) -> None: ...

    @pytest.mark.parametrize(
        "shape",
        [
            "closing reserved tag",
            "opening trusted tag",
            "nested tags",
            "zero-width characters",
            "bidirectional overrides",
            "a benign < that must survive readably",
        ],
    )
    def test_neutralise_handles_each_attack_shape(
        self, registry: PromptRegistry, shape: str
    ) -> None:
        """TC-PR-NEU-1..6. The last is a positive control: a filter that ruins ordinary text
        is a filter somebody switches off."""
