"""Host secrets: Windows Credential Manager or explicit process environment.

No fallback writes credentials to a file. Each state home has a separate
Credential Manager namespace, allowing isolated deployments.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
from pathlib import Path
from ctypes import wintypes


def state_home(home: str | Path | None = None) -> Path:
    return Path(home or os.environ.get("MEMORY_BASE_HOME") or Path.home() / ".agent-memory-base").resolve()


def env_name(name: str) -> str:
    return "MEMORY_BASE_SECRET_" + name.upper().replace("-", "_")


def _target(name: str, home: str | Path | None) -> str:
    namespace = hashlib.sha256(str(state_home(home)).casefold().encode()).hexdigest()[:16]
    return f"AgentMemoryBase/{namespace}/{name}"


class _CREDENTIAL(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR),
    ]


def get_secret(name: str, home: str | Path | None = None) -> str | None:
    """Read a named secret without logging it; missing credentials return None."""
    value = os.environ.get(env_name(name))
    if value:
        return value
    if os.name != "nt":
        return None
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                             ctypes.POINTER(ctypes.POINTER(_CREDENTIAL))]
    api.CredReadW.restype = wintypes.BOOL
    api.CredFree.argtypes = [ctypes.c_void_p]
    credential = ctypes.POINTER(_CREDENTIAL)()
    if not api.CredReadW(_target(name, home), 1, 0, ctypes.byref(credential)):
        error = ctypes.get_last_error()
        if error == 1168:  # ERROR_NOT_FOUND
            return None
        raise OSError(error, "Windows Credential Manager read failed")
    try:
        return ctypes.string_at(credential.contents.CredentialBlob,
                                credential.contents.CredentialBlobSize).decode("utf-8")
    finally:
        api.CredFree(credential)


def set_secret(name: str, value: str, home: str | Path | None = None) -> None:
    """Store in the user's protected Windows vault, never in application files."""
    if not isinstance(value, str) or not value:
        raise ValueError("Secret must be a nonempty string")
    if os.name != "nt":
        raise RuntimeError(f"Set {env_name(name)} in the service environment on this platform")
    blob = value.encode("utf-8")
    buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    credential = _CREDENTIAL()
    credential.Type = 1  # CRED_TYPE_GENERIC
    credential.TargetName = _target(name, home)
    credential.CredentialBlobSize = len(blob)
    credential.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
    credential.Persist = 2  # CRED_PERSIST_LOCAL_MACHINE (current Windows user)
    credential.UserName = "AgentMemoryBase"
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    api.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIAL), wintypes.DWORD]
    api.CredWriteW.restype = wintypes.BOOL
    if not api.CredWriteW(ctypes.byref(credential), 0):
        raise OSError(ctypes.get_last_error(), "Windows Credential Manager write failed")
