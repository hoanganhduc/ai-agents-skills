"""Handle-bound Windows owner and DACL validation.

The functions in this module intentionally use only the Python standard
library.  They open the target with write/delete sharing disabled, inspect the
file identity and security descriptor through that same handle, and reject
reparse points or allow ACEs for principals outside the current user, LOCAL
SYSTEM, and BUILTIN\\Administrators.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


class WindowsSecurityError(ValueError):
    """A Windows object is not private enough for security-sensitive use."""


NATIVE_WINDOWS_MUTATION_BLOCK = (
    "native Windows mutation is disabled until installer writes, replacements, "
    "and deletions remain bound to the validated Windows handle"
)


def host_is_native_windows() -> bool:
    """Return true only for the native Windows filesystem implementation."""
    return os.name == "nt"


def require_handle_bound_mutation(operation: str) -> None:
    """Fail closed while validation handles and mutation paths can diverge.

    The current DACL/reparse inspection is handle-bound, but the installer later
    closes that handle and mutates by pathname. Native Windows apply/uninstall/
    rollback therefore remain dry-run-only until the mutation itself is issued
    against the validated handle (or a parent directory handle with equivalent
    identity guarantees).
    """
    if host_is_native_windows():
        raise WindowsSecurityError(f"{operation}: {NATIVE_WINDOWS_MUTATION_BLOCK}")


def private_path_snapshot(path: Path, *, directory: bool) -> dict[str, Any]:
    """Return identities after enforcing the full no-reparse ACL chain."""
    if os.name != "nt":
        raise OSError("Windows handle security checks are only available on Windows")
    absolute = Path(os.path.abspath(path))
    ancestors: list[dict[str, Any]] = []
    cursor = absolute.parent
    parent_paths: list[Path] = []
    while cursor != cursor.parent:
        parent_paths.append(cursor)
        cursor = cursor.parent
    parent_paths.append(cursor)
    parent_paths.reverse()
    for parent in parent_paths:
        ancestors.append(
            _private_path_snapshot_windows(
                parent,
                directory=True,
                strict=parent == absolute.parent,
            )
        )
    snapshot = _private_path_snapshot_windows(absolute, directory=directory, strict=True)
    snapshot["ancestor_snapshots"] = ancestors
    return snapshot


def private_file_issue(path: Path) -> str | None:
    if os.name != "nt":
        return None
    try:
        private_path_snapshot(path, directory=False)
    except (OSError, WindowsSecurityError) as exc:
        return str(exc)
    return None


def private_directory_issue(path: Path) -> str | None:
    if os.name != "nt":
        return None
    try:
        private_path_snapshot(path, directory=True)
    except (OSError, WindowsSecurityError) as exc:
        return str(exc)
    return None


def _private_path_snapshot_windows(
    path: Path,
    *,
    directory: bool,
    strict: bool,
) -> dict[str, Any]:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

    class FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

    class BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", FILETIME),
            ("ftLastAccessTime", FILETIME),
            ("ftLastWriteTime", FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    class ACL_SIZE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("AceCount", wintypes.DWORD),
            ("AclBytesInUse", wintypes.DWORD),
            ("AclBytesFree", wintypes.DWORD),
        ]

    class ACE_HEADER(ctypes.Structure):
        _fields_ = [
            ("AceType", ctypes.c_ubyte),
            ("AceFlags", ctypes.c_ubyte),
            ("AceSize", wintypes.WORD),
        ]

    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(BY_HANDLE_FILE_INFORMATION),
    ]
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL

    advapi32.GetSecurityInfo.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
    ]
    advapi32.GetSecurityInfo.restype = wintypes.DWORD
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.CreateWellKnownSid.argtypes = [
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.CreateWellKnownSid.restype = wintypes.BOOL
    advapi32.ConvertStringSidToSidW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(wintypes.LPVOID),
    ]
    advapi32.ConvertStringSidToSidW.restype = wintypes.BOOL
    advapi32.EqualSid.argtypes = [wintypes.LPVOID, wintypes.LPVOID]
    advapi32.EqualSid.restype = wintypes.BOOL
    advapi32.GetAclInformation.argtypes = [
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    advapi32.GetAclInformation.restype = wintypes.BOOL
    advapi32.GetAce.argtypes = [
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
    ]
    advapi32.GetAce.restype = wintypes.BOOL

    READ_CONTROL = 0x00020000
    FILE_READ_ATTRIBUTES = 0x00000080
    FILE_SHARE_READ = 0x00000001
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_DIRECTORY = 0x00000010
    FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
    FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    SE_FILE_OBJECT = 1
    OWNER_SECURITY_INFORMATION = 0x00000001
    DACL_SECURITY_INFORMATION = 0x00000004
    TOKEN_QUERY = 0x0008
    TOKEN_USER = 1
    ACL_SIZE_INFORMATION_CLASS = 2
    WIN_LOCAL_SYSTEM_SID = 22
    WIN_BUILTIN_ADMINISTRATORS_SID = 26
    SECURITY_MAX_SID_SIZE = 68
    ACCESS_ALLOWED_ACE_TYPES = {0x00, 0x05, 0x09, 0x0B}
    OBJECT_ACE_TYPES = {0x05, 0x0B}
    ACE_OBJECT_TYPE_PRESENT = 0x1
    ACE_INHERITED_OBJECT_TYPE_PRESENT = 0x2
    MUTATION_ACCESS_MASK = 0x500D0156
    TRUSTED_INSTALLER_SID = (
        "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
    )

    def fail_api(name: str) -> OSError:
        return ctypes.WinError(ctypes.get_last_error(), f"{name} failed")

    def current_user_sid() -> tuple[ctypes.Array[ctypes.c_char], wintypes.LPVOID]:
        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
            raise fail_api("OpenProcessToken")
        try:
            needed = wintypes.DWORD()
            advapi32.GetTokenInformation(token, TOKEN_USER, None, 0, ctypes.byref(needed))
            if needed.value == 0:
                raise fail_api("GetTokenInformation(size)")
            buffer = ctypes.create_string_buffer(needed.value)
            if not advapi32.GetTokenInformation(
                token,
                TOKEN_USER,
                buffer,
                needed,
                ctypes.byref(needed),
            ):
                raise fail_api("GetTokenInformation")
            sid = ctypes.cast(buffer, ctypes.POINTER(wintypes.LPVOID)).contents
            return buffer, sid
        finally:
            kernel32.CloseHandle(token)

    def well_known_sid(kind: int) -> tuple[ctypes.Array[ctypes.c_char], wintypes.LPVOID]:
        size = wintypes.DWORD(SECURITY_MAX_SID_SIZE)
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi32.CreateWellKnownSid(kind, None, buffer, ctypes.byref(size)):
            raise fail_api("CreateWellKnownSid")
        return buffer, ctypes.cast(buffer, wintypes.LPVOID)

    def string_sid(value: str) -> wintypes.LPVOID:
        pointer = wintypes.LPVOID()
        if not advapi32.ConvertStringSidToSidW(value, ctypes.byref(pointer)):
            raise fail_api("ConvertStringSidToSidW")
        return pointer

    flags = FILE_FLAG_OPEN_REPARSE_POINT | (FILE_FLAG_BACKUP_SEMANTICS if directory else 0)
    handle = kernel32.CreateFileW(
        str(path),
        READ_CONTROL | FILE_READ_ATTRIBUTES,
        FILE_SHARE_READ,
        None,
        OPEN_EXISTING,
        flags,
        None,
    )
    if handle == INVALID_HANDLE_VALUE:
        raise fail_api("CreateFileW")

    security_descriptor = wintypes.LPVOID()
    try:
        info = BY_HANDLE_FILE_INFORMATION()
        if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise fail_api("GetFileInformationByHandle")
        is_directory = bool(info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)
        if is_directory != directory:
            raise WindowsSecurityError("Windows private path has the wrong object type")
        if info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT:
            raise WindowsSecurityError("Windows private path must not be a reparse point")
        if not directory and info.nNumberOfLinks != 1:
            raise WindowsSecurityError("Windows private file must have exactly one hard link")

        owner = wintypes.LPVOID()
        dacl = wintypes.LPVOID()
        error = advapi32.GetSecurityInfo(
            handle,
            SE_FILE_OBJECT,
            OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
            ctypes.byref(owner),
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(security_descriptor),
        )
        if error != 0:
            raise ctypes.WinError(error, "GetSecurityInfo failed")
        if not owner or not dacl:
            raise WindowsSecurityError("Windows private path must have an explicit owner and non-null DACL")

        current_buffer, current_sid = current_user_sid()
        system_buffer, system_sid = well_known_sid(WIN_LOCAL_SYSTEM_SID)
        admin_buffer, admin_sid = well_known_sid(WIN_BUILTIN_ADMINISTRATORS_SID)
        trusted_installer_sid = string_sid(TRUSTED_INSTALLER_SID)
        # Keep SID buffers alive while every comparison runs.
        _sid_buffers = (current_buffer, system_buffer, admin_buffer)
        trusted_owner_sids = (current_sid, system_sid, admin_sid, trusted_installer_sid)
        if (
            (strict and not advapi32.EqualSid(owner, current_sid))
            or (
                not strict
                and not any(
                    advapi32.EqualSid(owner, trusted_owner)
                    for trusted_owner in trusted_owner_sids
                )
            )
        ):
            raise WindowsSecurityError(
                "Windows private path has an untrusted owner in its ancestor chain"
            )

        acl_info = ACL_SIZE_INFORMATION()
        if not advapi32.GetAclInformation(
            dacl,
            ctypes.byref(acl_info),
            ctypes.sizeof(acl_info),
            ACL_SIZE_INFORMATION_CLASS,
        ):
            raise fail_api("GetAclInformation")
        allowed_sids = trusted_owner_sids
        for index in range(acl_info.AceCount):
            ace = wintypes.LPVOID()
            if not advapi32.GetAce(dacl, index, ctypes.byref(ace)):
                raise fail_api("GetAce")
            header = ctypes.cast(ace, ctypes.POINTER(ACE_HEADER)).contents
            if header.AceType not in ACCESS_ALLOWED_ACE_TYPES:
                continue
            sid_offset = 8
            if header.AceType in OBJECT_ACE_TYPES:
                object_flags = ctypes.c_uint32.from_address(ace.value + 8).value
                sid_offset = 12
                if object_flags & ACE_OBJECT_TYPE_PRESENT:
                    sid_offset += 16
                if object_flags & ACE_INHERITED_OBJECT_TYPE_PRESENT:
                    sid_offset += 16
            if sid_offset >= header.AceSize:
                raise WindowsSecurityError("Windows private path has a malformed allow ACE")
            ace_sid = wintypes.LPVOID(ace.value + sid_offset)
            access_mask = ctypes.c_uint32.from_address(ace.value + 4).value
            if (
                not any(advapi32.EqualSid(ace_sid, allowed) for allowed in allowed_sids)
                and (strict or bool(access_mask & MUTATION_ACCESS_MASK))
            ):
                raise WindowsSecurityError(
                    "Windows private path DACL grants unsafe access outside the "
                    "current account, SYSTEM, Administrators, and TrustedInstaller"
                )

        return {
            "volume_serial": int(info.dwVolumeSerialNumber),
            "file_index": (int(info.nFileIndexHigh) << 32) | int(info.nFileIndexLow),
            "nlink": int(info.nNumberOfLinks),
            "attributes": int(info.dwFileAttributes),
            "owner_private_dacl": True,
            "strict_acl": strict,
        }
    finally:
        if "trusted_installer_sid" in locals() and trusted_installer_sid:
            kernel32.LocalFree(trusted_installer_sid)
        if security_descriptor:
            kernel32.LocalFree(security_descriptor)
        kernel32.CloseHandle(handle)
