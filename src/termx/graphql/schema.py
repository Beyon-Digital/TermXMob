"""Schema assembly — root Query/Mutation/Subscription + SDL export.

Run ``python -m termx.graphql.schema`` to print the SDL (used by the frontend
Relay compiler).
"""

from __future__ import annotations

import strawberry
from strawberry.schema.config import StrawberryConfig

from termx.graphql.domains.agent import AgentMutations, AgentQueries
from termx.graphql.domains.chatgpt import ChatGPTQueries, ChatGPTMutations
from termx.graphql.domains.chat import ChatMutations, ChatQueries
from termx.graphql.domains.core import CoreMutations, CoreQueries
from termx.graphql.domains.files import FilesMutations, FilesQueries
from termx.graphql.domains.network import NetworkMutations, NetworkQueries
from termx.graphql.domains.runbooks import RunbookMutations, RunbookQueries
from termx.graphql.domains.workspace import WorkspaceMutations, WorkspaceQueries
from termx.graphql.subscriptions import Subscription


@strawberry.type
class Query(
    CoreQueries,
    WorkspaceQueries,
    FilesQueries,
    NetworkQueries,
    AgentQueries,
    ChatQueries,
    ChatGPTQueries,
    RunbookQueries,
):
    pass


@strawberry.type
class Mutation(
    CoreMutations,
    WorkspaceMutations,
    FilesMutations,
    NetworkMutations,
    AgentMutations,
    ChatMutations,
    ChatGPTMutations,
    RunbookMutations,
):
    pass


def build_schema() -> strawberry.Schema:
    return strawberry.Schema(
        query=Query,
        mutation=Mutation,
        subscription=Subscription,
        config=StrawberryConfig(auto_camel_case=False),
    )


schema = build_schema()


if __name__ == "__main__":
    print(str(schema))
