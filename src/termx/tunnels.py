from __future__ import annotations

import asyncio
from typing import Any

from termx.config import ConfigStore, TunnelProfile
from termx.providers import PROVIDERS
from termx.providers.base import ProviderStatus, TunnelProvider


class TunnelManager:
    def __init__(
        self,
        store: ConfigStore,
        port: int = 8787,
        providers: dict[str, TunnelProvider] | None = None,
    ) -> None:
        self.store = store
        self.port = port
        self._lock = asyncio.Lock()
        self._active_provider: str | None = None
        self._providers = providers or PROVIDERS

    def detect(self) -> list[dict[str, Any]]:
        return [provider.detect() for provider in self._providers.values()]

    def runtime(self) -> dict[str, Any]:
        statuses = {key: value.status().public() for key, value in self._providers.items()}
        active = None
        if self._active_provider:
            active = statuses.get(self._active_provider)
        return {
            "providers": self.detect(),
            "profiles": [item.public() for item in self.store.list_profiles()],
            "active_profile_id": self.store.get().tunnels.active_profile_id,
            "runtime": statuses,
            "active": active,
        }

    def status_public(self) -> dict[str, Any]:
        if not self._active_provider:
            return {
                "state": "stopped",
                "provider": None,
                "url": None,
                "detail": None,
                "log": [],
                "started_at": None,
                "uptime_s": None,
            }
        return self._providers[self._active_provider].status().public()

    async def start(self, profile: TunnelProfile) -> ProviderStatus:
        provider = self._providers.get(profile.provider)
        if provider is None:
            return ProviderStatus(provider=profile.provider, state="error", detail="unknown provider")
        async with self._lock:
            if self._active_provider:
                active = self._providers.get(self._active_provider)
                if active is not None:
                    await active.stop()
                self._active_provider = None
            status = await provider.start(self.port, {"kind": profile.kind, "extra": profile.extra})
            if status.state == "connected":
                self._active_provider = profile.provider
                self.store.set_active_profile(profile.id)
            return status

    async def start_quick_cloudflare(self) -> ProviderStatus:
        profiles = [item for item in self.store.list_profiles() if item.provider == "cloudflare" and item.kind == "quick"]
        if profiles:
            profile = profiles[0]
        else:
            profile = self.store.add_profile("cloudflare", "Cloudflare Quick Tunnel", "quick")
        return await self.start(profile)

    async def stop(self) -> ProviderStatus:
        async with self._lock:
            if self._active_provider:
                active = self._providers.get(self._active_provider)
                if active is not None:
                    await active.stop()
                self._active_provider = None
            self.store.set_active_profile(None)
            return ProviderStatus(provider="none", state="stopped")

    async def restart(self) -> ProviderStatus:
        profile_id = self.store.get().tunnels.active_profile_id
        profile = self.store.get_profile(profile_id) if profile_id else None
        if profile is None:
            return await self.start_quick_cloudflare()
        await self.stop()
        return await self.start(profile)

    async def stop_all(self) -> None:
        async with self._lock:
            names: list[str] = []
            if self._active_provider:
                names.append(self._active_provider)
            for name, provider in self._providers.items():
                if name in names:
                    continue
                if provider.status().state != "stopped":
                    names.append(name)
            if names:
                await asyncio.gather(*(self._providers[name].stop() for name in names), return_exceptions=True)
            self._active_provider = None
            self.store.set_active_profile(None)
