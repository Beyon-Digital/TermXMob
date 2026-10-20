"""Character billing and persistence boundaries under authority changes."""

import asyncio
from io import BytesIO
from threading import Event
from types import SimpleNamespace

from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
import pytest

from termx.app import AppState, create_app
from termx.characters.service import CharacterService
from termx.media.service import ArtifactService
import termx.characters.service as character_service_module


@pytest.fixture
def http_host():
    state = AppState(passcode="character-security-bootstrap")
    app = create_app(state, web_dir=None)
    owner = state.identity.setup_owner("character-security-owner", "character-security-password-123")
    login = asyncio.run(state.identity.login("local-password", {
        "username": "character-security-owner", "password": "character-security-password-123"}, peer="local"))
    headers = {"Authorization": "Bearer " + login.access_token}
    yield state, owner, TestClient(app), headers
    asyncio.run(state.characters.close())


@pytest.fixture
def host(tmp_path):
    # Authoring boundaries need the real stores/compiler, without unrelated
    # password hashing or initializing a complete desktop/browser runtime.
    state = SimpleNamespace(agent=SimpleNamespace(_listeners=set()),
                            artifacts=ArtifactService(tmp_path / "artifacts"),
                            media=SimpleNamespace(), delivery=SimpleNamespace(directory=tmp_path / "delivery"))
    state.characters = CharacterService(state, tmp_path / "characters")
    owner = SimpleNamespace(id="character-security-owner")
    yield state, owner, None, None
    asyncio.run(state.characters.close())


def creation_args(**changes):
    return {"request_id": "character-security-operation", "provider_id": "fixture", "model": "image-fixture",
            "name": "My robot", "prompt": "Original mint robot", "phase": "reference",
            "acknowledge_billing": True, **changes}


def sheet_artifact(state, owner):
    image = Image.new("RGBA", (256, 128))
    draw = ImageDraw.Draw(image)
    for index in range(8):
        x, y = index % 4 * 64, index // 4 * 64
        draw.rounded_rectangle((x + 16, y + 10, x + 48, y + 50), radius=8, fill="teal")
    target = BytesIO()
    image.save(target, format="PNG")
    return state.artifacts.create(owner.id, None, "image", "Sprite source", blob=target.getvalue(), mime="image/png")


def test_generation_needs_explicit_billing_consent_without_creating_operation(host):
    state, owner, _, _ = host
    with pytest.raises(HTTPException) as refused:
        state.characters.generate(owner.id, creation_args(acknowledge_billing=False), authority=lambda: None)
    assert refused.value.status_code == 409
    with pytest.raises(KeyError):
        state.characters.operation(owner.id, "character-security-operation")


def test_revocation_before_background_job_prevents_provider_charge(host):
    state, owner, _, _ = host
    charges = []
    async def generate(*args):
        charges.append(args)
        return {"id": "should-not-be-called"}
    state.media.generate = generate
    def revoked():
        raise PermissionError("Authority revoked")
    async def run():
        state.characters.generate(owner.id, creation_args(), authority=revoked)
        await asyncio.gather(*state.characters._jobs)
    asyncio.run(run())
    assert charges == []
    assert state.characters.operation(owner.id, "character-security-operation")["status"] == "failed"


def test_revocation_after_provider_output_prevents_draft_and_repeated_charge(host):
    state, owner, _, _ = host
    revoked, charges = [False], []
    async def generate(*args):
        charges.append(args)
        revoked[0] = True
        return {"id": "already-charged-provider-result"}
    state.media.generate = generate
    def authority():
        if revoked[0]:
            raise PermissionError("Authority revoked")
    async def run():
        state.characters.generate(owner.id, creation_args(), authority=authority)
        await asyncio.gather(*state.characters._jobs)
        result = state.characters.generate(owner.id, creation_args(), authority=authority)
        assert result["status"] == "failed"
    asyncio.run(run())
    assert len(charges) == 1
    assert len(state.characters.library(owner.id)["packs"]) == 1


def test_clip_generation_rechecks_authority_after_compilation_before_persisting(host, monkeypatch):
    state, owner, _, _ = host
    artifact = sheet_artifact(state, owner)
    revoked = [False]
    original_compile = character_service_module.compile_sheet
    def compile_then_revoke(*args, **kwargs):
        result = original_compile(*args, **kwargs)
        revoked[0] = True
        return result
    monkeypatch.setattr(character_service_module, "compile_sheet", compile_then_revoke)
    async def generate(*args):
        return artifact
    state.media.generate = generate
    def authority():
        if revoked[0]:
            raise PermissionError("Authority revoked while compiling")
    async def run():
        state.characters.generate(owner.id, creation_args(phase="clip", reference_id=artifact["id"]), authority=authority)
        await asyncio.gather(*state.characters._jobs)
    asyncio.run(run())
    assert len(state.characters.library(owner.id)["packs"]) == 1
    assert state.characters.operation(owner.id, "character-security-operation")["status"] in {"failed", "unknown"}


def test_cancelled_compilation_cannot_persist_a_late_revision(host, monkeypatch):
    state, owner, _, _ = host
    artifact = sheet_artifact(state, owner)
    compiling, release, finished = Event(), Event(), Event()
    original_compile = character_service_module.compile_sheet
    original_build = state.characters.build
    def blocked_compile(*args, **kwargs):
        compiling.set()
        assert release.wait(5)
        return original_compile(*args, **kwargs)
    def observed_build(*args, **kwargs):
        try:
            return original_build(*args, **kwargs)
        finally:
            finished.set()
    monkeypatch.setattr(character_service_module, "compile_sheet", blocked_compile)
    monkeypatch.setattr(state.characters, "build", observed_build)
    async def generate(*args):
        return artifact
    state.media.generate = generate
    async def run():
        state.characters.generate(owner.id, creation_args(phase="clip", reference_id=artifact["id"]), authority=lambda: None)
        assert await asyncio.to_thread(compiling.wait, 5)
        state.characters.cancel(owner.id, "character-security-operation")
        release.set()
        await asyncio.gather(*state.characters._jobs, return_exceptions=True)
        assert await asyncio.to_thread(finished.wait, 5)
    asyncio.run(run())
    assert len(state.characters.library(owner.id)["packs"]) == 1
    assert state.characters.operation(owner.id, "character-security-operation")["status"] == "unknown"


def test_accepted_revision_and_new_draft_survive_service_restart(host):
    state, owner, _, _ = host
    service = state.characters
    manifest, assets = service.pack(owner.id, "termx-robot")
    draft = service.save_pack(owner.id, manifest, assets)
    service.accept(owner.id, draft["id"], draft["revision"])
    service.edit_clip(owner.id, draft["id"], "walk", 230)
    directory = state.delivery.directory.parent / "characters"
    asyncio.run(service.close())
    state.characters = CharacterService(state, directory)
    state.agent.characters = state.characters
    metadata = state.characters.metadata(owner.id, draft["id"])
    assert metadata["accepted_revision"] == 1
    assert metadata["revision"] == 2 and metadata["accepted"] is False
    assert state.characters.pack(owner.id, draft["id"], 1)[0]["clips"]["walk"]["frames"][0]["duration_ms"] != 230
    assert state.characters.pack(owner.id, draft["id"], 2)[0]["clips"]["walk"]["frames"][0]["duration_ms"] == 230


def test_rejected_provider_png_preserves_accepted_revision(host):
    state, owner, _, _ = host
    service = state.characters
    manifest, assets = service.pack(owner.id, "termx-robot")
    draft = service.save_pack(owner.id, manifest, assets)
    service.accept(owner.id, draft["id"], 1)
    image = Image.new("RGB", (256, 128), "white")
    target = BytesIO()
    image.save(target, format="PNG")
    invalid = state.artifacts.create(owner.id, None, "image", "Opaque provider output", blob=target.getvalue(), mime="image/png")
    async def generate(*args):
        return invalid
    state.media.generate = generate
    async def run():
        service.generate(owner.id, creation_args(phase="clip", reference_id=invalid["id"], pack_id=draft["id"]), authority=lambda: None)
        await asyncio.gather(*service._jobs)
    asyncio.run(run())
    metadata = service.metadata(owner.id, draft["id"])
    assert metadata["accepted_revision"] == metadata["revision"] == 1
    assert service.operation(owner.id, "character-security-operation")["status"] == "failed"


def test_missing_sheet_artifact_is_an_http_not_found(http_host):
    _, _, client, headers = http_host
    response = client.post("/api/characters/build", headers=headers, json={"artifact_id": "missing", "name": "Robot"})
    assert response.status_code == 404


@pytest.mark.parametrize("cancelled", [False, True])
def test_agent_generation_rechecks_originating_managed_session_and_task_cancel(host, cancelled):
    from termx.agent.providers import ProviderCall
    from termx.agent.tools.characters import execute
    state, owner, _, _ = host
    principal = SimpleNamespace(id=owner.id, policy_version=1)
    valid = [True]
    charges = []
    origin = {"principal_id": owner.id, "session_id": "managed-fixture", "project_id": None, "policy_version": 1}
    state.browser = SimpleNamespace(records=SimpleNamespace(get=lambda kind, identifier: origin))
    state.identity = SimpleNamespace(execution_session=lambda identifier: SimpleNamespace(principal=principal) if valid[0] else None)
    state.workspace = SimpleNamespace(
        store=SimpleNamespace(get=lambda kind, identifier: {"owner": owner.id, "project_id": None}),
        record=lambda *args, **kwargs: None, validate_task_execution=lambda task_id: None)
    async def generate(*args):
        charges.append(args)
        valid[0] = False
        return {"id": "charged-result-after-session-revocation"}
    state.media.generate = generate
    call = ProviderCall("function", "fixture-call", "character_generate", {
        "provider_id": "fixture", "model": "image-fixture", "prompt": "Original robot", "name": "Robot"})
    async def run():
        cancel = asyncio.Event()
        if cancelled:
            cancel.set()
        ctx = SimpleNamespace(manager=SimpleNamespace(characters=state.characters), task_id="enrolled-task",
                              read_only=False, cancel=cancel)
        outcome = await execute(call, ctx)
        if cancelled:
            assert outcome.result["refused"] is True
        else:
            assert outcome.result["operation"]["status"] in {"failed", "unknown"}
    asyncio.run(run())
    assert len(charges) == (0 if cancelled else 1)
    assert len(state.characters.library(owner.id)["packs"]) == 1
