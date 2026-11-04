"""Loading a prompt from `prompts/` and rendering it for one case (P4-01).

Runtime prompts live only in `prompts/<name>/<version>/`, never in code. This reads one,
renders its user message for a case, and returns it with a hash of the system prompt - the
hash that is recorded on every proposal, so any decision can be traced back to the exact
instructions that produced it.

**An inactive prompt is refused unless the caller opts in.** A prompt is `inactive` until
its eval suite has passed (prompts/README.md). The prototype phase runs before the eval
harness exists, so the prototype needs this prompt while it is still inactive. Rather than
switch the rule off, the registry takes `allow_inactive`, which is off by default and set
only by `PROMPTS_ALLOW_INACTIVE=true` - "I am knowingly testing an un-evaluated prompt".
Without it, building the model reasoner fails at start-up (`app.build_llm_reasoner`), and
a case that somehow reaches an inactive prompt goes to a person.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import yaml
from jinja2 import UndefinedError

from dispute_agent.kernel.domain.errors import DomainValidationError, NotFound
from dispute_agent.kernel.domain.hashing import payload_hash
from dispute_agent.kernel.domain.reasoning import ChatMessage, RenderedPrompt
from dispute_agent.kernel.domain.values import UntrustedText
from dispute_agent.modules.reasoning.templates import make_environment

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from dispute_agent.kernel.domain.json_values import JsonValue

#: Names and versions are used as path parts, so they are checked before they touch the
#: filesystem. A name like "../../secrets" must not be loadable as a prompt.
NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
VERSION = re.compile(r"^v[0-9]{1,4}$")


@dataclass
class FilePromptRegistry:
    """Implements PromptRegistry over the `prompts/` folder."""

    root: Path
    allow_inactive: bool = False

    def load(
        self,
        name: str,
        version: str,
        variables: Mapping[str, JsonValue] | Mapping[str, Any],
        untrusted: Mapping[str, UntrustedText],
    ) -> RenderedPrompt:
        """Render `prompts/<name>/<version>/` for one case.

        `untrusted` is passed to the template as its own variable, so a value's trust label
        is decided by which argument it arrived in, never by a guess at render time.
        """
        folder = self._folder(name, version)
        meta = self.meta(name, version)
        status = str(meta.get("status", "inactive")).split()[0]
        if status != "active" and not self.allow_inactive:
            raise DomainValidationError(
                f"prompt {name}/{version} is {status}: it has not passed its eval suite. "
                "Set PROMPTS_ALLOW_INACTIVE=true only to knowingly test an un-evaluated "
                "prompt (docs/SETUP_FOUNDRY.md)."
            )

        system = (folder / "system.md").read_text(encoding="utf-8")
        env = make_environment(folder)
        try:
            user = env.get_template("user.md.j2").render(**_attach_untrusted(variables, untrusted))
        except UndefinedError as missing:
            raise DomainValidationError(
                f"prompt {name}/{version} needs a value the briefing does not have: {missing}"
            ) from missing
        schema = json.loads(
            (folder / str(meta.get("output_schema", "schema.json"))).read_text(encoding="utf-8")
        )

        return RenderedPrompt(
            name=name,
            version=version,
            # The system prompt is static, so its hash identifies the instructions exactly;
            # per-case data is in the user message and deliberately not part of this hash.
            hash=payload_hash(system),
            messages=(
                ChatMessage(role="system", content=system),
                ChatMessage(role="user", content=user),
            ),
            output_schema=schema,
        )

    def system_text(self, name: str, version: str) -> str:
        """The static instructions, for agent sync: what Foundry is given as the agent."""
        return (self._folder(name, version) / "system.md").read_text(encoding="utf-8")

    def meta(self, name: str, version: str) -> dict[str, Any]:
        loaded = yaml.safe_load(
            (self._folder(name, version) / "meta.yaml").read_text(encoding="utf-8")
        )
        return dict(loaded) if isinstance(loaded, dict) else {}

    def _folder(self, name: str, version: str) -> Path:
        if not NAME.match(name) or not VERSION.match(version):
            raise DomainValidationError(f"not a prompt name and version: {name!r} {version!r}")
        folder = self.root / name / version
        if not (folder / "system.md").is_file():
            raise NotFound(f"no prompt {name}/{version} under {self.root}")
        return folder


def _attach_untrusted(
    variables: Mapping[str, JsonValue] | Mapping[str, Any],
    untrusted: Mapping[str, UntrustedText],
) -> dict[str, Any]:
    """Put each piece of outside text into the briefing slot that names it, and only there.

    The convention: the briefing `b` lists outside documents in `untrusted_evidence` by
    `id` and `source` only, and the text for each id arrives in `untrusted`. So the trusted
    argument can never carry outside text (refused if it tries), and every piece of outside
    text passed in is rendered - inside its delimited block, through `neutralise` - rather
    than silently dropped.
    """
    for key, value in untrusted.items():
        if not isinstance(value, UntrustedText):
            raise DomainValidationError(f"untrusted[{key!r}] is not UntrustedText")

    out: dict[str, Any] = dict(variables)

    # The `ctx` prompts (reply interpreter, drafter): outside text addressed as
    # "ctx.<field>" is placed at that field, and the trusted `ctx` may not already hold it.
    slotted = {key: value for key, value in untrusted.items() if key.startswith("ctx.")}
    if slotted:
        ctx = out.get("ctx")
        if not isinstance(ctx, dict):
            raise DomainValidationError("outside text names ctx, but there is no ctx")
        ctx = dict(ctx)
        for key, slot_text in slotted.items():
            field = key.removeprefix("ctx.")
            if field in ctx:
                raise DomainValidationError(
                    f"ctx.{field} must arrive through the untrusted argument only (I5)"
                )
            ctx[field] = slot_text.text
        out["ctx"] = ctx
        untrusted = {k: v for k, v in untrusted.items() if k not in slotted}

    briefing = out.get("b")
    if not isinstance(briefing, dict):
        if untrusted:
            raise DomainValidationError(
                "outside text was passed but the briefing has no slot for it"
            )
        return out

    listed: list[dict[str, Any]] = []
    for item in briefing.get("untrusted_evidence", []):
        if not isinstance(item, dict) or "text" in item:
            raise DomainValidationError(
                "outside text must arrive through the untrusted argument, not the briefing (I5)"
            )
        doc = untrusted.get(str(item.get("id")))
        if doc is None:
            raise DomainValidationError(f"no outside text supplied for {item.get('id')!r}")
        listed.append({**item, "text": doc.text})

    unused = set(untrusted) - {str(item["id"]) for item in listed}
    if unused:
        raise DomainValidationError(f"outside text supplied but not listed: {sorted(unused)}")
    out["b"] = {**briefing, "untrusted_evidence": listed}
    return out


__all__ = ["NAME", "VERSION", "FilePromptRegistry"]
