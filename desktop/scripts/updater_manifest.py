"""Publish only complete, version-consistent, platform-specific update sets."""
import argparse
import datetime
import json
from pathlib import Path
from urllib.parse import urlparse


PLATFORMS = {'darwin-aarch64', 'darwin-x86_64', 'windows-x86_64', 'linux-x86_64'}


def merge(fragments, *, tag, repository, notes=None):
    version = tag.removeprefix('v')
    platforms = {}
    urls = set()
    for data in fragments:
        if data.get('version') != version:
            raise ValueError('Updater fragment version does not match the release tag')
        for platform, entry in data.get('platforms', {}).items():
            if platform not in PLATFORMS or platform in platforms:
                raise ValueError('Unexpected or duplicate updater platform')
            url = entry.get('url', '')
            parsed = urlparse(url)
            prefix = f'/{repository}/releases/download/{tag}/'
            if parsed.scheme != 'https' or parsed.netloc != 'github.com' or not parsed.path.startswith(prefix) or not parsed.path[len(prefix):] or parsed.query or parsed.fragment:
                raise ValueError('Updater payload must belong to this repository and release')
            if url in urls:
                raise ValueError('Platforms must not share an updater payload')
            if not isinstance(entry.get('signature'), str) or not entry['signature'].strip():
                raise ValueError('Every updater payload needs a signature')
            urls.add(url)
            platforms[platform] = entry
    if set(platforms) != PLATFORMS:
        raise ValueError(f'Missing updater platforms: {sorted(PLATFORMS - set(platforms))}')
    return {
        'version': version, 'notes': notes or f'Termx {tag}',
        'pub_date': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'platforms': platforms,
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--repository', required=True)
    parser.add_argument('--notes-file', type=Path)
    args = parser.parse_args()
    fragments = [json.loads(path.read_text()) for path in sorted(args.directory.glob('updater-*.json'))]
    result = merge(fragments, tag=args.tag, repository=args.repository,
                   notes=args.notes_file.read_text() if args.notes_file else None)
    (args.directory / 'latest.json').write_text(json.dumps(result, indent=2) + '\n')
