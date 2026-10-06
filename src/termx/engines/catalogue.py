"""Explicit, single-flight engine discovery. Ordinary reads never spawn agents."""

from __future__ import annotations

import asyncio
import copy
import time
from typing import Any


class EngineCatalogue:
    def __init__(self, gateway: Any):
        self.gateway = gateway
        self.entries: dict[str, dict[str, Any]] = {}
        self.jobs: dict[str, asyncio.Task] = {}
        self.startup: asyncio.Task | None = None
        # Discovery starts temporary subprocesses; bound startup refresh so a
        # large configured runner set cannot fork every agent at once.
        self._semaphore = asyncio.Semaphore(4)

    def start(self) -> None:
        if self.startup is None:
            self.startup = asyncio.create_task(self.refresh())

    def invalidate(self, engine: str) -> None:
        if engine in self.entries:
            self.entries[engine]["stale"] = True

    async def read(self, engine: str) -> dict[str, Any]:
        adapter = self.gateway.adapter(engine)
        # A first read can wait for already scheduled startup discovery; it
        # never initiates a probe or retries a failed/empty catalogue itself.
        if engine not in self.entries and self.startup and not self.startup.done():
            await asyncio.shield(self.startup)
        entry = self.entries.get(engine)
        if entry is None:
            descriptor = adapter.descriptor().as_dict()
            descriptor["capabilities"] = adapter.capabilities().as_dict()
            entry = {"descriptor": descriptor, "configuration": {}, "refreshed_at": None,
                     "stale": True, "refresh_error": "Runner has not been refreshed yet."}
        return copy.deepcopy(entry)

    async def refresh(self, engine: str | None = None, cwd: str | None = None) -> dict[str, Any]:
        names = [engine] if engine else self.gateway.engines()
        for name in names:
            self.gateway.adapter(name)  # validate before starting any work
        entries = await asyncio.gather(*(self._single(name, cwd) for name in names))
        return dict(zip(names, entries))

    async def _single(self, engine: str, cwd: str | None) -> dict[str, Any]:
        task = self.jobs.get(engine)
        if task is None:
            task = asyncio.create_task(self._discover(engine, cwd))
            self.jobs[engine] = task
        try:
            return copy.deepcopy(await asyncio.shield(task))
        finally:
            if task.done() and self.jobs.get(engine) is task:
                self.jobs.pop(engine, None)

    async def _discover(self, engine: str, cwd: str | None) -> dict[str, Any]:
        adapter = self.gateway.adapter(engine)
        launch = copy.deepcopy(getattr(adapter, "_launch_config", {}))
        previous = self.entries.get(engine)
        descriptor = adapter.descriptor().as_dict()
        configuration = copy.deepcopy((previous or {}).get("configuration", {}))
        error = None

        async def discover():
            nonlocal descriptor, configuration
            desc = await adapter.probe()
            descriptor = desc.as_dict()
            if desc.error:
                raise ValueError(desc.error)
            read = getattr(adapter, "discover_catalogue", None)
            if read is None:
                read = getattr(adapter, "discover_configuration", None)
            configuration = await read(cwd or launch.get("cwd")) if read else {"models": adapter.capabilities().models}

        try:
            async with self._semaphore:
                await asyncio.wait_for(discover(), float(launch.get("catalogue_timeout_s", 120)))
        except Exception as exc:
            error = str(exc) or type(exc).__name__
        capabilities = adapter.capabilities().as_dict()
        # Session notifications and model-branch discovery cannot overwrite
        # the stable catalogue's default model/mode/reasoning choices.
        if configuration:
            capabilities["models"] = configuration.get("models", [])
            capabilities["modes"] = configuration.get("modes", {}).get("availableModes", [])
            capabilities["config_options"] = configuration.get("config_options", [])
        descriptor["capabilities"] = capabilities
        descriptor["error"] = error
        entry = {"descriptor": descriptor, "configuration": configuration,
                 "refreshed_at": time.time(), "stale": bool(error), "refresh_error": error}
        if adapter is self.gateway._adapters.get(engine) and launch == getattr(adapter, "_launch_config", {}):
            self.entries[engine] = entry
        else:
            entry["stale"] = True
        return entry

    async def stop(self) -> None:
        tasks = [*self.jobs.values(), *([self.startup] if self.startup else [])]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.jobs.clear()
