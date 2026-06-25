"""Secure credential-store boundary for future refresh/device secrets."""

from __future__ import annotations

import ctypes
import json
import re
import sys
from ctypes import wintypes
from typing import Protocol

from app.identity.errors import (
    CredentialDeleteError,
    CredentialNotFoundError,
    CredentialReadError,
    CredentialStoreError,
    CredentialStoreUnavailableError,
    CredentialWriteError,
    MalformedCredentialError,
)


TARGET_NAMESPACE = "Daftar/Identity/"
_ENTRY_VERSION = 1
_TARGET_COMPONENT_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class CredentialStore(Protocol):
    def write_credential(self, name: str, secret: str) -> None:
        """Write a secret into a secure OS-backed store."""

    def read_credential(self, name: str) -> str:
        """Read a secret from a secure OS-backed store."""

    def delete_credential(self, name: str) -> None:
        """Delete a secret from a secure OS-backed store."""


def credential_target(name: str) -> str:
    if not isinstance(name, str):
        raise MalformedCredentialError("Invalid credential entry name.")
    if name.startswith(TARGET_NAMESPACE):
        target = name
        suffix = name[len(TARGET_NAMESPACE):]
    elif name.startswith("Daftar/"):
        raise MalformedCredentialError("Invalid credential entry name.")
    else:
        suffix = name
        target = f"{TARGET_NAMESPACE}{name}"
    parts = suffix.split("/")
    if (
        not suffix
        or len(target) > 256
        or any(part in ("", ".", "..") for part in parts)
        or not all(_TARGET_COMPONENT_PATTERN.fullmatch(part) for part in parts)
    ):
        raise MalformedCredentialError("Invalid credential entry name.")
    return target


def _encode_secret(secret: str) -> bytes:
    if not isinstance(secret, str) or not secret:
        raise MalformedCredentialError("Credential secret must be a non-empty string.")
    return json.dumps(
        {"version": _ENTRY_VERSION, "secret": secret},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _decode_secret(blob: bytes) -> str:
    try:
        payload = json.loads(blob.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MalformedCredentialError("Credential entry is malformed.") from error
    if not isinstance(payload, dict) or payload.get("version") != _ENTRY_VERSION:
        raise MalformedCredentialError("Credential entry version is unsupported.")
    secret = payload.get("secret")
    if not isinstance(secret, str) or not secret:
        raise MalformedCredentialError("Credential entry is missing its secret.")
    return secret


class InMemoryCredentialStore:
    """Fully isolated test adapter; it never falls back to files or settings."""

    def __init__(self):
        self._entries: dict[str, bytes] = {}
        self.fail_writes = False
        self.fail_reads = False
        self.fail_deletes = False

    def write_credential(self, name: str, secret: str) -> None:
        if self.fail_writes:
            raise CredentialWriteError("Simulated credential write failure.")
        self._entries[credential_target(name)] = _encode_secret(secret)

    def read_credential(self, name: str) -> str:
        if self.fail_reads:
            raise CredentialReadError("Simulated credential read failure.")
        target = credential_target(name)
        try:
            blob = self._entries[target]
        except KeyError as error:
            raise CredentialNotFoundError("Credential entry was not found.") from error
        return _decode_secret(blob)

    def delete_credential(self, name: str) -> None:
        if self.fail_deletes:
            raise CredentialDeleteError("Simulated credential delete failure.")
        target = credential_target(name)
        try:
            del self._entries[target]
        except KeyError as error:
            raise CredentialNotFoundError("Credential entry was not found.") from error

    def inject_raw_entry(self, name: str, blob: bytes) -> None:
        self._entries[credential_target(name)] = blob


class WindowsCredentialManagerStore:
    """Windows Credential Manager adapter using CredWrite/CredRead/CredDelete."""

    def __init__(self):
        if not sys.platform.startswith("win"):
            self._advapi32 = None
            return
        self._advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        self._configure_api()

    def write_credential(self, name: str, secret: str) -> None:
        advapi32 = self._api()
        target = credential_target(name)
        blob = _encode_secret(secret)
        if len(blob) > 5120:
            raise CredentialWriteError("Credential entry exceeds Windows size limit.")
        buffer = ctypes.create_string_buffer(blob)
        credential = _CREDENTIALW()
        credential.Type = _CRED_TYPE_GENERIC
        credential.TargetName = target
        credential.CredentialBlobSize = len(blob)
        credential.CredentialBlob = ctypes.cast(buffer, _LPBYTE)
        credential.Persist = _CRED_PERSIST_LOCAL_MACHINE
        credential.UserName = "Daftar"
        if not advapi32.CredWriteW(ctypes.byref(credential), 0):
            raise CredentialWriteError(_windows_error_message("CredWriteW"))

    def read_credential(self, name: str) -> str:
        advapi32 = self._api()
        target = credential_target(name)
        credential_pointer = ctypes.POINTER(_CREDENTIALW)()
        if not advapi32.CredReadW(target, _CRED_TYPE_GENERIC, 0, ctypes.byref(credential_pointer)):
            code = ctypes.get_last_error()
            if code == _ERROR_NOT_FOUND:
                raise CredentialNotFoundError("Credential entry was not found.")
            raise CredentialReadError(_windows_error_message("CredReadW"))
        try:
            credential = credential_pointer.contents
            blob = ctypes.string_at(
                credential.CredentialBlob,
                credential.CredentialBlobSize,
            )
            return _decode_secret(blob)
        finally:
            advapi32.CredFree(credential_pointer)

    def delete_credential(self, name: str) -> None:
        advapi32 = self._api()
        target = credential_target(name)
        if not advapi32.CredDeleteW(target, _CRED_TYPE_GENERIC, 0):
            code = ctypes.get_last_error()
            if code == _ERROR_NOT_FOUND:
                raise CredentialNotFoundError("Credential entry was not found.")
            raise CredentialDeleteError(_windows_error_message("CredDeleteW"))

    def _api(self):
        if self._advapi32 is None:
            raise CredentialStoreUnavailableError(
                "Windows Credential Manager is unavailable on this platform."
            )
        return self._advapi32

    def _configure_api(self) -> None:
        advapi32 = self._advapi32
        advapi32.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]
        advapi32.CredWriteW.restype = wintypes.BOOL
        advapi32.CredReadW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(_CREDENTIALW)),
        ]
        advapi32.CredReadW.restype = wintypes.BOOL
        advapi32.CredDeleteW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        advapi32.CredDeleteW.restype = wintypes.BOOL
        advapi32.CredFree.argtypes = [wintypes.LPVOID]
        advapi32.CredFree.restype = None


_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2
_ERROR_NOT_FOUND = 1168
_LPBYTE = ctypes.POINTER(ctypes.c_ubyte)


class _FILETIME(ctypes.Structure):
    _fields_ = [
        ("dwLowDateTime", wintypes.DWORD),
        ("dwHighDateTime", wintypes.DWORD),
    ]


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", _FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", _LPBYTE),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", wintypes.LPVOID),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def _windows_error_message(operation: str) -> str:
    return f"{operation} failed with Windows error {ctypes.get_last_error()}."


def assert_no_plaintext_fallback() -> None:
    """Document the hard boundary: no alternate secret backends are implemented."""

    raise CredentialStoreError(
        "Credential fallback is intentionally unsupported; use the secure OS store only."
    )
