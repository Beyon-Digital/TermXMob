"""Schema assembly — root Query/Mutation/Subscription + SDL export.

Run ``python -m termx.graphql.schema`` to print the SDL (used by the frontend
Relay compiler).
"""

from __future__ import annotations

import strawberry
from strawberry.schema.config import StrawberryConfig

from termx.graphql.domains.agent import AgentMutations, AgentQueries
from termx.graphql.domains.bot_messages import BotMessageMutations, BotMessageQueries
from termx.graphql.domains.bot_schedules import BotScheduleMutations, BotScheduleQueries
from termx.graphql.domains.bots import BotMutations, BotQueries
from termx.graphql.domains.chatgpt import ChatGPTQueries, ChatGPTMutations
from termx.graphql.domains.chat import ChatMutations, ChatQueries
from termx.graphql.domains.core import CoreMutations, CoreQueries
from termx.graphql.domains.desktop_control import DesktopControlMutations, DesktopControlQueries
from termx.graphql.domains.files import FilesMutations, FilesQueries
from termx.graphql.domains.memory import MemoryMutations, MemoryQueries
from termx.graphql.domains.network import NetworkMutations, NetworkQueries
from termx.graphql.domains.runbooks import RunbookMutations, RunbookQueries
from termx.graphql.domains.skills import SkillMutations, SkillQueries
from termx.graphql.domains.voice import VoiceMutations, VoiceQueries
from termx.graphql.domains.workspace import WorkspaceMutations, WorkspaceQueries
from termx.graphql.domains.desktop_workspace import DesktopWorkspaceQueries, ArtifactNode, WorkspaceTurnNode
from termx.graphql.subscriptions import Subscription


@strawberry.type
class Query(
    DesktopWorkspaceQueries,
    DesktopControlQueries,
    CoreQueries,
    WorkspaceQueries,
    FilesQueries,
    NetworkQueries,
    AgentQueries,
    ChatQueries,
    ChatGPTQueries,
    RunbookQueries,
    MemoryQueries,
    SkillQueries,
    BotQueries,
    BotScheduleQueries,
    BotMessageQueries,
    VoiceQueries,
):
    pass


@strawberry.type
class Mutation(
    DesktopControlMutations,
    CoreMutations,
    WorkspaceMutations,
    FilesMutations,
    NetworkMutations,
    AgentMutations,
    ChatMutations,
    ChatGPTMutations,
    RunbookMutations,
    MemoryMutations,
    SkillMutations,
    BotMutations,
    BotScheduleMutations,
    BotMessageMutations,
    VoiceMutations,
):
    pass


def build_schema() -> strawberry.Schema:
    return strawberry.Schema(
        query=Query,
        mutation=Mutation,
        subscription=Subscription,
        types=[ArtifactNode, WorkspaceTurnNode],
        config=StrawberryConfig(auto_camel_case=False),
    )


schema = build_schema()


if __name__ == "__main__":
    print(str(schema))
