#!/usr/bin/env python3
"""Reject bundled Intel binaries/metadata that require a newer macOS.

Read Mach-O load commands directly so the same gate can be tested on Linux.
SDK version is deliberately ignored: only the runtime deployment minimum matters.
This does not substitute for launching the installed app on the minimum OS.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import plistlib
import struct

INTEL = 0x01000007
THIN = {b'\xce\xfa\xed\xfe': ('<', 28), b'\xcf\xfa\xed\xfe': ('<', 32),
        b'\xfe\xed\xfa\xce': ('>', 28), b'\xfe\xed\xfa\xcf': ('>', 32)}
FAT = {b'\xca\xfe\xba\xbe': ('>', False), b'\xbe\xba\xfe\xca': ('<', False),
       b'\xca\xfe\xba\xbf': ('>', True), b'\xbf\xba\xfe\xca': ('<', True)}


def version(value: str) -> tuple[int, int, int]:
    parts = value.split('.')
    if not 1 <= len(parts) <= 3 or any(not part.isdecimal() for part in parts):
        raise ValueError(f'Invalid macOS version: {value!r}')
    return tuple([int(part) for part in parts] + [0] * (3 - len(parts)))


def packed_version(value: int) -> str:
    return f'{value >> 16}.{(value >> 8) & 255}.{value & 255}'


def intel_minimums(path: Path) -> list[str]:
    """Return minima for Intel slices; non-Mach-O/other CPU resources are ignored."""
    size = path.stat().st_size
    with path.open('rb') as stream:
        def read(offset: int, count: int, limit: int = size) -> bytes:
            if offset < 0 or count < 0 or offset + count > limit:
                raise ValueError('Truncated Mach-O header or load command')
            stream.seek(offset)
            data = stream.read(count)
            if len(data) != count:
                raise ValueError('Truncated Mach-O data')
            return data

        magic = stream.read(4)
        slices = [(0, size)]
        if magic in FAT:
            endian, wide = FAT[magic]
            count = struct.unpack(endian + 'I', read(4, 4))[0]
            if not 1 <= count <= 64:
                # Java class files share CAFEBABE but not the fat architecture table.
                if path.suffix == '.class':
                    return []
                raise ValueError('Invalid Mach-O fat architecture count')
            entry_size = 32 if wide else 20
            slices = []
            for index in range(count):
                entry = read(8 + index * entry_size, entry_size)
                cpu = struct.unpack_from(endian + 'I', entry)[0]
                offset, length = struct.unpack_from(endian + ('QQ' if wide else 'II'), entry, 8)
                if offset < 8 + count * entry_size or offset + length > size:
                    raise ValueError('Invalid Mach-O fat slice bounds')
                if cpu == INTEL:
                    slices.append((offset, length))
        elif magic not in THIN:
            return []

        minima = []
        for offset, length in slices:
            limit = offset + length
            magic = read(offset, 4, limit)
            if magic not in THIN:
                raise ValueError('Invalid Mach-O slice magic')
            endian, header_size = THIN[magic]
            header = read(offset, header_size, limit)
            cpu, _, kind, count, command_bytes = struct.unpack_from(endian + '5I', header, 4)
            if cpu != INTEL:
                continue
            if kind == 1:  # MH_OBJECT is not a loadable runtime binary.
                continue
            start = offset + header_size
            end = start + command_bytes
            if end > limit or count > command_bytes // 8:
                raise ValueError('Invalid Mach-O load command bounds')
            found = []
            for _ in range(count):
                command, length = struct.unpack(endian + 'II', read(start, 8, end))
                if length < 8 or start + length > end:
                    raise ValueError('Invalid Mach-O load command size')
                if command == 0x32:  # LC_BUILD_VERSION
                    if length < 24:
                        raise ValueError('Truncated LC_BUILD_VERSION')
                    platform, minimum = struct.unpack(endian + 'II', read(start + 8, 8, end))
                    if platform != 1:
                        raise ValueError(f'Intel binary targets platform {platform}, not macOS')
                    found.append(packed_version(minimum))
                elif command == 0x24:  # LC_VERSION_MIN_MACOSX
                    if length < 16:
                        raise ValueError('Truncated LC_VERSION_MIN_MACOSX')
                    found.append(packed_version(struct.unpack(endian + 'I', read(start + 8, 4, end))[0]))
                start += length
            if start != end or not found:
                raise ValueError('Missing or inconsistent macOS deployment commands')
            minima.extend(found)
        return minima


def verify(root: Path, minimum: str, required: list[str]) -> dict:
    floor = version(minimum)
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError('Compatibility root must be a directory')
    report = {'minimum_macos': minimum, 'architecture': 'x86_64', 'binaries': [], 'bundles': [], 'errors': []}
    required = list(required)
    if root.suffix == '.app':
        info = plistlib.loads((root / 'Contents/Info.plist').read_bytes())
        executable = info.get('CFBundleExecutable', '')
        if not isinstance(executable, str) or not executable or Path(executable).name != executable:
            raise ValueError('Invalid application executable metadata')
        required.append('Contents/MacOS/' + executable)
        if version(info.get('LSMinimumSystemVersion', '')) != floor:
            report['errors'].append('Application minimum does not match the configured deployment target')
    seen = set()
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            if not path.resolve().is_relative_to(root):
                report['errors'].append(f'{path.relative_to(root)}: external symbolic link')
            continue  # Inspect each real framework file once, not its aliases.
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        try:
            minima = intel_minimums(path)
            if minima:
                seen.add(relative)
                report['binaries'].append({'path': relative, 'minimums': minima})
                for value in minima:
                    if version(value) > floor:
                        report['errors'].append(f'{relative}: requires macOS {value}, exceeds {minimum}')
            if path.name == 'Info.plist':
                info = plistlib.loads(path.read_bytes())
                by_arch = info.get('LSMinimumSystemVersionByArchitecture', {})
                value = by_arch.get('x86_64', info.get('LSMinimumSystemVersion'))
                if value:
                    report['bundles'].append({'path': relative, 'minimum': value})
                    if version(value) > floor:
                        report['errors'].append(f'{relative}: requires macOS {value}, exceeds {minimum}')
        except (OSError, ValueError, struct.error, plistlib.InvalidFileException) as error:
            report['errors'].append(f'{relative}: {error}')
    for relative in required:
        resolved = (root / relative).resolve()
        if not resolved.is_relative_to(root) or resolved.relative_to(root).as_posix() not in seen:
            report['errors'].append(f'{relative}: required Intel executable is missing or unverified')
    if not seen:
        report['errors'].append('No Intel runtime binaries were inspected')
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--minimum', required=True)
    parser.add_argument('--require', action='append', default=[])
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    report = verify(args.root, args.minimum, args.require)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    if report['errors']:
        raise SystemExit('\n'.join(report['errors']))
    print(f"Verified {len(report['binaries'])} Intel binaries for macOS {args.minimum}; report: {args.report}")


if __name__ == '__main__':
    main()
