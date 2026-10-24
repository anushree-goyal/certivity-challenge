"""Minimal JSON Schema checking, standard library only (owner: Anushree).

``output_config.format`` already constrains the provider's response, so this is
a second, local check rather than the primary guarantee. It exists because:

* fixture and scripted adapters used in tests have no server-side constraint;
* a provider change or a relaxed schema should fail loudly here, not silently
  produce a draft whose fields the extractor then reads as ``None``.

It covers the subset of JSON Schema the agent prompts actually use: object
properties, required, arrays with ``items``, scalar types, enums and nullable
unions written as ``{"type": ["string", "null"]}``.
"""

from __future__ import annotations

from typing import Any

_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "null": (type(None),),
}


def _type_ok(expected: str, value: Any) -> bool:
    if expected == "integer":
        # bool is a subclass of int in Python; a flag is not an integer here.
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    allowed = _TYPE_MAP.get(expected)
    if allowed is None:
        return True  # unknown keyword: do not invent a failure
    return isinstance(value, allowed)


def schema_errors(schema: dict[str, Any], value: Any, path: str = "$") -> list[str]:
    """Return human-readable violations. An empty list means the value conforms."""
    errors: list[str] = []
    expected = schema.get("type")
    if expected is not None:
        options = expected if isinstance(expected, list) else [expected]
        if not any(_type_ok(option, value) for option in options):
            errors.append(f"{path}: expected type {options}, got {type(value).__name__}")
            return errors

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} is not one of {schema['enum']}")

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                errors.append(f"{path}: missing required property {name!r}")
        for name, sub_value in value.items():
            sub_schema = properties.get(name)
            if sub_schema is None:
                if schema.get("additionalProperties") is False and properties:
                    errors.append(f"{path}: unexpected property {name!r}")
                continue
            errors.extend(schema_errors(sub_schema, sub_value, f"{path}.{name}"))

    if isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(schema_errors(item_schema, item, f"{path}[{index}]"))

    return errors
