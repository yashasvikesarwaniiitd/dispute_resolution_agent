"""Native structured outputs for the Anthropic route (ADR 0073).

The prompt's own schema stays the source of truth: this module only derives what the API's
grammar can enforce (`output_config.format`), and `output.parse` still validates every answer
against the full schema afterwards. What the grammar cannot express is dropped here and
checked there:
- length, count and range limits (`maxLength`, `maxItems`, `minimum`, ...), `pattern`,
  `uniqueItems` and string formats the API does not list;
- an object whose keys are free (`{"type": "object"}`, e.g. a tool's `arguments`): the
  grammar can only return listed keys, so it is sent as a list of {key, value} pairs and
  turned back into the object before parsing - the API's documented workaround.

A schema past the API's documented limits (24 optional fields, 16 unions) is sent without a
format, exactly as before; so is one the API refused once (remembered for the process).
"""

from __future__ import annotations

import json
from typing import Any

#: Keywords the grammar enforces, per the structured-outputs documentation (2026-11-27).
KEPT = frozenset(
    {
        "type",
        "properties",
        "required",
        "items",
        "enum",
        "const",
        "anyOf",
        "allOf",
        "$ref",
        "$defs",
        "definitions",
        "description",
        "title",
        "default",
    }
)
FORMATS = frozenset(
    {"date-time", "time", "date", "duration", "email", "hostname", "uri", "ipv4", "ipv6", "uuid"}
)
MAX_OPTIONAL = 24
MAX_UNIONS = 16
#: A free-form object's values: what a tool argument can be.
PAIR = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "key": {"type": "string"},
            "value": {"type": ["string", "number", "boolean", "null"]},
        },
        "required": ["key", "value"],
        "additionalProperties": False,
    },
}

Path = tuple[str, ...]


def provider_schema(schema: dict[str, Any]) -> tuple[dict[str, Any] | None, list[Path]]:
    """(the schema the grammar can enforce, the paths sent as key/value pairs), or (None, [])
    when the schema cannot be enforced natively within the documented limits."""
    pairs: list[Path] = []
    try:
        converted = _convert(schema, (), pairs)
    except ValueError:
        return None, []
    optional, unions = _count(converted)
    if optional > MAX_OPTIONAL or unions > MAX_UNIONS:
        return None, []
    return converted, pairs


def restore(text: str, pairs: list[Path]) -> str:
    """The answer with each key/value list turned back into the object the schema asks for.
    Anything unexpected is left as it is, for `parse` to judge."""
    if not pairs:
        return text
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text
    for path in pairs:
        holder, key = _parent(value, path)
        found = holder.get(key) if isinstance(holder, dict) else None
        if isinstance(found, list) and all(isinstance(p, dict) and "key" in p for p in found):
            holder[key] = {str(p["key"]): p.get("value") for p in found}
    return json.dumps(value)


def _convert(node: Any, path: Path, pairs: list[Path]) -> dict[str, Any]:
    if not isinstance(node, dict):
        raise ValueError("not a schema")
    if node.get("type") == "object" and "properties" not in node:
        if not path:
            raise ValueError("a free-form root cannot be sent as pairs")
        pairs.append(path)
        return dict(PAIR)
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key == "properties":
            out[key] = {name: _convert(sub, (*path, name), pairs) for name, sub in value.items()}
        elif key == "items":
            out[key] = _convert(value, (*path, "[]"), [])  # free objects inside lists: not needed
        elif key in ("anyOf", "allOf"):
            out[key] = [_convert(sub, path, []) for sub in value]
        elif key in ("$defs", "definitions"):
            out[key] = {name: _convert(sub, path, []) for name, sub in value.items()}
        elif (
            (key == "format" and value in FORMATS)
            or (key == "minItems" and value in (0, 1))
            or key in KEPT
        ):
            out[key] = value
        elif key == "oneOf":
            out["anyOf"] = [_convert(sub, path, []) for sub in value]
    if out.get("type") == "object":
        out["additionalProperties"] = False
    return out


def _count(node: Any) -> tuple[int, int]:
    optional = unions = 0
    if isinstance(node, dict):
        if isinstance(node.get("type"), list) or "anyOf" in node:
            unions += 1
        if isinstance(node.get("properties"), dict):
            optional += len(set(node["properties"]) - set(node.get("required") or ()))
        for value in node.values():
            o, u = _count(value)
            optional, unions = optional + o, unions + u
    elif isinstance(node, list):
        for value in node:
            o, u = _count(value)
            optional, unions = optional + o, unions + u
    return optional, unions


def _parent(value: Any, path: Path) -> tuple[Any, str]:
    for key in path[:-1]:
        value = value.get(key) if isinstance(value, dict) else None
    return value, path[-1] if path else ""


__all__ = ["provider_schema", "restore"]
