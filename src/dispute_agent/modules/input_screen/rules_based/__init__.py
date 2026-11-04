"""The rules-based input screen (P4-09, test-charter.md section 5, threats T01/T02, I15).

It reads outside text and names the shapes of an attack it can see. It never decides
anything: a flag can only add caution (R21 escalates on `instruction_like`), a clean result
never relaxes a rule, and a screen that breaks says `screen_error`, which also adds caution.

The patterns are about *shapes*, not about any particular case: an instruction aimed at the
system, markup that pretends to be structure, text hidden from a human reader, an encoded
payload with an order to decode it, a different address to write to, a figure offered for
the system to use. They are deliberately broad - a false flag costs a person a look; a
missed one costs nothing only because the other layers still hold (the fence, the menu,
code-derived amounts, approvals). That layering is the point: this screen is one layer.

A model-based screen (Foundry guardrails) is a separate adapter; this one needs no network.
"""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.reasoning import ScreenResult

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.enums import ScreenKind
    from dispute_agent.kernel.domain.values import UntrustedText

MODULE = "input_screen"
ADAPTER = "rules_based"

_I = re.IGNORECASE

#: Flag name -> patterns. `instruction_like` is the one R21 reads; the others are recorded
#: as their own flags (and become `screen_flag` caution signals).
SHAPES: dict[str, tuple[re.Pattern[str], ...]] = {
    "instruction_like": (
        re.compile(r"\b(ignore|disregard|forget)\b.{0,40}\b(previous|prior|above|earlier)\b", _I),
        re.compile(r"(^|\n)\s*(system|assistant|developer|admin)\s*:", _I),
        re.compile(r"\b(policy|rules?)\b.{0,30}\b(updated|changed|no longer)\b", _I),
        re.compile(
            r"\b(approve|authori[sz]e|mark)\b.{0,40}\b(credit|refund|resolved|closed)\b", _I
        ),
        re.compile(r"\b(i|we)\s+(authori[sz]e|instruct|order)\s+you\b", _I),
        re.compile(r"\bmaintenance mode\b|\bdeveloper mode\b|\bjailbreak\b", _I),
        re.compile(r"\brefund\b.{0,40}\b(all|every|last \d+|\d+ shipments)\b", _I),
        re.compile(r"\b(credit|refund)\b.{0,40}\b(immediately|without review|right now)\b", _I),
    ),
    "markup_like": (re.compile(r"</?\s*[A-Za-z_][A-Za-z0-9_-]*\s*[^<>]*>"),),
    "hidden_text": (
        re.compile(r"<!--.*?-->", re.S),
        re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff]"),
    ),
    "encoded_payload": (
        re.compile(r"\b(decode|base64|rot13)\b", _I),
        re.compile(r"[A-Za-z0-9+/]{40,}={0,2}"),
    ),
    "recipient_change": (
        re.compile(r"\b(send|email|forward|reply)\b.{0,40}\b[\w.+-]+@[\w-]+\.[\w.-]+", _I),
    ),
    "number_injection": (
        re.compile(
            r"\b(use|put|write)\b.{0,30}\b(this|that|the)\s+(number|figure|amount|weight)\b", _I
        ),
        re.compile(r"\bcorrect (weight|amount|charge) is\b", _I),
    ),
    "fabricated_reference": (re.compile(r"\b(ev|evi)[._][A-Za-z_]+v\d+\b", _I),),
}

#: Instruction words in a script the patterns above do not read. A request for a credit in
#: Hindi is the same attack in another language (corpus item 7).
_FOREIGN_ASK = re.compile(r"(रिफंड|क्रेडिट|मंजूर|स्वीकृत|वापस)")


class RulesBasedInputScreen:
    """Implements InputScreen."""

    def screen(self, text: UntrustedText, kind: ScreenKind) -> ScreenResult:
        try:
            found = self.flags_for(text.text)
            if text.truncated:
                # The end of the text was cut, so neither the screen nor the model saw all of
                # it - and the end is where an instruction hides (corpus item 10).
                found.add("truncated_input")
            flags = tuple(sorted(found))
        except Exception:
            flags = ("screen_error",)
        return ScreenResult(kind=kind, flags=flags, blocked=False, adapter=ADAPTER)

    @staticmethod
    def flags_for(raw: str) -> set[str]:
        # Look-alike characters become their plain form first, so a full-width "SYSTEM:"
        # reads like the ordinary one.
        text = unicodedata.normalize("NFKC", raw)
        found = {name for name, patterns in SHAPES.items() if any(p.search(text) for p in patterns)}
        if _FOREIGN_ASK.search(text):
            found.add("instruction_like")
        # Encoded payloads count only with the order to decode, or when long enough that
        # ordinary prose never looks like that; a single long word is not an attack.
        if (
            "encoded_payload" in found
            and not re.search(r"\b(decode|base64)\b", text, _I)
            and not re.search(r"[A-Za-z0-9+/]{60,}={0,2}", text)
        ):
            found.discard("encoded_payload")
        if found & {
            "encoded_payload",
            "hidden_text",
            "markup_like",
            "number_injection",
            "recipient_change",
        }:
            # Hiding or encoding text aimed at the system is itself an instruction attempt.
            found.add("instruction_like")
        return found


__all__ = ["ADAPTER", "MODULE", "SHAPES", "RulesBasedInputScreen"]
