#!/usr/bin/env python3
"""Build private static TLS libraries for the Intel deployment minimum.

Homebrew's binaries follow the build host OS and must not enter the installer.
Keep this supported OpenSSL LTS source pin/checksum current when updating TLS.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

from verify_macos_compatibility import version

VERSION = '3.5.9'
SHA256 = '603f5602e2eef00d77fbd429d34dcd5822bb301757a1bc9cdb24c670f1eb859a'
URL = f'https://github.com/openssl/openssl/releases/download/openssl-{VERSION}/openssl-{VERSION}.tar.gz'


def unpack_source(archive: Path, destination: Path) -> Path:
    with archive.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != SHA256:
            raise ValueError('OpenSSL source checksum mismatch')
    with tarfile.open(archive) as source:
        source.extractall(destination, filter='data')
    root = destination / f'openssl-{VERSION}'
    if not (root / 'Configure').is_file():
        raise ValueError('OpenSSL Configure entrypoint missing')
    return root


def configure_arguments(prefix: Path, minimum: str) -> list[str]:
    version(minimum)
    if not prefix.is_absolute():
        raise ValueError('OpenSSL prefix must be absolute')
    return ['perl', 'Configure', 'darwin64-x86_64-cc', 'no-shared', 'no-tests', 'no-module',
            '-fPIC', f'-mmacosx-version-min={minimum}', f'--prefix={prefix}', '--libdir=lib']


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix', type=Path, required=True)
    parser.add_argument('--minimum', required=True)
    args = parser.parse_args()
    if sys.platform != 'darwin' or platform.machine() != 'x86_64':
        parser.error('This TLS build requires a native Intel macOS host')
    argv = configure_arguments(args.prefix, args.minimum)
    if os.environ.get('MACOSX_DEPLOYMENT_TARGET') != args.minimum:
        parser.error('Compiler deployment target differs from the requested minimum')
    with tempfile.TemporaryDirectory(prefix='termx-openssl-') as temporary:
        archive = Path(temporary) / 'openssl.tar.gz'
        with urllib.request.urlopen(URL, timeout=120) as response, archive.open('wb') as output:
            while block := response.read(1024 * 1024):
                output.write(block)
        source = unpack_source(archive, Path(temporary))
        subprocess.run(argv, cwd=source, check=True)
        subprocess.run(['make', '-j2', 'build_sw'], cwd=source, check=True)
        subprocess.run(['make', 'install_sw'], cwd=source, check=True)
    for name in ('lib/libssl.a', 'lib/libcrypto.a', 'include/openssl/ssl.h'):
        if not (args.prefix / name).is_file():
            raise SystemExit(f'Private OpenSSL install is incomplete: {name}')
    with open(os.environ['GITHUB_ENV'], 'a') as output:
        output.write(f'OPENSSL_DIR={args.prefix}\nOPENSSL_STATIC=1\nOPENSSL_NO_VENDOR=1\n')
    (args.prefix / 'termx-build.json').write_text(json.dumps({
        'version': VERSION, 'sha256': SHA256, 'minimum_macos': args.minimum, 'static': True,
    }, indent=2) + '\n')


if __name__ == '__main__':
    main()
