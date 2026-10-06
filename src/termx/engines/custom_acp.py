"""User-configured standard ACP stdio agents; no vendor-specific protocol."""

from typing import Any

from .acp import AcpEngine


class CustomAcpEngine(AcpEngine):
    # ACP does not require a --version CLI. Version comes from agentInfo.
    version_args: list[str] = []

    def __init__(self, runner_id: str, config: dict[str, Any], **kwargs: Any):
        self.id = runner_id
        self.label = config.get("label") or runner_id
        self.executable_name = config["executable"]
        super().__init__(launch_config=config, **kwargs)
