from termx.providers.base import ProviderStatus, TunnelProvider
from termx.providers.cloudflare import CloudflareProvider
from termx.providers.ngrok import NgrokProvider
from termx.providers.tailscale import TailscaleProvider

PROVIDERS: dict[str, TunnelProvider] = {
    "cloudflare": CloudflareProvider(),
    "ngrok": NgrokProvider(),
    "tailscale": TailscaleProvider(),
}

__all__ = ["PROVIDERS", "ProviderStatus", "TunnelProvider"]
