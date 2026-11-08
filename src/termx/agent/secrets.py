from __future__ import annotations

import ctypes
import hashlib
import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

from termx.config import config_dir

SERVICE = "com.termx.agent"


def _env_name(provider_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9]", "_", provider_id).upper()
    return f"TERMX_AI_{safe}_API_KEY"


def _run(argv: list[str], store: str, *, input: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run an OS credential tool. Failures carry a plain message, never the command line or the key.

    `from None` drops the exception context: a TimeoutExpired holds the full argv.
    """
    try:
        return subprocess.run(argv, input=input, capture_output=True, text=True, timeout=10, check=False)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"The {store} did not respond. Unlock it and try again.") from None
    except OSError:
        raise RuntimeError(f"The {store} could not be started. Check that it is installed and try again.") from None


def _scrub(message: str, secret: str) -> str:
    return message.replace(secret, "[key hidden]") if secret else message


def _security_quote(value: str) -> str:
    """Quote one argument for `security -i`, which reads commands from stdin."""
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("A key or account name cannot contain a line break or control character.")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


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
            proc = _run(["security", "find-generic-password", "-s", SERVICE, "-a", provider_id, "-w"], "Keychain")
            return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else None
        if shutil.which("secret-tool"):
            proc = _run(["secret-tool", "lookup", "service", SERVICE, "account", provider_id], "Secret Service")
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
            # The key goes in on stdin: a process listing shows argv to every local user.
            command = "add-generic-password -U -s {} -a {} -w {}\n".format(
                _security_quote(SERVICE), _security_quote(provider_id), _security_quote(secret)
            )
            proc = _run(["security", "-i"], "Keychain", input=command)
            if proc.returncode != 0:
                raise RuntimeError(_scrub(proc.stderr.strip(), secret) or "The Keychain did not accept the key.")
            return
        if shutil.which("secret-tool"):
            proc = _run(
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
                "Secret Service",
                input=secret,
            )
            if proc.returncode != 0:
                raise RuntimeError(_scrub(proc.stderr.strip(), secret) or "The Secret Service did not accept the key.")
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
            _run(["security", "delete-generic-password", "-s", SERVICE, "-a", provider_id], "Keychain")
            return
        if shutil.which("secret-tool"):
            _run(["secret-tool", "clear", "service", SERVICE, "account", provider_id], "Secret Service")

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
        from termx.private_files import protect_private_path
        protect_private_path(path.parent, directory=True)
        encrypted = self._protect(secret.encode("utf-8"), decrypt=False)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".credential-")
        try:
            with os.fdopen(fd, "wb") as handle:
                protect_private_path(Path(temporary))
                handle.write(encrypted)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        protect_private_path(path)

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
