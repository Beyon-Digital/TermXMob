"""Real-host proof of the Settings pages for Anthropic accounts, web search and the always-on host.

    python tests/workspace_settings_providers_e2e.py OUTPUT_DIR [EVIDENCE_DIR]

Starts a real uvicorn host on a temporary profile with the built workspace (`pnpm --dir desktop/workspace build`) and
drives it in Chromium. The host runs its terminals directly (TERMX_PTY_HOLDER=direct) and keep-awake uses a fake
backend, so the run never starts a holder process or changes the machine's sleep. It creates an Anthropic account
through the UI and stops at the connection-test confirmation (no request leaves the machine), sets and clears the
web-search key in an in-memory credential store, and changes the keep-awake and terminal settings. With EVIDENCE_DIR it
also writes screenshots at 375x812 and 1280x900, light and dark.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
from pathlib import Path
from time import monotonic, sleep

import uvicorn
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
KEY = "sk-ant-e2e-synthetic-write-only-key"
SEARCH_KEY = "BSA-e2e-synthetic-write-only-key"
SEEDED = ["claude-opus-5-5", "claude-fable-5-2", "claude-sonnet-5-5", "claude-haiku-5-5"]
SIZES = [(375, 812), (1280, 900)]


def start_host(root: Path):
    os.environ.update(
        TERMX_CONFIG_DIR=str(root / "config"),
        TERMX_AGENTS_DIR=str(root / "agents"),
        TERMX_ENGINE_STARTUP_REFRESH="0",
        TERMX_PTY_HOLDER="direct",
    )
    for name in ("TERMX_AI_WEB_SEARCH_API_KEY", "TERMX_WEB_SEARCH_URL", "TERMX_WEB_SEARCH_PROVIDER"):
        os.environ.pop(name, None)
    from termx.agent.secrets import CredentialStore
    from termx.app import AppState, create_app
    from termx.keep_awake import Backend, KeepAwake

    class FakeBackend(Backend):
        name = "fake"
        supported = True

        def __init__(self) -> None:
            self.running = False

        def start(self, why: str) -> None:
            self.running = True

        def stop(self) -> None:
            self.running = False

        def alive(self) -> bool:
            return self.running

    memory: dict[str, str] = {}
    state = AppState(passcode=None, credentials=CredentialStore(memory=memory))
    state.host_status.keep_awake = KeepAwake(FakeBackend())
    state.identity.setup_owner("providers-fixture", "providers-fixture-password-123")
    state.projects.register(str(root), name="Providers fixture")
    app = create_app(state, web_dir=ROOT / "desktop/workspace/dist")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on", access_log=False))
    worker = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    worker.start()
    deadline = monotonic() + 20
    while not server.started:
        if monotonic() > deadline:
            raise RuntimeError("Fixture host did not start")
        sleep(0.05)
    return state, memory, server, worker, listener.getsockname()[1]


def run(destination: str, evidence: str | None = None) -> None:
    output = Path(destination)
    output.mkdir(parents=True, exist_ok=True)
    shots = Path(evidence) if evidence else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
    report: dict = {"checks": [], "errors": [], "axe": [], "layout": [], "screenshots": []}
    with tempfile.TemporaryDirectory(prefix="termx-providers-proof-") as scratch:
        root = Path(scratch)
        state, memory, server, worker, port = start_host(root)
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    executable_path=os.environ.get("TERMX_TEST_CHROMIUM") or shutil.which("chromium") or None, headless=True,
                )
                page = browser.new_page(viewport={"width": 1280, "height": 900}, reduced_motion="reduce", color_scheme="light")
                page.on("pageerror", lambda error: report["errors"].append(str(error)))
                sent: list[str] = []
                page.on("request", lambda request: sent.append(request.post_data or "") if request.url.endswith("/graphql") and request.method == "POST" else None)
                axe_path = ROOT / "desktop/workspace/node_modules/axe-core/axe.min.js"

                def settle() -> None:
                    page.wait_for_timeout(120)

                def capture(label: str, target=None) -> None:
                    if not shots:
                        return
                    for scheme in ("light", "dark"):
                        page.emulate_media(color_scheme=scheme, reduced_motion="reduce")
                        for width, height in SIZES:
                            page.set_viewport_size({"width": width, "height": height})
                            settle()
                            if target is not None:
                                target.evaluate("(node)=>node.scrollIntoView({block:'start'})")
                                settle()
                            name = f"{label}-{scheme}-{width}.png"
                            page.screenshot(path=str(shots / name))
                            report["screenshots"].append(name)
                    page.emulate_media(color_scheme="light", reduced_motion="reduce")
                    page.set_viewport_size({"width": 1280, "height": 900})
                    settle()

                def layout(label: str) -> None:
                    """No page overflow, the Settings page inside the viewport, and no axe violation at desktop and phone width."""
                    for width, height in ((1280, 900), (390, 844)):
                        page.set_viewport_size({"width": width, "height": height})
                        settle()
                        overflow = page.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
                        inner = page.evaluate(
                            "()=>{const d=document.querySelector('.settings-surface-page');if(!d)return 0;"
                            "return [...d.querySelectorAll('.settings-content *')].filter(e=>e.getBoundingClientRect().right>innerWidth+1&&getComputedStyle(e).position!=='fixed').length}"
                        )
                        bounds = dialog.bounding_box()
                        assert not overflow, f"{label} overflows the page at {width}px"
                        assert bounds and bounds["x"] >= -1 and bounds["x"] + bounds["width"] <= width + 1, f"{label} dialog leaves the viewport at {width}px"
                        assert inner == 0, f"{label}: {inner} elements extend past the viewport at {width}px"
                        violations = page.evaluate(
                            "async()=>{const r=await axe.run(document.querySelector('.settings-surface-page'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});"
                            "return r.violations.map(v=>({id:v.id,impact:v.impact,targets:v.nodes.map(n=>n.target)}))}"
                        )
                        report["layout"].append({"state": label, "width": width, "overflow": False})
                        report["axe"].append({"state": label, "width": width, "violations": violations})
                        assert not violations, (label, violations)
                    page.set_viewport_size({"width": 1280, "height": 900})
                    settle()

                page.goto(f"http://127.0.0.1:{port}/")
                page.get_by_label("Username", exact=True).fill("providers-fixture")
                page.get_by_label("Password", exact=True).fill("providers-fixture-password-123")
                page.get_by_role("button", name="Continue with password").click()
                # The IDE layout is the default: Settings is a tab, opened from the activity bar.
                page.get_by_role("tablist", name="Editor group 1 tabs").wait_for(timeout=60000)
                dialog = page.get_by_role("region", name="Settings", exact=True)
                for attempt in range(4):
                    # A click that lands while the shell is still restoring its layout can be lost: ask again.
                    page.get_by_role("toolbar", name="Activity bar").get_by_role("button", name="Settings", exact=True).click()
                    try:
                        dialog.wait_for(timeout=8000)
                        break
                    except Exception:
                        if attempt == 3:
                            raise
                page.add_script_tag(path=str(axe_path))
                nav = dialog.get_by_role("navigation", name="Settings categories")
                mobile_category = dialog.get_by_role("combobox", name="Settings category", exact=True)

                def choose(label: str) -> None:
                    if mobile_category.is_visible():
                        mobile_category.select_option(label=label)
                    else:
                        nav.get_by_role("link", name=label, exact=True).click()
                    settle()

                def discard_if_asked() -> None:
                    prompt = page.get_by_role("dialog", name="Discard unsaved changes?", exact=True)
                    if prompt.is_visible():
                        prompt.get_by_role("button", name="Discard changes", exact=True).click()

                # -- Anthropic account -----------------------------------------------------------------------------
                choose("Models & engines")
                add_form = dialog.get_by_role("form", name="Add provider account")
                add_form.wait_for()
                add_form.get_by_label("Provider", exact=True).select_option(label="Anthropic (Claude)")
                add_form = dialog.get_by_role("form", name="Add provider account")
                expect(add_form.get_by_label("API key", exact=True)).to_have_attribute("type", "password")
                assert add_form.get_by_label("Request format").count() == 0, "the request format is fixed for Anthropic"
                seeded = add_form.locator('input[name="models"]').evaluate_all("(nodes)=>nodes.map(n=>n.value)")
                assert seeded == SEEDED, seeded
                expect(add_form.get_by_label("Default model", exact=True)).to_have_value("claude-opus-5-5")
                capture("01-anthropic-add", add_form)
                add_form.get_by_role("button", name="Save account", exact=True).click()
                expect(add_form.get_by_label("Display name", exact=True)).to_have_attribute("aria-invalid", "true")
                expect(add_form.get_by_text("Enter your Anthropic API key.", exact=False)).to_be_visible()
                capture("02-anthropic-add-errors", add_form)
                add_form.get_by_label("Display name", exact=True).fill("Claude")
                add_form.get_by_label("API key", exact=True).fill(KEY)
                layout("anthropic add form")
                add_form.get_by_role("button", name="Save account", exact=True).click()
                dialog.get_by_text("Provider account saved", exact=True).wait_for()
                providers = state.agent.list_providers()
                saved = next(row for row in providers if row["kind"] == "anthropic")
                assert saved["wire"] == "messages" and saved["base_url"] == "https://api.anthropic.com", saved
                assert saved["model"].split("\n") == SEEDED and saved["secret_configured"] is True
                assert memory[saved["id"]] == KEY
                report["checks"].append("Anthropic account created through the UI: kind, messages wire, seeded models, key in the credential store")
                dialog.get_by_text("Anthropic (Claude) · Credential configured", exact=False).wait_for()

                dialog.get_by_role("button", name="Edit account", exact=True).click()
                edit_form = dialog.get_by_role("form", name="Edit provider account")
                edit_form.wait_for()
                expect(edit_form.get_by_text("Key saved", exact=True)).to_be_visible()
                assert KEY not in page.content(), "the saved key must never be rendered"
                assert edit_form.locator('input[name="api_key"]').count() == 0
                capture("03-anthropic-edit-key-saved", edit_form)
                layout("anthropic edit form")
                edit_form.get_by_role("button", name="Replace key", exact=True).click()
                expect(edit_form.get_by_label("New API key", exact=True)).to_have_value("")
                capture("04-anthropic-replace-key", edit_form.get_by_label("New API key", exact=True))
                edit_form.get_by_role("button", name="Keep saved key", exact=True).click()
                expect(edit_form.get_by_text("Key saved", exact=True)).to_be_visible()
                edit_form.get_by_label("Display name", exact=True).fill("Claude work")
                expect(dialog.get_by_role("status").filter(has_text="Unsaved changes")).to_be_visible()
                edit_form.get_by_role("button", name="Save account changes", exact=True).click()
                dialog.get_by_text("Provider account updated", exact=True).wait_for()
                assert next(row for row in state.agent.list_providers() if row["kind"] == "anthropic")["name"] == "Claude work"
                assert memory[saved["id"]] == KEY, "saving without a new key keeps the stored key"
                report["checks"].append("edit keeps the key (Key saved), saves a rename, and the footer tracks unsaved edits")

                # The reviewed connection test stops at its confirmation: nothing is sent to Anthropic.
                dialog.get_by_role("button", name="Test connection", exact=True).first.click()
                confirm = page.get_by_role("dialog", name=re.compile(r"^Test Claude work\?"))
                confirm.wait_for()
                expect(confirm).to_contain_text("list the models your key can use")
                expect(confirm).to_contain_text("nothing is billed")
                capture("05-anthropic-test-confirm", confirm)
                confirm.get_by_role("button", name="Cancel test", exact=True).click()
                expect(confirm).to_be_hidden()
                assert not any("test_agent_provider" in body for body in sent), "the connection test must not be sent"
                report["checks"].append("connection test confirmation shown and cancelled; no test request was sent")

                # The new account is a choice in the model picker, with cost shown as unpriced.
                role_provider = dialog.locator('select[name$=".provider"]').first
                role_provider.select_option(saved["id"])
                role_model = dialog.locator('select[name$=".model"]').first
                options = role_model.locator("option").evaluate_all("(nodes)=>nodes.map(n=>n.value)")
                assert "claude-opus-5-5" in options, options
                role_model.select_option("claude-opus-5-5")
                expect(dialog.get_by_text("Cost unpriced", exact=False).first).to_be_visible()
                capture("06-anthropic-model-picker-unpriced", role_model)
                report["checks"].append("Anthropic models appear in the model picker; cost reads unpriced")
                dialog.get_by_role("button", name="Discard", exact=True).click()
                discard_if_asked()

                # -- Web search ------------------------------------------------------------------------------------
                choose("Tools & connections")
                dialog.get_by_text("Web search", exact=True).click()
                search_form = dialog.get_by_role("form", name="Web search key")
                search_form.wait_for()
                expect(dialog.get_by_text("Not available. Add a key to turn it on")).to_be_visible()
                expect(dialog.get_by_text("approve each website once", exact=False)).to_be_visible()
                capture("07-web-search-empty", dialog.get_by_label("Web search", exact=True))
                layout("web search empty")
                search_form.get_by_label("Brave Search API key", exact=True).fill(SEARCH_KEY)
                dialog.get_by_role("button", name="Save", exact=True).click()
                expect(dialog.get_by_text("Key saved", exact=True)).to_be_visible()
                assert memory.get("web-search") == SEARCH_KEY
                assert SEARCH_KEY not in page.content()
                expect(dialog.get_by_text("Available to agents", exact=True)).to_be_visible()
                capture("08-web-search-saved", dialog.get_by_label("Web search", exact=True))
                layout("web search saved")
                dialog.get_by_role("button", name="Remove key", exact=True).click()
                review = page.get_by_role("dialog", name="Remove key?", exact=True)
                review.wait_for()
                review.get_by_role("button", name="Confirm remove key", exact=True).click()
                expect(dialog.get_by_text("Not available. Add a key to turn it on")).to_be_visible()
                assert "web-search" not in memory
                report["checks"].append("web-search key set with the footer Save, never rendered, removed after confirmation")

                # -- Always-on host --------------------------------------------------------------------------------
                choose("Access & sessions")
                dialog.get_by_text("Always-on host", exact=True).click()
                host_form = dialog.get_by_role("form", name="Always-on host settings")
                host_form.wait_for()
                facts = dialog.get_by_label("Host status", exact=True)
                expect(facts).to_contain_text("Terminals end with this host")
                expect(facts).to_contain_text("TERMX_PTY_HOLDER")
                expect(dialog.get_by_text("TERMX_PTY_HOLDER is set on the host and overrides this choice", exact=False)).to_be_visible()
                capture("09-host-always-on-direct", dialog.get_by_label("Always-on host", exact=True))
                layout("host always-on")
                host_form.get_by_role("checkbox", name=re.compile("Keep this computer awake")).check()
                host_form.get_by_role("radio", name=re.compile("Keep running")).check()
                dialog.get_by_role("button", name="Save", exact=True).click()
                expect(facts).to_contain_text("Awake now")
                assert state.store.get().host.keep_awake is True
                assert state.store.get().host.pty_holder == "held"
                dialog.get_by_role("button", name="Refresh host status", exact=True).click()
                settle()
                expect(host_form.get_by_role("radio", name=re.compile("Keep running"))).to_be_checked()
                expect(dialog.get_by_text("overrides this choice", exact=False)).to_be_visible()
                capture("10-host-always-on-saved", dialog.get_by_label("Always-on host", exact=True))
                host_form.get_by_role("checkbox", name=re.compile("Keep this computer awake")).uncheck()
                dialog.get_by_role("button", name="Save", exact=True).click()
                expect(facts).to_contain_text("Off.")
                assert state.store.get().host.keep_awake is False
                report["checks"].append("keep awake and terminal choice saved; direct mode reason and the host-variable override are shown")

                assert not report["errors"], report["errors"]
                browser.close()
        finally:
            server.should_exit = True
            worker.join(timeout=15)
            (output / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({key: (value if key in ("checks", "errors") else len(value)) for key, value in report.items()}, indent=2))


if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
