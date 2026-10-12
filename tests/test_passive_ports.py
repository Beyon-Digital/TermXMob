"""Port discovery must never make HTTP requests to the services it lists."""

from contextlib import ExitStack, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import pytest
from fastapi.testclient import TestClient

from _gql import data
from termx import processes
from termx.app import AppState, create_app


@contextmanager
def counting_server():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.requestline)
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def discovery_snapshot(monkeypatch, platform, ports, root):
    """Limit OS enumeration to our two servers; leave discovery/probing real."""
    monkeypatch.setattr(processes.sys, "platform", platform)
    monkeypatch.setattr(processes, "_proc_net_listeners", lambda: [
        {"address": "127.0.0.1", "port": port, "family": "ipv4", "inode": str(pid)}
        for pid, port in enumerate(ports, 100)
    ])
    monkeypatch.setattr(processes, "_live_pids", lambda: [100, 101])
    monkeypatch.setattr(processes, "_socket_inodes", lambda pid: {str(pid)})
    monkeypatch.setattr(processes, "_proc_info", lambda pid: {
        "name": "server", "cmdline": "server", "cwd": str(root) if pid == 100 else None,
    })
    monkeypatch.setattr(processes, "_lsof", lambda *_args:
        "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n" + "\n".join(
            f"server {pid} user 10u IPv4 0x1 0t0 TCP 127.0.0.1:{port} (LISTEN)"
            for pid, port in enumerate(ports, 100)
        ))


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_polling_and_preview_registration_do_not_contact_listeners(tmp_path, monkeypatch, platform):
    with ExitStack() as stack:
        target, target_requests = stack.enter_context(counting_server())
        unrelated, unrelated_requests = stack.enter_context(counting_server())
        state = AppState(passcode="test-only")
        client = stack.enter_context(TestClient(create_app(state, web_dir=None)))
        project = state.projects.register(str(tmp_path), "test project")
        headers = {"x-termx-passcode": "test-only"}
        # Set the platform only around requests, after host startup.
        with monkeypatch.context() as patch:
            discovery_snapshot(patch, platform, [target, unrelated], tmp_path)
            for _ in range(5):
                entries = data(client, "{ ports { ports { port url is_http } } }", "ports", headers=headers)["ports"]
                assert {entry["port"] for entry in entries} == {target, unrelated}
                # Project filtering must not probe other projects either.
                data(client, "query($id: String!) { ports(project_id: $id) { ports { port } } }",
                     "ports", {"id": project["id"]}, headers)
            preview = data(client,
                "mutation($id: String!, $input: PreviewFromPortInput!) { preview_from_port(project_id: $id, input: $input) { preview } }",
                "preview_from_port", {"id": project["id"], "input": {"port": target, "name": "dev server"}}, headers)
            assert preview["preview"]["url"] == f"http://127.0.0.1:{target}"
            assert target_requests == [], f"Unsolicited target requests: {target_requests}"
            assert unrelated_requests == [], f"Unsolicited unrelated requests: {unrelated_requests}"
            assert all(entry["is_http"] is None and entry["url"] is None for entry in entries)


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_discovery_never_opens_a_connection(monkeypatch, tmp_path, platform):
    discovery_snapshot(monkeypatch, platform, [3000, 5432], tmp_path)

    def unexpected_connection(*_args, **_kwargs):
        pytest.fail("Listing ports must not connect to HTTP or non-HTTP listeners")

    monkeypatch.setattr(processes.socket, "create_connection", unexpected_connection)
    assert {entry["port"] for entry in processes.listeners()} == {3000, 5432}
