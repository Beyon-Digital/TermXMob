"""Voice domain: the owner's voice settings and what each engine can do."""

from __future__ import annotations

import strawberry
from strawberry.scalars import JSON
from strawberry.types import Info

from termx.graphql import types as T
from termx.graphql.context import TermxContext
from termx.graphql.errors import resolver
from termx.voice.http import voice_owner

Ctx = Info[TermxContext, None]

# Settings that choose an account to receive audio or text, stamp consent to send it, or keep recordings.
DESTINATION_FIELDS = frozenset({"provider", "live_provider", "consent_provider", "consent_live", "cloud_fallback", "keep_audio"})


@strawberry.type
class VoiceStatus(T.DictBacked):
    available: bool
    settings: JSON
    engines: JSON
    stt: JSON
    live: JSON


@strawberry.input
class VoiceSettingsInput:
    engine: str | None = None
    provider: str | None = None
    model: str | None = None
    transcribe_model: str | None = None
    voice: str | None = None
    auto_send: bool | None = None
    auto_speak: bool | None = None
    cloud_fallback: bool | None = None
    keep_audio: bool | None = None
    live_provider: str | None = None
    live_model: str | None = None
    live_voice: str | None = None
    live_transcribe_model: str | None = None
    # The host stamps consent itself. These only say what the owner just agreed to.
    consent_provider: bool | None = None
    consent_live: bool | None = None
    accept_woof_license: str | None = None


def _owner(ctx: TermxContext, scope: str) -> str:
    return voice_owner(ctx.state, ctx.secret, scope)


@strawberry.type
class VoiceQueries:
    @strawberry.field
    @resolver
    def voice_status(self, info: Ctx) -> VoiceStatus:
        ctx = info.context
        return VoiceStatus.wrap(ctx.state.voice.status(_owner(ctx, "agent-view")))


@strawberry.type
class VoiceMutations:
    @strawberry.mutation
    @resolver
    def save_voice_settings(self, info: Ctx, input: VoiceSettingsInput) -> VoiceStatus:
        ctx = info.context
        owner = _owner(ctx, "agent-run")
        changes = {key: value for key, value in vars(input).items() if value is not strawberry.UNSET}
        if DESTINATION_FIELDS & {key for key, value in changes.items() if value is not None}:
            ctx.require_host("agent-control")  # where the owner's audio goes, and the consent to send it, is the owner's own decision
        ctx.state.voice.save_settings(owner, changes)
        return VoiceStatus.wrap(ctx.state.voice.status(owner))
