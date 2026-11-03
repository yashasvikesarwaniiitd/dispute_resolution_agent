"""The JSON-shaped types the kernel passes around (kernel-interfaces.md section 4.2).

Two aliases, and the difference between them matters. `JsonScalar` is what a domain pack may
state as a fact, and it excludes float deliberately: a pack fact reaches the calculator, and
I9 says money and weight are exact. `JsonValue` is the wider shape used for things that are
genuinely arbitrary JSON — a prompt's output schema, a model's parsed response, the value a
source returned — where the kernel is a courier rather than a reader.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal

type JsonScalar = str | int | bool | Decimal | None

type JsonValue = JsonScalar | Sequence[JsonValue] | Mapping[str, JsonValue]
