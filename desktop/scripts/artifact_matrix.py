#!/usr/bin/env python3
"""Resolve artifact qualification scope; release/tag builds always retain every platform."""
import json
import os

SCOPES = ('all', 'macos-and-windows', 'windows')


def build_matrix(*, event: str, release_tag: str, scope: str = 'all', include_intel: bool = True) -> dict:
    artifact_only = event == 'workflow_dispatch' and not release_tag
    effective_scope = scope if artifact_only else 'all'
    if effective_scope not in SCOPES:
        raise ValueError('Unknown artifact qualification scope')
    mac_bundles = 'app' if artifact_only else 'dmg'
    platforms = []
    if effective_scope != 'windows':
        platforms.append({'name': 'macos-arm64', 'platform': 'macos-15', 'target': 'aarch64-apple-darwin', 'bundles': mac_bundles})
        if include_intel:
            platforms.append({'name': 'macos-x86_64', 'platform': 'macos-15-intel', 'target': 'x86_64-apple-darwin', 'bundles': mac_bundles})
    platforms.append({'name': 'windows', 'platform': 'windows-latest', 'target': '', 'bundles': 'msi,nsis'})
    if effective_scope == 'all':
        platforms.append({'name': 'linux', 'platform': 'ubuntu-22.04', 'target': '', 'bundles': 'deb,rpm,appimage'})
    return {'include': platforms}


if __name__ == '__main__':
    print(json.dumps(build_matrix(event=os.environ.get('ARTIFACT_EVENT', ''), release_tag=os.environ.get('ARTIFACT_RELEASE_TAG', ''), scope=os.environ.get('ARTIFACT_QUALIFICATION_SCOPE', 'all') or 'all', include_intel=os.environ.get('INCLUDE_INTEL', 'true') == 'true')))
