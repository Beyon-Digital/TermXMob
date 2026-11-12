"""Bounded workflow proof on immutable UI bytes and a scratch authenticated host.

Run: .venv/bin/python -B tests/workspace_workflows_e2e.py OUTPUT UI_DIST [--baseline]
Only provider responses are scripted in process. Actual JS workers, task plans,
user approval, scratch shell, password/CSRF and stores remain production paths.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import uvicorn
from playwright.sync_api import expect, sync_playwright

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))
import workspace_studio_projects_e2e as fixture
from test_workflow_integration import AGENT_SOURCE, INPUT_SCHEMA, PASSWORD, PREFIX, PURE_SOURCE, USERNAME, WORKFLOW_TOOL_PROMPT, build_state
from workspace_ui_snapshot import snapshot_ui


@contextmanager
def isolated_workflow_host(ui_dist, report):
    scratch = Path(tempfile.mkdtemp(prefix="termx-workflow-qa-"))
    keys = ("TERMX_CONFIG_DIR", "TERMX_AGENTS_DIR", "TERMX_ENGINE_STARTUP_REFRESH")
    old = {key: os.environ.get(key) for key in keys}
    server = worker = listener = client = None
    try:
        os.environ.update(TERMX_CONFIG_DIR=str(scratch / "config"), TERMX_AGENTS_DIR=str(scratch / "agents"), TERMX_ENGINE_STARTUP_REFRESH="0")
        from termx.app import create_app
        state, owner, project, calls = build_state(scratch)
        app = create_app(state, web_dir=snapshot_ui(scratch, Path(ui_dist)))
        state.workspace.orchestrator.ensure(owner)
        def provider(request):
            report["backend_requests"].append({"method": request.method, "path": request.url.path})
            if request.method == "GET" and request.url.path in ("/api/v1/models", "/api/v1/images/models", "/api/v1/videos/models"):
                return httpx.Response(503, json={"error": "Synthetic offline catalogue"})
            raise AssertionError("Unexpected provider request in workflow proof")
        client = httpx.AsyncClient(transport=httpx.MockTransport(provider))
        state.studio.openrouter.client = client
        state.studio.client = client
        manifest = json.loads((scratch / "fixture-ui-snapshot.json").read_text())
        digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
        assert report.get("ui_manifest_digest", digest) == digest, "UI candidate changed between cases"
        report["ui_manifest_digest"] = digest
        (Path(report["output"]) / "ui-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        base = "http://127.0.0.1:" + str(listener.getsockname()[1])
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on", access_log=False))
        worker = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        worker.start()
        fixture.wait(lambda: server.started, seconds=30)
        yield state, owner, project, calls, base, scratch
        assert (Path(project["path"]) / "source.txt").read_text() == "Owned synthetic workflow source remains unchanged.\n"
    finally:
        if server:
            server.should_exit = True
        if worker:
            worker.join(timeout=15)
            assert not worker.is_alive(), "Scratch host did not stop"
        if listener:
            listener.close()
        if client:
            failures = []
            def close_client():
                try:
                    asyncio.run(client.aclose())
                except BaseException as error:
                    failures.append(error)
            cleanup = threading.Thread(target=close_client)
            cleanup.start()
            cleanup.join(timeout=5)
            assert not cleanup.is_alive() and not failures, failures
        shutil.rmtree(scratch, ignore_errors=True)
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def api(page, path, method="GET", body=None):
    response = page.evaluate("""async ({path,method,body})=>{const csrf=document.cookie.split('; ').find(row=>row.startsWith('termx_csrf='))?.split('=')[1];const r=await fetch(path,{method,credentials:'same-origin',headers:{'Content-Type':'application/json','X-Termx-CSRF':decodeURIComponent(csrf||'')},...(body===null?{}:{body:JSON.stringify(body)})});return{status:r.status,body:await r.json()}}""", {"path": path, "method": method, "body": body})
    assert response["status"] == 200, response
    return response["body"]


def audit(page, out, report, name, theme, width):
    page.wait_for_timeout(60)
    metrics = page.evaluate("""()=>{const shown=e=>{const r=e.getBoundingClientRect();return r.width&&r.height&&!e.closest('[inert]')},rect=e=>e.getBoundingClientRect().toJSON();return{documentOverflow:document.documentElement.scrollWidth>innerWidth,dialogs:[...document.querySelectorAll('[role=dialog]')].filter(shown).map(e=>({class:e.className,border:getComputedStyle(e).borderWidth,rect:rect(e),containsFocus:e.contains(document.activeElement)})),focus:{tag:document.activeElement?.tagName,outline:getComputedStyle(document.activeElement).outline,offset:getComputedStyle(document.activeElement).outlineOffset},horizontalScrollContainers:[...document.querySelectorAll('.workflows-workspace *,[role=dialog] *')].filter(shown).filter(e=>['auto','scroll'].includes(getComputedStyle(e).overflowX)&&e.scrollWidth>e.clientWidth+1).map(e=>({class:e.className,scroll:e.scrollWidth,client:e.clientWidth})),controls:[...document.querySelectorAll('.workflows-workspace input,.workflows-workspace select,[role=dialog] input,[role=dialog] textarea')].filter(shown).map(e=>({tag:e.tagName,font:getComputedStyle(e).fontSize,height:rect(e).height})),motion:[...document.querySelectorAll('.workflow-call-trigger>.disclosure-chevron')].map(e=>getComputedStyle(e).transitionDuration)}}""")
    axe = REPO / "desktop/workspace/node_modules/axe-core/axe.min.js"
    if axe.is_file():
        if not page.evaluate("typeof axe!=='undefined'"):
            page.add_script_tag(path=str(axe))
        result = page.evaluate("async()=>await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}})")
        metrics["axe"] = {key: [{"id": value["id"], "targets": [node["target"] for node in value["nodes"]]} for value in result[key]] for key in ("violations", "incomplete")}
    filename = f'{report["mode"]}-{name}-{theme}-{width}.png'
    page.screenshot(path=str(out / filename), full_page=True)
    item = {"state": name, "theme": theme, "width": width, "screenshot": filename, **metrics}
    report["states"].append(item)
    (out / "partial-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"capture": filename}), flush=True)
    assert not metrics["documentOverflow"], metrics
    if report["mode"] == "after":
        assert not metrics.get("axe", {}).get("violations"), metrics.get("axe")
        assert not metrics["horizontalScrollContainers"], metrics["horizontalScrollContainers"]
        for dialog in metrics["dialogs"]:
            box = dialog["rect"]
            assert box["x"] >= -1 and box["right"] <= width + 1 and box["y"] >= -1 and box["bottom"] <= page.viewport_size["height"] + 1, box
            assert dialog["containsFocus"], dialog
            if re.search(r"workflow-(?:editor|input|confirm)-dialog", dialog["class"]):
                assert dialog["border"] == "0px", dialog
        for control in metrics["controls"]:
            assert control["font"] == "16px", control
        assert all(value in ("0s", "0s, 0s") for value in metrics["motion"]), metrics["motion"]
    return item


def authenticate(page, base, baseline):
    page.goto(base + "/manage/" + ("projects" if baseline else "workflows") + "?workspace=agent")
    page.get_by_label("Username", exact=True).fill(USERNAME)
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Continue with password", exact=True).click()
    expect(page.get_by_role("heading", name="Projects" if baseline else "Workflows", exact=True)).to_be_visible(timeout=60000)
    nav = page.get_by_role("navigation", name="Workspace navigation", exact=True)
    if page.viewport_size["width"] < 640 and nav.get_by_role("button", name="Hide sidebar", exact=True).is_visible():
        nav.get_by_role("button", name="Hide sidebar", exact=True).click()


def keyboard_dialog(page, dialog):
    expect(dialog).to_be_visible()
    assert dialog.evaluate("e=>e.contains(document.activeElement)")
    for key in ("Tab", "Tab", "Shift+Tab", "Shift+Tab"):
        page.keyboard.press(key)
        assert dialog.evaluate("e=>e.contains(document.activeElement)"), "Dialog focus escaped"


def approve_initial_plan(page):
    direct = page.get_by_role("button", name="Approve plan", exact=True)
    if direct.is_visible():
        direct.click()
        return
    # Legacy Agent-mode planning uses the normal general review card; Plan
    # mode's editable plan card alone exposes the direct Approve plan button.
    page.get_by_role("button", name="Review action", exact=True).click(timeout=15000)
    review = page.get_by_role("dialog", name="Review action", exact=True)
    keyboard_dialog(page, review)
    review.get_by_text("Exact operation and host policy", exact=True).click()
    expect(review.get_by_role("region", name="Approval details", exact=True)).to_contain_text("Verify the owned scratch command")
    review.get_by_role("button", name="Allow once", exact=True).click()


def write_source(dialog, source):
    dialog.locator('.cm-content[contenteditable="true"]').fill(source)


def create_in_editor(page, name, source, schema=None):
    page.get_by_role("button", name="New workflow", exact=True).click()
    dialog = page.get_by_role("dialog", name="New workflow", exact=True)
    dialog.get_by_role("textbox", name="Name", exact=True).fill(name)
    dialog.get_by_role("textbox", name="Description", exact=True).fill("")
    write_source(dialog, source)
    schema = schema if schema is not None else {"type": "object", "properties": {}, "additionalProperties": False}
    dialog.get_by_text("Inputs", exact=True).click()
    dialog.get_by_role("textbox", name="Workflow input schema", exact=True).fill(json.dumps(schema, indent=2))
    return dialog


def open_library(page, base):
    page.goto(base + "/manage/workflows?workspace=agent")
    expect(page.get_by_role("heading", name="Workflows", exact=True)).to_be_visible(timeout=15000)


def exercise(page, context, state, owner, project, calls, base, out, report, theme, width):
    panel = page.get_by_role("region", name="Workflows workspace", exact=True)
    expect(panel.get_by_text("Create a workflow to coordinate repeatable work.", exact=True)).to_be_visible()
    views = page.get_by_role("navigation", name="Workspace views", exact=True)
    expect(views.get_by_role("button", name="Workflows", exact=True)).to_be_visible()
    if width == 1280 and theme == "dark":
        audit(page, out, report, "empty-library", theme, width)
    dialog = create_in_editor(page, "Compute values", PURE_SOURCE, INPUT_SCHEMA)
    keyboard_dialog(page, dialog)
    dialog.get_by_role("button", name="Validate", exact=True).click()
    expect(dialog.get_by_role("status")).to_contain_text("Workflow is valid.", timeout=15000)
    # Keyboard-visible inset focus and scroll stay within the editor.
    source = dialog.locator('.cm-content[contenteditable="true"]')
    source.focus()
    inset = dialog.locator(".workflow-source-editor").evaluate("e=>({outline:getComputedStyle(e).outline,offset:getComputedStyle(e).outlineOffset})")
    assert "2px" in inset["outline"] and inset["offset"] == "-2px", inset
    viewport = dialog.get_by_role("region", name="Workflow source viewport", exact=True)
    viewport.focus()
    scroll = viewport.evaluate("e=>({total:e.scrollHeight,client:e.clientHeight,top:e.scrollTop,tabIndex:e.tabIndex})")
    assert scroll["tabIndex"] == 0
    if scroll["total"] > scroll["client"] + 1:
        page.keyboard.press("End")
        fixture.wait(lambda: viewport.evaluate("e=>e.scrollTop") > 0, page, seconds=3)
        scroll["after_End"] = viewport.evaluate("e=>e.scrollTop")
        page.keyboard.press("Home")
        fixture.wait(lambda: viewport.evaluate("e=>e.scrollTop") == 0, page, seconds=3)
    audit(page, out, report, "validated-editor", theme, width)
    dialog.get_by_role("button", name="Save", exact=True).click()
    dialog.wait_for(state="detached")
    expect(panel.get_by_role("button", name="Compute values JavaScript workflow", exact=True)).to_be_visible(timeout=15000)
    panel.get_by_role("button", name="Run", exact=True).click()
    inputs = page.get_by_role("dialog", name="Compute values", exact=True)
    keyboard_dialog(page, inputs)
    inputs.get_by_role("spinbutton").fill("6")
    audit(page, out, report, "run-inputs", theme, width)
    inputs.get_by_role("button", name="Start", exact=True).click()
    inputs.wait_for(state="detached")
    run = page.get_by_role("region", name="Workflow run", exact=True)
    expect(run.get_by_role("heading", name="Result", exact=True)).to_be_visible(timeout=15000)
    expect(run.locator(".workflow-run-meta")).to_contain_text("Completed")
    # Result is production JSON rendering; compare authoritative run independently.
    runs = api(page, PREFIX + "/runs")["runs"]
    completed = next(row for row in runs if row["name"] == "Compute values")
    assert completed["status"] == "completed" and completed["result"] == {"values": [0, 5, 10], "total": 15, "globals": ["undefined"] * 3}
    audit(page, out, report, "completed-result", theme, width)
    if width == 1280 and theme == "dark":
        chevron = run.locator(".workflow-call-trigger>.disclosure-chevron").first
        page.emulate_media(reduced_motion="no-preference")
        normal = chevron.evaluate("e=>({duration:getComputedStyle(e).transitionDuration,property:getComputedStyle(e).transitionProperty})")
        assert normal["property"] == "transform" and all(.12 <= float(value.strip().removesuffix("s")) <= .2 for value in normal["duration"].split(",")), normal
        page.emulate_media(reduced_motion="reduce")
        reduced = chevron.evaluate("e=>getComputedStyle(e).transitionDuration")
        assert reduced == "0s", reduced
        report["checks"].append({"check": "workflow-disclosure-motion", "normal": normal, "reduced": reduced})
    report["checks"].append({"check": "real-browser-save-schema-validate-QuickJS-run-result", "theme": theme, "width": width, "run_id": completed["id"], "result": completed["result"], "source_focus": inset, "source_keyboard_scroll": scroll})
    if width == 1280 and theme == "dark":
        desktop_failure_journeys(page, context, state, owner, project, calls, base, out, report, theme, width, completed)
    else:
        assert state.agent_store.list_tasks() == [] and calls == []
    receipt_journey(page, state, owner, project, base, out, report, theme, width)


def desktop_failure_journeys(page, context, state, owner, project, calls, base, out, report, theme, width, completed):
    run = page.get_by_role("region", name="Workflow run", exact=True)
    run.get_by_role("button", name="History", exact=True).click()
    history = page.get_by_role("region", name="Run history", exact=True)
    expect(history).to_contain_text("Completed")
    audit(page, out, report, "history", theme, width)
    panel = page.get_by_role("region", name="Workflows workspace", exact=True)
    panel.get_by_role("button", name="Edit", exact=True).click()
    editor = page.get_by_role("dialog", name="Edit workflow", exact=True)
    write_source(editor, "return (;")
    editor.get_by_role("button", name="Validate", exact=True).click()
    expect(editor.get_by_role("status")).not_to_contain_text("Workflow is valid.")
    audit(page, out, report, "syntax-error", theme, width)
    write_source(editor, PURE_SOURCE)
    definition = api(page, PREFIX)["workflows"][0]
    api(page, PREFIX + "/" + definition["id"], "PATCH", {"revision": definition["revision"], "changes": {"description": "Concurrent saved edit"}})
    editor.get_by_role("textbox", name="Description", exact=True).fill("Browser draft survives conflict")
    editor.get_by_role("button", name="Save", exact=True).click()
    expect(editor.get_by_role("alert")).to_contain_text("A newer version was saved. Your edits are kept.")
    expect(editor.get_by_role("textbox", name="Description", exact=True)).to_have_value("Browser draft survives conflict")
    audit(page, out, report, "revision-conflict-draft", theme, width)
    editor.get_by_role("button", name="Load saved version", exact=True).click()
    expect(editor.get_by_role("textbox", name="Description", exact=True)).to_have_value("Concurrent saved edit")
    editor.get_by_role("button", name="Cancel", exact=True).click()
    editor.wait_for(state="detached")
    open_library(page, base)
    # Cancel a real JavaScript promise waiting on a normally approved child.
    # Bare never-resolving promises fail explicitly with no_progress instead.
    panel.get_by_role("combobox", name="Filter workflows by project", exact=True).select_option(project["id"])
    expect(panel.get_by_text("Compute values", exact=True)).to_be_visible()
    editor = create_in_editor(page, "Wait for cancellation", AGENT_SOURCE)
    editor.get_by_role("button", name="Save", exact=True).click()
    editor.wait_for(state="detached")
    row = panel.locator(".workflow-library-list>li").filter(has=page.get_by_role("button", name="Wait for cancellation JavaScript workflow", exact=True))
    row.get_by_role("button", name="Run", exact=True).click()
    page.get_by_role("dialog", name="Wait for cancellation", exact=True).get_by_role("button", name="Start", exact=True).click()
    expect(run.get_by_role("button", name="Review", exact=True)).to_be_visible(timeout=15000)
    run.get_by_role("button", name="Stop", exact=True).click()
    expect(run.locator(".workflow-run-meta")).to_contain_text("Stopped", timeout=15000)
    stopped = next(row for row in api(page, PREFIX + "/runs")["runs"] if row["name"] == "Wait for cancellation")
    assert stopped["status"] == "cancelled"
    audit(page, out, report, "cancelled-agent-promise", theme, width)
    open_library(page, base)
    # Owned project binding is explicit; inference is the in-process scripted adapter.
    panel.get_by_role("combobox", name="Filter workflows by project", exact=True).select_option(project["id"])
    expect(panel.get_by_role("button", name="Wait for cancellation JavaScript workflow", exact=True)).to_be_visible()
    editor = create_in_editor(page, "Review scratch command", AGENT_SOURCE)
    editor.get_by_role("button", name="Save", exact=True).click()
    editor.wait_for(state="detached")
    agent_row = panel.locator(".workflow-library-list>li").filter(has=page.get_by_role("button", name="Review scratch command JavaScript workflow", exact=True))
    agent_row.get_by_role("button", name="Run", exact=True).click()
    page.get_by_role("dialog", name="Review scratch command", exact=True).get_by_role("button", name="Start", exact=True).click()
    expect(run.get_by_role("button", name="Review", exact=True)).to_be_visible(timeout=15000)
    awaiting = next(row for row in api(page, PREFIX + "/runs")["runs"] if row["name"] == "Review scratch command")
    child = next(call for call in awaiting["calls"] if call["operation"] == "agent")
    assert state.agent_store.get_task(child["task_id"])["status"] == "awaiting_approval"
    assert not any(e["type"] == "process.started" for e in state.agent_store.events(child["task_id"]))
    audit(page, out, report, "agent-approval-wait", theme, width)
    run.get_by_role("button", name="Review", exact=True).click()
    expect(page.get_by_role("textbox", name="Message", exact=True)).to_be_visible(timeout=15000)
    assert child["session_id"] in page.url, page.url
    audit(page, out, report, "exact-child-chat-approval", theme, width)
    approve_initial_plan(page)
    # A plan does not grant the shell call. Review the separately requested
    # exact operation in the existing child chat, with one-shot consent.
    fixture.wait(lambda: state.agent_store.get_task(child["task_id"])["status"] == "completed" or any(item["status"] == "pending" and item["kind"] == "tool" for item in state.agent_store.approvals(child["task_id"])), page, seconds=15)
    pending = [item for item in state.agent_store.approvals(child["task_id"]) if item["status"] == "pending" and item["kind"] == "tool"]
    if pending:
        assert len(pending) == 1 and pending[0]["kind"] == "tool", pending
        command = pending[0]["payload"].get("call", {})
        assert command.get("name") == "run_shell" and command.get("arguments", {}).get("command") == "printf workflow-shell-ok", pending
        page.get_by_role("button", name="Review action", exact=True).click()
        review = page.get_by_role("dialog", name="Review action", exact=True)
        keyboard_dialog(page, review)
        review.get_by_text("Exact operation and host policy", exact=True).click()
        expect(review.get_by_role("region", name="Approval details", exact=True)).to_contain_text("printf workflow-shell-ok")
        audit(page, out, report, "exact-tool-review", theme, width)
        review.get_by_role("button", name="Allow once", exact=True).click()
    fixture.wait(lambda: state.agent_store.get_task(child["task_id"])["status"] == "completed", page, seconds=15)
    assert any(e["type"] == "process.output" and "workflow-shell-ok" in e["payload"].get("chunk", "") for e in state.agent_store.events(child["task_id"]))
    final = api(page, PREFIX + "/runs/" + awaiting["id"])
    fixture.wait(lambda: api(page, PREFIX + "/runs/" + awaiting["id"])["status"] == "completed", page)
    commands = page.get_by_role("button", name="Workspace actions", exact=True)
    commands.focus()
    page.keyboard.press("Enter")
    actions = page.get_by_role("dialog", name="Workspace actions", exact=True)
    expect(actions).to_be_visible()
    actions.get_by_role("button", name="Commands", exact=True).click()
    command_dialog = page.get_by_role("dialog", name="Workspace commands", exact=True)
    expect(command_dialog).to_be_visible()
    command_dialog.get_by_role("button", name="Workflows", exact=True).click()
    expect(run.get_by_role("heading", name="Review scratch command", exact=True)).to_be_visible(timeout=15000)
    expect(run.locator(".workflow-run-meta")).to_contain_text("Completed", timeout=15000)
    assert api(page, PREFIX + "/runs/" + awaiting["id"])["calls"][0]["session_id"] == child["session_id"]
    report["checks"].append({"check": "actual-agent-QuickJS-send-normal-plan-approval-safe-shell", "run_id": awaiting["id"], "task_id": child["task_id"], "session_id": child["session_id"], "preapproval_process_events": 0, "safe_shell_output": "workflow-shell-ok", "scripted_provider_operations": calls})
    report["checks"].append({"check": "Review-exact-child-chat-keyboard-return-preserves-selected-workflow-run", "run_id": awaiting["id"], "same_child_session": child["session_id"], "return": "Commands → Workflows"})


def receipt_journey(page, state, owner, project, base, out, report, theme, width):
    # Explicit synthetic provider-availability metadata lets the normal composer
    # use the in-process adapter. CredentialStore stays empty; no model exists.
    state.agent_store.put_provider("offline-workflow", kind="openai-compatible", name="Offline workflow fixture", base_url="http://127.0.0.1:1/v1", model="fixture-workflow", capabilities=["shell"], secret_configured=True)
    parent = state.workspace.create_session(owner, title="Workflow tool receipt", project_id=project["id"], mode="agent", provider_id="offline-workflow", model="fixture-workflow")
    state._qa_allowed_turn_sessions = {parent["id"]}
    page.goto(base + "/?session=" + parent["id"] + "&workspace=agent&layout=chat")
    composer = page.get_by_role("textbox", name="Message", exact=True)
    expect(composer).to_be_visible(timeout=15000)
    composer.fill(WORKFLOW_TOOL_PROMPT)
    page.get_by_role("button", name="Send message", exact=True).click()
    expect(page.get_by_role("button", name="Review action", exact=True)).to_be_visible(timeout=15000)
    approve_initial_plan(page)
    page.get_by_role("button", name="Review action", exact=True).click(timeout=15000)
    review = page.get_by_role("dialog", name="Review action", exact=True)
    keyboard_dialog(page, review)
    review.get_by_text("Exact operation and host policy", exact=True).click()
    expect(review.get_by_role("region", name="Approval details", exact=True)).to_contain_text("run_workflow")
    review.get_by_role("button", name="Allow once", exact=True).click()
    activity = page.get_by_role("button", name="View activity", exact=True)
    expect(activity).to_be_visible(timeout=15000)
    if activity.get_attribute("aria-expanded") != "true":
        activity.click()
    action = page.locator(".tool-action-trigger").filter(has=page.get_by_text("Run workflow", exact=True))
    expect(action).to_be_visible(timeout=15000)
    if action.get_attribute("aria-expanded") != "true":
        action.click()
    receipt = page.get_by_role("group", name="Workflow run receipt", exact=True)
    expect(receipt).to_be_visible(timeout=15000)
    expect(receipt).to_contain_text("Agent-created workflow")
    runs = api(page, PREFIX + "/runs")["runs"]
    actual = next(row for row in runs if row["name"] == "Agent-created workflow")
    assert actual["parent_session_id"] == parent["id"] and actual["project_id"] == project["id"]
    view = receipt.get_by_role("button", name="View run", exact=True)
    view.focus()
    audit(page, out, report, "coding-agent-run-receipt", theme, width)
    page.keyboard.press("Enter")
    detail = page.get_by_role("region", name="Workflow run", exact=True)
    expect(detail.get_by_role("heading", name="Agent-created workflow", exact=True)).to_be_visible(timeout=15000)
    expect(detail.get_by_role("heading", name="Result", exact=True)).to_be_visible(timeout=15000)
    fixture.wait(lambda: api(page, PREFIX + "/runs/" + actual["id"])["status"] == "completed", page)
    final = api(page, PREFIX + "/runs/" + actual["id"])
    assert final["result"] == {"receipt": "workflow-tool-ok"}
    report["checks"].append({"check": "normal-composer-coding-agent-run_workflow-review-real-receipt-View-run", "theme": theme, "width": width, "run_id": actual["id"], "parent_session_id": parent["id"], "result": final["result"], "synthetic_provider_availability_metadata": True, "provider_credentials": 0})
    state._qa_allowed_turn_sessions = set()


def quota_controls(dialog, width):
    actual = dialog.locator(".workflow-limit-toggle").evaluate_all("rows=>rows.map(label=>({height:label.getBoundingClientRect().height,accent:getComputedStyle(label.querySelector('input')).accentColor}))")
    assert len(actual) == 3, actual
    assert all(row["height"] >= (44 if width < 640 else 40) and row["accent"] != "auto" for row in actual), actual
    return actual


def quota_exercise(page, context, state, owner, project, calls, base, out, report, theme, width):
    """The new manual-limit contract, with no synthetic execution responses."""
    panel = page.get_by_role("region", name="Workflows workspace", exact=True)
    null_limits = {"max_agents": None, "concurrency": None, "timeout_s": None}
    source = "for(let i=0;i<args.count;i++) await log('item-'+i);\nreturn {calls:args.count,hostProcess:typeof process};"
    schema = {"type": "object", "properties": {"count": {"type": "integer", "minimum": 1}}, "required": ["count"], "additionalProperties": False}
    editor = create_in_editor(page, "Large receipt run", source, schema)
    assert editor.get_by_text("Limits", exact=True).count() == 0
    assert editor.get_by_role("checkbox").count() == 0
    keyboard_dialog(page, editor)
    editor.get_by_role("button", name="Validate", exact=True).click()
    expect(editor.get_by_role("status")).to_contain_text("Workflow is valid.", timeout=15000)
    audit(page, out, report, "editor-without-quotas", theme, width)
    editor.get_by_role("button", name="Save", exact=True).click()
    editor.wait_for(state="detached")
    definition = api(page, PREFIX)["workflows"][0]
    assert definition["limits"] == null_limits
    panel.get_by_role("button", name="Run", exact=True).click()
    inputs = page.get_by_role("dialog", name="Large receipt run", exact=True)
    inputs.get_by_role("spinbutton").fill("600")
    inputs.get_by_text("Limits", exact=True).click()
    toggles = inputs.get_by_role("checkbox")
    expect(toggles).to_have_count(3)
    for index in range(3):
        expect(toggles.nth(index)).not_to_be_checked()
    assert inputs.get_by_role("spinbutton").count() == 1
    keyboard_dialog(page, inputs)
    targets = quota_controls(inputs, width)
    audit(page, out, report, "run-limits-default-off", theme, width)
    inputs.get_by_role("button", name="Start", exact=True).click()
    inputs.wait_for(state="detached")
    detail = page.get_by_role("region", name="Workflow run", exact=True)
    expect(detail.get_by_role("heading", name="Result", exact=True)).to_be_visible(timeout=40000)
    expect(detail.locator(".workflow-run-meta")).to_contain_text("Completed")
    expect(detail.locator(".workflow-activity-meta")).to_contain_text("Recent activity · 512 of 600 entries")
    first = api(page, PREFIX + "/runs")["runs"][0]
    assert first["limits"] == null_limits and first["status"] == "completed"
    assert first["result"] == {"calls": 600, "hostProcess": "undefined"}
    assert first["call_count"] == 600 and first["calls_truncated"] and len(first["calls"]) == 512
    if width == 1280 and theme == "dark":
        audit(page, out, report, "unlimited-600-call-result", theme, width)
    detail.get_by_role("button", name="Run again", exact=True).click()
    again = page.get_by_role("dialog", name="Run workflow again", exact=True)
    expect(again.get_by_role("spinbutton")).to_have_value("600")
    again.get_by_text("Limits", exact=True).click()
    for toggle, label, value in (("Limit agent calls", "Agent calls limit", "1000"), ("Limit parallel agents", "Parallel agents limit", "24"), ("Limit seconds", "Seconds limit", "7200")):
        checkbox = again.get_by_role("checkbox", name=toggle, exact=True)
        expect(checkbox).not_to_be_checked()
        checkbox.check()
        field = again.get_by_role("spinbutton", name=label, exact=True)
        expect(field).to_have_value("")
        assert field.get_attribute("max") is None
        field.fill(value)
    # Enabled fields are required; turning a bound off removes its draft value.
    seconds = again.get_by_role("checkbox", name="Limit seconds", exact=True)
    seconds.uncheck()
    assert again.get_by_role("spinbutton", name="Seconds limit", exact=True).count() == 0
    seconds.check()
    expect(again.get_by_role("spinbutton", name="Seconds limit", exact=True)).to_have_value("")
    again.get_by_role("spinbutton", name="Seconds limit", exact=True).fill("7200")
    again.get_by_role("button", name="Start", exact=True).scroll_into_view_if_needed()
    expect(again.get_by_role("button", name="Start", exact=True)).to_be_in_viewport(ratio=.99)
    audit(page, out, report, "manual-run-limits-explicit", theme, width)
    again.get_by_role("button", name="Start", exact=True).click()
    again.wait_for(state="detached")
    expect(detail.get_by_role("heading", name="Result", exact=True)).to_be_visible(timeout=40000)
    manual = api(page, PREFIX + "/runs")["runs"][0]
    assert manual["id"] != first["id"] and manual["status"] == "completed"
    assert manual["limits"] == {"max_agents": 1000, "concurrency": 24, "timeout_s": 7200}
    assert manual["result"] == first["result"]
    detail.get_by_role("button", name="Run again", exact=True).click()
    reset = page.get_by_role("dialog", name="Run workflow again", exact=True)
    reset.get_by_text("Limits", exact=True).click()
    for index in range(3):
        expect(reset.get_by_role("checkbox").nth(index)).not_to_be_checked()
    assert reset.get_by_role("spinbutton").count() == 1
    keyboard_dialog(page, reset)
    audit(page, out, report, "run-again-limits-reset-off", theme, width)
    reset.get_by_role("button", name="Cancel", exact=True).click()
    reset.wait_for(state="detached")
    assert state.agent_store.list_tasks() == [] and calls == []
    report["checks"].append({"check": "quota-free-editor-and-default-run-explicit-manual-limits-reset-on-Run-again", "theme": theme, "width": width, "default_run_id": first["id"], "default_limits": first["limits"], "manual_run_id": manual["id"], "manual_limits": manual["limits"], "calls_per_run": 600, "visible_calls": 512, "history_truncated_honestly": True, "manual_checkbox_targets": targets, "agent_tasks": 0})


def run(destination, ui_dist, baseline=False, quota_checks=False):
    out = Path(destination).resolve()
    out.mkdir(parents=True, exist_ok=False)
    report = {"mode": "before" if baseline else "after", "proof_type": "optional-workflow-limits" if quota_checks else "custom-workflows", "result": "running", "output": str(out), "ui_dist": str(Path(ui_dist).resolve()), "started_at": datetime.now(timezone.utc).isoformat(), "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "states": [], "checks": [], "errors": [], "external_requests": [], "forbidden_mutations": [], "owned_mutations": [], "backend_requests": [], "requests": [], "limits": ["Scratch authenticated local profiles; no real profile, provider secrets, paid inference or native device proof.", "Provider responses are deterministic fixtures; the browser, QuickJS worker, shell, normal plan approval and durable stores are real.", "Axe is bounded automated coverage; incomplete findings need human review."]}
    try:
        sources = {str(path.relative_to(REPO)): hashlib.sha256(path.read_bytes()).hexdigest() for root in (REPO / "desktop/workspace/src", REPO / "src/termx") for path in sorted(root.rglob("*")) if path.is_file() and path.suffix in (".ts", ".tsx", ".css", ".py", ".js")}
        (out / "source-manifest.json").write_text(json.dumps(sources, indent=2) + "\n")
        report["source_manifest_digest"] = hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest()
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
            report["browser"] = browser.version
            for width, height, theme in ((1280, 900, "dark"), (1280, 900, "light"), (375, 812, "dark"), (375, 812, "light")):
                with isolated_workflow_host(ui_dist, report) as (state, owner, project, calls, base, scratch):
                    context = browser.new_context(viewport={"width": width, "height": height}, has_touch=width < 640, reduced_motion="reduce")
                    context.add_init_script('localStorage.setItem("termx-shell-theme",' + json.dumps(theme) + ')')
                    origin = urlsplit(base)
                    def guard(route):
                        req = route.request
                        url = urlsplit(req.url)
                        if url.scheme in ("http", "https") and (url.scheme, url.netloc) != (origin.scheme, origin.netloc):
                            report["external_requests"].append(req.url)
                            route.abort()
                            return
                        body = req.post_data_json if req.method == "POST" and url.path == "/graphql" else {}
                        workflow_mutation = not baseline and (url.path.startswith(PREFIX) or re.fullmatch(r"/api/workspace/tasks/[^/]+/approvals/[^/]+/resolve", url.path))
                        session_write = re.fullmatch(r"/api/workspace/sessions/([^/]+)(?:/turns)?", url.path)
                        owned_turn = not baseline and session_write and session_write[1] in getattr(state, "_qa_allowed_turn_sessions", set())
                        allowed = req.method in ("GET", "HEAD") or url.path in ("/auth/login", "/auth/refresh") or workflow_mutation or owned_turn or (url.path == "/graphql" and not re.search(r"\bmutation\b", (body or {}).get("query", "")))
                        if not allowed:
                            report["forbidden_mutations"].append({"method": req.method, "path": url.path})
                            route.abort()
                            return
                        if req.method not in ("GET", "HEAD") and (workflow_mutation or owned_turn):
                            report["owned_mutations"].append({"method": req.method, "path": url.path})
                        report["requests"].append({"method": req.method, "path": url.path, "query": url.query})
                        route.continue_()
                    context.route("**/*", guard)
                    page = context.new_page()
                    page.on("pageerror", lambda error: report["errors"].append(str(error)))
                    try:
                        authenticate(page, base, baseline)
                        if baseline:
                            assert page.get_by_role("navigation", name="Workspace views", exact=True).get_by_role("button", name="Workflows", exact=True).count() == 0
                            audit(page, out, report, "projects-workflows-absent", theme, width)
                            report["checks"].append({"check": "baseline-workflow-navigation-absent", "theme": theme, "width": width})
                        else:
                            capability = api(page, PREFIX + "/capabilities")
                            assert capability["runtime"]["available"], capability
                            report["runtime"] = capability
                            (quota_exercise if quota_checks else exercise)(page, context, state, owner, project, calls, base, out, report, theme, width)
                    except BaseException:
                        page.screenshot(path=str(out / "failure.png"), full_page=True)
                        raise
                    finally:
                        context.close()
            browser.close()
            report["cleanup"] = {"browser_closed": not browser.is_connected(), "scratch_hosts_closed": True, "owned_profiles_removed": True}
        assert not report["errors"] and not report["external_requests"] and not report["forbidden_mutations"], report
        if not baseline:
            assert all(hashlib.sha256((REPO / path).read_bytes()).hexdigest() == digest for path, digest in sources.items()), "Source changed during final proof; rerun frozen source"
        report["result"] = "passed"
    except BaseException as error:
        report.update(result="failed", failure=type(error).__name__ + ": " + str(error))
        raise
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({"result": report["result"], "report": str(out / "report.json")}), flush=True)


if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2], "--baseline" in sys.argv, "--quotas" in sys.argv)
