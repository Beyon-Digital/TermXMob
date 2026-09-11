import asyncio
from pathlib import Path
from time import time

from termx.config import ConfigStore
from termx.providers.base import ProviderStatus
from termx.providers.cloudflare import named_tunnel_cmd
from termx.tunnels import TunnelManager

RUNTIME_KEYS = {"provider", "state", "url", "detail", "log", "started_at", "uptime_s"}


class FakeProvider:
    id = "cloudflare"

    def __init__(self) -> None:
        self._status = ProviderStatus(provider=self.id, state="stopped")

    def detect(self) -> dict:
        return {"id": self.id, "name": "Cloudflare", "available": True, "kinds": ["quick", "named"]}

    async def start(self, port: int, profile: dict) -> ProviderStatus:
        self._status = ProviderStatus(
            provider=self.id,
            state="connected",
            url="https://example.trycloudflare.com",
            started_at=time(),
            log=["started"],
        )
        return self._status

    async def stop(self) -> None:
        self._status = ProviderStatus(provider=self.id, state="stopped")

    def status(self) -> ProviderStatus:
        return self._status


def test_detect_and_runtime(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    manager = TunnelManager(store, port=8787)
    runtime = manager.runtime()
    ids = {item["id"] for item in runtime["providers"]}
    assert ids == {"cloudflare", "ngrok", "tailscale"}
    assert runtime["active"] is None
    public = manager.status_public()
    assert public["state"] == "stopped"
    assert RUNTIME_KEYS <= public.keys()
    for status in runtime["runtime"].values():
        assert RUNTIME_KEYS <= status.keys()
        assert isinstance(status["log"], list)
        assert len(status["log"]) <= 40


def test_status_redacts_log_and_uptime() -> None:
    status = ProviderStatus(
        provider="ngrok",
        state="connected",
        url="https://abc.ngrok-free.app",
        log=[
            "using authtoken=abcdefghijklmnopqrstuvwxyz0123456789",
            "Authorization Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9aaaa",
            "https://foo.trycloudflare.com/?k=super-secret-pass",
        ],
        started_at=time() - 5,
    )
    public = status.public()
    joined = "\n".join(public["log"])
    assert "abcdefghijklmnopqrstuvwxyz0123456789" not in joined
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9aaaa" not in joined
    assert "super-secret-pass" not in joined
    assert public["uptime_s"] is not None
    assert public["uptime_s"] >= 4
    assert public["started_at"] is not None


def test_restart_without_active(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    fake = FakeProvider()
    manager = TunnelManager(store, port=8787, providers={"cloudflare": fake})
    status = asyncio.run(manager.restart())
    assert status.state == "connected"
    assert status.url == "https://example.trycloudflare.com"
    public = manager.status_public()
    assert public["state"] == "connected"
    assert RUNTIME_KEYS <= public.keys()


def test_named_tunnel_cmd() -> None:
    assert named_tunnel_cmd("/usr/bin/cloudflared", "secret-token") == [
        "/usr/bin/cloudflared",
        "tunnel",
        "--no-autoupdate",
        "run",
        "--token",
        "secret-token",
    ]
