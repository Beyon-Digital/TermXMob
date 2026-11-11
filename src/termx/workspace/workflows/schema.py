"""Bounded, dependency-free validation for reusable workflow definitions.

This deliberately implements a small JSON Schema vocabulary. Unsupported
keywords are errors, so a definition never appears more constrained than it is.
"""

from __future__ import annotations

import json
import math
from typing import Any

DEFAULT_LIMITS = {"max_agents": None, "concurrency": None, "timeout_s": None}
MAX_SOURCE_BYTES = 65_536
MAX_SCHEMA_BYTES = 65_536
MAX_SCHEMA_DEPTH = 16
MAX_SCHEMA_NODES = 2_048
MAX_ARGS_BYTES = 65_536
MAX_ARGS_DEPTH = 32
MAX_ARGS_NODES = 10_000

_TYPES = frozenset({"object", "array", "string", "integer", "number", "boolean", "null"})
_KEYWORDS = frozenset({
    "type", "properties", "required", "additionalProperties", "items", "enum", "const",
    "minLength", "maxLength", "minItems", "maxItems", "minProperties", "maxProperties",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "title", "description", "default",
})
_COUNT_PAIRS = (("minLength", "maxLength"), ("minItems", "maxItems"),
                ("minProperties", "maxProperties"))
_NUMERIC = ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum")
_DEFINITION_KEYS = frozenset({"name", "description", "project_id", "language", "source",
                              "input_schema", "limits", "archived"})


def _utf8_size(value: str, label: str) -> int:
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} must contain valid Unicode") from exc


def _bounded_json(value: Any, *, label: str, max_bytes: int,
                  max_depth: int, max_nodes: int) -> Any:
    nodes = 0

    def visit(item: Any, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > max_nodes:
            raise ValueError(f"{label} exceeds {max_nodes} JSON nodes")
        if depth > max_depth:
            raise ValueError(f"{label} exceeds JSON depth {max_depth}")
        kind = type(item)
        if item is None or kind in (bool, int):
            return
        if kind is float:
            if not math.isfinite(item):
                raise ValueError(f"{label} must contain finite JSON numbers")
            return
        if kind is str:
            if _utf8_size(item, label) > max_bytes:
                raise ValueError(f"{label} exceeds {max_bytes} UTF-8 bytes")
            return
        if kind is list:
            for child in item:
                visit(child, depth + 1)
            return
        if kind is dict:
            for key, child in item.items():
                if type(key) is not str:
                    raise ValueError(f"{label} object keys must be strings")
                visit(key, depth + 1)
                visit(child, depth + 1)
            return
        raise ValueError(f"{label} must contain only JSON values")

    visit(value, 0)
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False,
                             separators=(",", ":"))
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ValueError(f"{label} must contain only bounded JSON values") from exc
    if _utf8_size(encoded, label) > max_bytes:
        raise ValueError(f"{label} exceeds {max_bytes} UTF-8 bytes")
    return json.loads(encoded)


def _positive_limits(value: Any, label: str) -> dict[str, int | None]:
    if type(value) is not dict:
        raise ValueError(f"{label} must be an object")
    unknown = value.keys() - DEFAULT_LIMITS.keys()
    if unknown:
        raise ValueError(f"Unsupported {label} field: {sorted(map(str, unknown))[0]}")
    for key, limit in value.items():
        if limit is not None and (type(limit) is not int or limit < 1):
            raise ValueError(f"{label}.{key} must be null or a positive integer")
    return dict(value)


def resolve_limits(value: Any = None, ceiling: Any = None) -> dict[str, int | None]:
    """Manual run opt-ins only; omitted/null quotas mean unlimited.

    ``ceiling`` remains accepted for source compatibility and is validated, but
    inherited task or saved-definition ceilings no longer impose run quotas.
    """
    requested = {} if value is None else _positive_limits(value, "limits")
    if ceiling is not None:
        _positive_limits(ceiling, "ceiling")
    return {**DEFAULT_LIMITS, **requested}


def validate_source(source: Any) -> str:
    if type(source) is not str or not source.strip():
        raise ValueError("source must be a nonempty JavaScript function body")
    if _utf8_size(source, "source") > MAX_SOURCE_BYTES:
        raise ValueError(f"source exceeds {MAX_SOURCE_BYTES} UTF-8 bytes")
    return source


def _json_equal(left: Any, right: Any) -> bool:
    if type(left) in (int, float) and type(right) in (int, float):
        return left == right
    if type(left) is not type(right):
        return False
    if type(left) is list:
        return len(left) == len(right) and all(_json_equal(a, b) for a, b in zip(left, right))
    if type(left) is dict:
        return left.keys() == right.keys() and all(_json_equal(left[k], right[k]) for k in left)
    return left == right


def _validate_schema(schema: Any, path: str) -> None:
    if type(schema) is not dict:
        raise ValueError(f"{path} must be a schema object")
    unknown = schema.keys() - _KEYWORDS
    if unknown:
        raise ValueError(f"{path}: unsupported JSON Schema keyword {sorted(unknown)[0]}")
    for annotation, maximum in (("title", 120), ("description", 2000)):
        if annotation in schema and (type(schema[annotation]) is not str or len(schema[annotation]) > maximum):
            raise ValueError(f"{path}.{annotation} must be a string of at most {maximum} characters")
    if "type" in schema:
        declared = schema["type"]
        types = [declared] if type(declared) is str else declared
        if (type(types) is not list or not types or
                any(type(kind) is not str or kind not in _TYPES for kind in types) or
                len(types) != len(set(types))):
            raise ValueError(f"{path}.type must name supported, unique JSON types")
    if "properties" in schema:
        if type(schema["properties"]) is not dict:
            raise ValueError(f"{path}.properties must be an object")
        for key, child in schema["properties"].items():
            _validate_schema(child, f"{path}.properties[{key!r}]")
    if "required" in schema:
        required = schema["required"]
        if (type(required) is not list or any(type(key) is not str for key in required) or
                len(required) != len(set(required))):
            raise ValueError(f"{path}.required must be a list of unique property names")
    if "additionalProperties" in schema:
        additional = schema["additionalProperties"]
        if type(additional) is not bool:
            _validate_schema(additional, f"{path}.additionalProperties")
    if "items" in schema:
        _validate_schema(schema["items"], f"{path}.items")
    if "enum" in schema:
        choices = schema["enum"]
        if type(choices) is not list or not choices:
            raise ValueError(f"{path}.enum must be a nonempty array")
        if any(_json_equal(choice, earlier) for index, choice in enumerate(choices)
               for earlier in choices[:index]):
            raise ValueError(f"{path}.enum must contain unique JSON values")
    for minimum, maximum in _COUNT_PAIRS:
        for key in (minimum, maximum):
            if key in schema and (type(schema[key]) is not int or schema[key] < 0):
                raise ValueError(f"{path}.{key} must be a nonnegative integer")
        if minimum in schema and maximum in schema and schema[minimum] > schema[maximum]:
            raise ValueError(f"{path}.{minimum} must not exceed {maximum}")
    for key in _NUMERIC:
        if key in schema and type(schema[key]) not in (int, float):
            raise ValueError(f"{path}.{key} must be a finite number")
    lower = [(schema[key], key.startswith("exclusive")) for key in ("minimum", "exclusiveMinimum") if key in schema]
    upper = [(schema[key], key.startswith("exclusive")) for key in ("maximum", "exclusiveMaximum") if key in schema]
    if any(lo > hi or (lo == hi and (lo_exclusive or hi_exclusive))
           for lo, lo_exclusive in lower for hi, hi_exclusive in upper):
        raise ValueError(f"{path}: numeric bounds cannot be satisfied")
    if "default" in schema:
        # Defaults are annotations, never inserted into args. Their JSON budget
        # was checked with the complete schema; the value walker ignores
        # annotations, so this validates constraints without recursive defaults.
        _validate_value(schema["default"], schema, f"{path}.default")


def validate_input_schema(schema: Any = None) -> dict[str, Any]:
    if schema is None:
        schema = {"type": "object", "properties": {}, "additionalProperties": True}
    schema = _bounded_json(schema, label="input_schema", max_bytes=MAX_SCHEMA_BYTES,
                           max_depth=MAX_SCHEMA_DEPTH, max_nodes=MAX_SCHEMA_NODES)
    _validate_schema(schema, "input_schema")
    if schema.get("type") != "object":
        raise ValueError("input_schema root must have type='object'")
    return schema


def _matches_type(value: Any, kind: str) -> bool:
    if kind == "integer":
        return type(value) is int or (type(value) is float and value.is_integer())
    if kind == "number":
        return type(value) in (int, float)
    return {"object": dict, "array": list, "string": str, "boolean": bool,
            "null": type(None)}[kind] is type(value)


def _validate_value(value: Any, schema: dict[str, Any], path: str) -> None:
    if "type" in schema:
        declared = schema["type"]
        types = [declared] if type(declared) is str else declared
        if not any(_matches_type(value, kind) for kind in types):
            raise ValueError(f"{path} must have type {' or '.join(types)}")
    if "enum" in schema and not any(_json_equal(value, choice) for choice in schema["enum"]):
        raise ValueError(f"{path} is not one of the allowed values")
    if "const" in schema and not _json_equal(value, schema["const"]):
        raise ValueError(f"{path} must equal the schema constant")
    if type(value) is dict:
        for key in schema.get("required", []):
            if key not in value:
                raise ValueError(f"{path} is missing required property {key!r}")
        for keyword, compare in (("minProperties", lambda n, bound: n < bound),
                                 ("maxProperties", lambda n, bound: n > bound)):
            if keyword in schema and compare(len(value), schema[keyword]):
                raise ValueError(f"{path} violates {keyword}={schema[keyword]}")
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key, child in value.items():
            child_path = f"{path}[{key!r}]"
            if key in properties:
                _validate_value(child, properties[key], child_path)
            elif additional is False:
                raise ValueError(f"{child_path} is not an allowed property")
            elif type(additional) is dict:
                _validate_value(child, additional, child_path)
    elif type(value) is list:
        for keyword, compare in (("minItems", lambda n, bound: n < bound),
                                 ("maxItems", lambda n, bound: n > bound)):
            if keyword in schema and compare(len(value), schema[keyword]):
                raise ValueError(f"{path} violates {keyword}={schema[keyword]}")
        if "items" in schema:
            for index, child in enumerate(value):
                _validate_value(child, schema["items"], f"{path}[{index}]")
    elif type(value) is str:
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise ValueError(f"{path} is shorter than minLength={schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ValueError(f"{path} is longer than maxLength={schema['maxLength']}")
    elif type(value) in (int, float):
        for key, invalid in (("minimum", lambda a, b: a < b),
                             ("maximum", lambda a, b: a > b),
                             ("exclusiveMinimum", lambda a, b: a <= b),
                             ("exclusiveMaximum", lambda a, b: a >= b)):
            if key in schema and invalid(value, schema[key]):
                raise ValueError(f"{path} violates {key}={schema[key]}")


def validate_args(args: Any, schema: Any) -> dict[str, Any]:
    if type(args) is not dict:
        raise ValueError("args must be a JSON object")
    args = _bounded_json(args, label="args", max_bytes=MAX_ARGS_BYTES,
                         max_depth=MAX_ARGS_DEPTH, max_nodes=MAX_ARGS_NODES)
    _validate_value(args, validate_input_schema(schema), "args")
    return args


def validate_definition(data: Any, partial: bool = False) -> dict[str, Any]:
    if type(data) is not dict:
        raise ValueError("workflow definition must be an object")
    unknown = data.keys() - _DEFINITION_KEYS
    if unknown:
        raise ValueError(f"Unsupported workflow field: {sorted(map(str, unknown))[0]}")
    out: dict[str, Any] = {} if partial else {
        "description": "", "project_id": None, "language": "javascript",
        "input_schema": validate_input_schema(), "limits": resolve_limits(), "archived": False,
    }
    if not partial:
        for key in ("name", "source"):
            if key not in data:
                raise ValueError(f"workflow definition requires {key}")
    if "name" in data:
        name = data["name"]
        if type(name) is not str or not 1 <= len(name.strip()) <= 120:
            raise ValueError("name must contain 1..120 characters")
        _utf8_size(name, "name")
        out["name"] = name.strip()
    if "description" in data:
        description = data["description"]
        if type(description) is not str or len(description) > 2000:
            raise ValueError("description must be a string of at most 2000 characters")
        _utf8_size(description, "description")
        out["description"] = description
    if "project_id" in data:
        project_id = data["project_id"]
        if project_id is not None:
            if type(project_id) is not str or not 1 <= len(project_id.strip()) <= 200:
                raise ValueError("project_id must be null or a string of 1..200 characters")
            _utf8_size(project_id, "project_id")
            project_id = project_id.strip()
        out["project_id"] = project_id
    if "language" in data:
        if data["language"] != "javascript" or type(data["language"]) is not str:
            raise ValueError("language must be 'javascript'")
        out["language"] = "javascript"
    if "source" in data:
        out["source"] = validate_source(data["source"])
    if "input_schema" in data:
        out["input_schema"] = validate_input_schema(data["input_schema"])
    if "limits" in data:
        out["limits"] = resolve_limits(data["limits"])
    if "archived" in data:
        if type(data["archived"]) is not bool:
            raise ValueError("archived must be a boolean")
        out["archived"] = data["archived"]
    return out
