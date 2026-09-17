from __future__ import annotations

import ctypes
import hashlib
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

from termx.config import config_dir

SERVICE = "com.termx.agent"


def _env_name(provider_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9]", "_", provider_id).upper()
    return f"TERMX_AI_{safe}_API_KEY"


class CredentialStore:
    """Small OS-credential adapter with an injectable memory backend for tests."""

    def __init__(self, memory: dict[str, str] | None = None) -> None:
        self._memory = memory

    def available(self) -> bool:
        if self._memory is not None:
            return True
        if os.name == "nt":
            return True
        if sys_platform() == "darwin":
            return shutil.which("security") is not None
        return shutil.which("secret-tool") is not None

    def source(self, provider_id: str) -> str | None:
        if os.environ.get(_env_name(provider_id)):
            return "environment"
        if self.get(provider_id):
            return "credential_store"
        return None

    def get(self, provider_id: str) -> str | None:
        env = os.environ.get(_env_name(provider_id))
        if env:
            return env
        if self._memory is not None:
            return self._memory.get(provider_id)
        if os.name == "nt":
            return self._windows_get(provider_id)
        if sys_platform() == "darwin" and shutil.which("security"):
            proc = subprocess.run(
                ["security", "find-generic-password", "-s", SERVICE, "-a", provider_id, "-w"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else None
        if shutil.which("secret-tool"):
            proc = subprocess.run(
                ["secret-tool", "lookup", "service", SERVICE, "account", provider_id],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else None
        return None

    def set(self, provider_id: str, secret: str) -> None:
        if self._memory is not None:
            self._memory[provider_id] = secret
            return
        if os.name == "nt":
            self._windows_set(provider_id, secret)
            return
        if sys_platform() == "darwin" and shutil.which("security"):
            proc = subprocess.run(
                [
                    "security",
                    "add-generic-password",
                    "-U",
                    "-s",
                    SERVICE,
                    "-a",
                    provider_id,
                    "-w",
                    secret,
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip() or "could not save key in Keychain")
            return
        if shutil.which("secret-tool"):
            proc = subprocess.run(
                [
                    "secret-tool",
                    "store",
                    "--label",
                    f"Termx Agent provider {provider_id}",
                    "service",
                    SERVICE,
                    "account",
                    provider_id,
                ],
                input=secret,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip() or "could not save key in Secret Service")
            return
        raise RuntimeError(
            f"Secure credential storage is unavailable. Set {_env_name(provider_id)} on the host instead."
        )

    def delete(self, provider_id: str) -> None:
        if self._memory is not None:
            self._memory.pop(provider_id, None)
            return
        if os.name == "nt":
            try:
                self._windows_path(provider_id).unlink()
            except OSError:
                pass
            return
        if sys_platform() == "darwin" and shutil.which("security"):
            subprocess.run(
                ["security", "delete-generic-password", "-s", SERVICE, "-a", provider_id],
                capture_output=True,
                timeout=10,
                check=False,
            )
            return
        if shutil.which("secret-tool"):
            subprocess.run(
                ["secret-tool", "clear", "service", SERVICE, "account", provider_id],
                capture_output=True,
                timeout=10,
                check=False,
            )

    def _windows_path(self, provider_id: str) -> Path:
        digest = hashlib.sha256(provider_id.encode("utf-8")).hexdigest()
        return config_dir() / "credentials" / f"{digest}.dpapi"

    @staticmethod
    def _protect(data: bytes, *, decrypt: bool) -> bytes:
        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

        source_buffer = ctypes.create_string_buffer(data)
        source = DATA_BLOB(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
        destination = DATA_BLOB()
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        fn = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
        if decrypt:
            ok = fn(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(destination))
        else:
            ok = fn(ctypes.byref(source), SERVICE, None, None, None, 0, ctypes.byref(destination))
        if not ok:
            raise RuntimeError("Windows credential encryption failed")
        try:
            return ctypes.string_at(destination.pbData, destination.cbData)
        finally:
            kernel32.LocalFree(destination.pbData)

    def _windows_set(self, provider_id: str, secret: str) -> None:
        path = self._windows_path(provider_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self._protect(secret.encode("utf-8"), decrypt=False))
        if os.name == "posix":  # pragma: no cover - Windows branch
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)

    def _windows_get(self, provider_id: str) -> str | None:
        try:
            encrypted = self._windows_path(provider_id).read_bytes()
        except OSError:
            return None
        try:
            return self._protect(encrypted, decrypt=True).decode("utf-8")
        except (RuntimeError, UnicodeDecodeError):
            return None


def sys_platform() -> str:
    # Kept behind a function so platform branches are easy to test.
    import sys

    return sys.platform
