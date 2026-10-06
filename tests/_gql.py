"""Helpers for driving the GraphQL endpoint in tests.

The app-facing REST surface was migrated to GraphQL; these helpers keep test
code terse while exercising the real /graphql route. Errors that were HTTP
status codes under REST now arrive as ``errors[].extensions.http_status``.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient


def gql(
    client: TestClient,
    query: str,
    variables: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
):
    return client.post(
        "/graphql",
        json={"query": query, "variables": variables or {}},
        headers=headers or {},
    )


def data(
    client: TestClient,
    query: str,
    field: str | None = None,
    variables: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    """Run a query/mutation expected to succeed; return data or one field."""
    res = gql(client, query, variables, headers)
    body = res.json()
    assert res.status_code == 200 and not body.get("errors"), body
    if field is None:
        return body["data"]
    return body["data"][field]


def err_status(
    client: TestClient,
    query: str,
    variables: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> int:
    """HTTP-equivalent status for a failing operation (extensions.http_status)."""
    res = gql(client, query, variables, headers)
    body = res.json()
    errs = body.get("errors") or []
    if errs:
        return int(errs[0].get("extensions", {}).get("http_status", 500))
    return res.status_code
