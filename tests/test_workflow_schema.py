from __future__ import annotations

import math

import pytest

from termx.workspace.workflows import schema as validation
from termx.workspace.workflows.schema import (
    DEFAULT_LIMITS,
    MAX_SOURCE_BYTES,
    resolve_limits,
    validate_args,
    validate_definition,
    validate_input_schema,
    validate_source,
)


def object_schema(properties=None, **extra):
    return {"type": "object", "properties": properties or {}, **extra}


def test_definition_defaults_and_partial_updates_are_independent():
    source = "\nreturn await agent({prompt: args.prompt});\n"
    definition = validate_definition({"name": "  Research  ", "source": source})
    assert definition == {
        "name": "Research", "source": source, "description": "", "project_id": None,
        "language": "javascript", "input_schema": object_schema(additionalProperties=True),
        "limits": DEFAULT_LIMITS, "archived": False,
    }
    definition["limits"]["concurrency"] = 1
    definition["input_schema"]["properties"]["changed"] = {}
    other = validate_definition({"name": "Other", "source": "return 1;"})
    assert other["limits"]["concurrency"] is None
    assert other["input_schema"]["properties"] == {}
    assert validate_definition({"archived": True}, partial=True) == {"archived": True}
    assert validate_definition({}, partial=True) == {}


@pytest.mark.parametrize("data", [
    None, [], {}, {"source": "return 1;"}, {"name": "Name"},
    {"name": " ", "source": "return 1;"},
    {"name": "x" * 121, "source": "return 1;"},
    {"name": "Name", "source": "return 1;", "description": "x" * 2001},
    {"name": "Name", "source": "return 1;", "description": None},
    {"name": "Name", "source": "return 1;", "language": "python"},
    {"name": "Name", "source": "return 1;", "project_id": " "},
    {"name": "Name", "source": "return 1;", "project_id": "x" * 201},
    {"name": "Name", "source": "return 1;", "archived": 1},
    {"name": "Name", "source": "return 1;", "owner": "other-user"},
    {"name": "Name", "source": "return 1;", "revision": 5},
    {"name": "Name", "source": "return 1;", "request_id": "service-only"},
])
def test_definition_rejects_invalid_or_server_owned_fields(data):
    with pytest.raises(ValueError):
        validate_definition(data)


def test_definition_normalizes_project_id_and_preserves_description():
    assert validate_definition({"project_id": " project ", "description": " details "}, partial=True) == {
        "project_id": "project", "description": " details ",
    }
    assert validate_definition({"project_id": None}, partial=True) == {"project_id": None}


def test_source_limit_counts_utf8_bytes_and_preserves_source():
    assert validate_source("x" * MAX_SOURCE_BYTES) == "x" * MAX_SOURCE_BYTES
    assert validate_source("é" * (MAX_SOURCE_BYTES // 2))
    for source in (None, 42, "", "\n\t ", "x" * (MAX_SOURCE_BYTES + 1),
                   "é" * (MAX_SOURCE_BYTES // 2 + 1), "\ud800"):
        with pytest.raises(ValueError, match="source"):
            validate_source(source)


def test_manual_limits_are_optional_without_inherited_or_numeric_ceilings():
    assert resolve_limits() == DEFAULT_LIMITS
    assert resolve_limits({"max_agents": 64, "concurrency": 8, "timeout_s": 3600}) == {
        "max_agents": 64, "concurrency": 8, "timeout_s": 3600,
    }
    ceiling = {"max_agents": 2, "concurrency": 1, "timeout_s": 60}
    assert resolve_limits(None, ceiling) == DEFAULT_LIMITS
    assert resolve_limits({"max_agents": 1}, ceiling) == {**DEFAULT_LIMITS, "max_agents": 1}
    assert resolve_limits({"concurrency": 200, "max_agents": 1000, "timeout_s": None}, ceiling) == {
        "concurrency": 200, "max_agents": 1000, "timeout_s": None}
    returned = resolve_limits()
    returned["max_agents"] = 1
    assert DEFAULT_LIMITS["max_agents"] is None


@pytest.mark.parametrize("limits", [
    [], True, {"unknown": 1}, {"max_agents": True}, {"max_agents": 0},
    {"timeout_s": 1.0}, {"max_agents": "2"}, {"timeout_s": -1},
])
def test_limits_reject_noninteger_and_unbounded_values(limits):
    with pytest.raises(ValueError):
        resolve_limits(limits)
    with pytest.raises(ValueError):
        resolve_limits(ceiling=limits)


def test_nested_objects_arrays_nullable_and_property_schemas():
    schema = object_schema({
        "prompt": {"type": "string", "minLength": 2, "maxLength": 20},
        "researchers": {"type": "array", "minItems": 1, "maxItems": 2,
                        "items": {"type": "object", "required": ["id"],
                                  "properties": {"id": {"type": "integer"}},
                                  "additionalProperties": False}},
        "optional": {"type": ["string", "null"]},
    }, required=["prompt", "researchers"], minProperties=2, maxProperties=4,
       additionalProperties={"type": "boolean"})
    args = {"prompt": "Explore", "researchers": [{"id": 2.0}], "optional": None, "flag": True}
    result = validate_args(args, schema)
    assert result == args
    result["researchers"][0]["id"] = 4
    assert args["researchers"][0]["id"] == 2.0
    for changed in (
        {**args, "prompt": "x"}, {**args, "prompt": "x" * 21},
        {**args, "researchers": []}, {**args, "researchers": [{"id": 1}] * 3},
        {**args, "researchers": [{"id": True}]},
        {**args, "researchers": [{"id": 1, "unexpected": 2}]},
        {**args, "flag": "yes"}, {"prompt": "Explore"},
        {**args, "extra": True},
    ):
        with pytest.raises(ValueError, match="args"):
            validate_args(changed, schema)


def test_numeric_bounds_exclude_booleans_and_honor_exclusive_boundaries():
    schema = object_schema({"score": {"type": "number", "minimum": 0, "maximum": 10,
                                      "exclusiveMinimum": 1, "exclusiveMaximum": 9}},
                           required=["score"])
    assert validate_args({"score": 1.5}, schema) == {"score": 1.5}
    for score in (True, 0, 1, 9, 10, -1, math.inf, math.nan):
        with pytest.raises(ValueError):
            validate_args({"score": score}, schema)


def test_enum_and_const_use_json_equality_not_python_boolean_equality():
    schema = object_schema({"choice": {"enum": [1, True, {"kind": ["a", 2]}]},
                            "constant": {"const": {"ok": True}}},
                           required=["choice", "constant"])
    assert validate_args({"choice": 1.0, "constant": {"ok": True}}, schema)
    assert validate_args({"choice": True, "constant": {"ok": True}}, schema)
    assert validate_args({"choice": {"kind": ["a", 2]}, "constant": {"ok": True}}, schema)
    for args in ({"choice": False, "constant": {"ok": True}},
                 {"choice": 1, "constant": {"ok": 1}}):
        with pytest.raises(ValueError):
            validate_args(args, schema)


@pytest.mark.parametrize("keyword,value", [
    ("$ref", "https://example.com/schema"), ("$defs", {}), ("pattern", "(a+)+$"),
    ("format", "email"), ("allOf", []), ("oneOf", []), ("anyOf", []),
    ("not", {}), ("examples", ["unsupported"]), ("$schema", "draft"),
    ("unevaluatedProperties", False), ("additionalItems", False),
])
def test_unsupported_keywords_are_explicit_errors_even_when_nested(keyword, value):
    with pytest.raises(ValueError, match="unsupported JSON Schema keyword"):
        validate_input_schema(object_schema({"nested": {keyword: value}}))


@pytest.mark.parametrize("schema", [
    {}, [], True, {"type": "array"}, {"type": ["object", "null"]},
    object_schema({"x": {"type": "unsupported"}}),
    object_schema({"x": {"type": ["string", "string"]}}),
    object_schema({"x": {"type": []}}),
    object_schema({"x": {"type": ["string", {}]}}),
    {"type": "object", "properties": []},
    {"type": "object", "properties": {"x": False}},
    object_schema(required=["x", "x"]), object_schema(required=[1]),
    object_schema(additionalProperties=[]), object_schema(items=[]),
    object_schema(minItems=True), object_schema(maxLength=-1),
    object_schema(minProperties=2, maxProperties=1),
    object_schema(minimum=True), object_schema(maximum=math.inf),
    object_schema(minimum=3, maximum=2),
    object_schema(exclusiveMinimum=2, maximum=2),
    object_schema(enum=[]), object_schema(enum=[1, 1.0]),
    object_schema(enum=[{"a": [1]}, {"a": [1.0]}]),
])
def test_malformed_schemas_are_rejected(schema):
    with pytest.raises(ValueError):
        validate_input_schema(schema)


def test_schema_and_args_json_budgets_bound_size_depth_and_nodes(monkeypatch):
    with pytest.raises(ValueError, match="UTF-8 bytes"):
        validate_input_schema(object_schema(const="é" * validation.MAX_SCHEMA_BYTES))
    with pytest.raises(ValueError, match="UTF-8 bytes"):
        validate_args({"x": "é" * validation.MAX_ARGS_BYTES}, None)
    deep = "leaf"
    for _ in range(validation.MAX_ARGS_DEPTH + 1):
        deep = [deep]
    with pytest.raises(ValueError, match="depth"):
        validate_args({"deep": deep}, None)
    deep_schema = {"type": "string"}
    for _ in range(validation.MAX_SCHEMA_DEPTH + 1):
        deep_schema = {"type": "object", "additionalProperties": deep_schema}
    with pytest.raises(ValueError, match="depth"):
        validate_input_schema(deep_schema)
    monkeypatch.setattr(validation, "MAX_ARGS_NODES", 5)
    with pytest.raises(ValueError, match="nodes"):
        validate_args({"x": [1, 2, 3, 4]}, None)
    monkeypatch.setattr(validation, "MAX_SCHEMA_NODES", 5)
    with pytest.raises(ValueError, match="nodes"):
        validate_input_schema(object_schema({"x": {"type": "string"}}))


@pytest.mark.parametrize("args", [None, [], True, {1: "key"}, {"x": {1, 2}},
                                       {"x": math.nan}, {"x": "\ud800"}])
def test_args_require_json_objects_and_finite_supported_values(args):
    with pytest.raises(ValueError):
        validate_args(args, None)


def test_mutable_aliases_and_cycles_do_not_escape_validation():
    schema = object_schema({"x": {"type": "string"}})
    checked = validate_input_schema(schema)
    schema["properties"]["x"]["type"] = "number"
    assert checked["properties"]["x"]["type"] == "string"
    cyclic = {}
    cyclic["self"] = cyclic
    with pytest.raises(ValueError, match="depth"):
        validate_args(cyclic, None)


def test_schema_annotations_are_preserved_and_defaults_are_validated_without_application():
    schema = object_schema({
        "topic": {"type": "string", "title": "Research topic", "description": "What to research",
                  "minLength": 2, "default": "Energy"},
        "settings": {"type": "object", "properties": {"depth": {"type": "integer", "default": 2}},
                     "default": {"depth": 3}},
    }, title="Research inputs", description="Configure the workflow", default={"topic": "Energy"})
    assert validate_input_schema(schema) == schema
    assert validate_args({}, schema) == {}
    assert validate_input_schema(object_schema({"nullable": {"type": ["string", "null"], "default": None}}))


@pytest.mark.parametrize("annotation,value", [
    ("title", 1), ("title", "x" * 121), ("description", None), ("description", "x" * 2001),
])
def test_schema_annotations_reject_wrong_types_and_excessive_lengths(annotation, value):
    with pytest.raises(ValueError, match=annotation):
        validate_input_schema(object_schema({"field": {annotation: value}}))


@pytest.mark.parametrize("field", [
    {"type": "integer", "default": True},
    {"type": "string", "minLength": 2, "default": "x"},
    {"type": "number", "exclusiveMinimum": 0, "default": 0},
    {"type": "array", "items": {"type": "boolean"}, "default": [1]},
    {"type": "object", "required": ["name"], "default": {}},
    {"enum": ["one", "two"], "default": "three"},
    {"type": "object", "additionalProperties": False, "default": {"unknown": 1}},
])
def test_defaults_must_satisfy_their_own_schema(field):
    with pytest.raises(ValueError, match="default"):
        validate_input_schema(object_schema({"field": field}))


def test_defaults_remain_within_schema_json_budget():
    with pytest.raises(ValueError, match="finite"):
        validate_input_schema(object_schema({"field": {"default": math.nan}}))
    with pytest.raises(ValueError, match="UTF-8 bytes"):
        validate_input_schema(object_schema({"field": {"default": "x" * validation.MAX_SCHEMA_BYTES}}))
