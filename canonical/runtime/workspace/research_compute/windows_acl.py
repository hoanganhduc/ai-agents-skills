"""Native-Windows handle-bound owner/DACL guards for broker state.

This module is imported only by native-Windows branches.  It deliberately
uses the standard library and opens each object with write/delete sharing
disabled while its identity and security descriptor are checked.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
import os
from pathlib import Path
import re
from typing import Iterator


@contextmanager
def private_path_guard(
    path: Path,
    *,
    directory: bool,
    _include_ancestors: bool = True,
    _strict: bool = True,
    _check_dacl: bool = True,
) -> Iterator[dict[str, int]]:
    if os.name != "nt":
        yield {}
        return

    absolute = Path(os.path.abspath(path))
    if _include_ancestors:
        # Hold every ancestor no-follow handle for the full protected operation.
        # The immediate private parent is strict; higher system ancestors are
        # pinned by handle and must still be SYSTEM/Administrators/TrustedInstaller
        # owned, but their DACLs are not predicate-checked. Stock Windows grants
        # ``AU:(LC)`` (add-subdirectory) on the drive root, so demanding a
        # mutation-free chain up to ``C:\`` can never be satisfied. Nothing is
        # given up: a right on an ancestor confers no access to an existing
        # descendant, and any grant that does reach the target by inheritance is
        # materialised into the target's own DACL, which is checked below.
        ancestors: list[Path] = []
        cursor = absolute.parent
        while cursor != cursor.parent:
            ancestors.append(cursor)
            cursor = cursor.parent
        ancestors.append(cursor)
        ancestors.reverse()
        with ExitStack() as stack:
            for ancestor in ancestors:
                stack.enter_context(
                    private_path_guard(
                        ancestor,
                        directory=True,
                        _include_ancestors=False,
                        _strict=ancestor == absolute.parent,
                        _check_dacl=ancestor == absolute.parent,
                    )
                )
            snapshot = stack.enter_context(
                private_path_guard(
                    absolute,
                    directory=directory,
                    _include_ancestors=False,
                    _strict=True,
                )
            )
            yield {**snapshot, "ancestor_handles": len(ancestors)}
        return

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

    class FILETIME(ctypes.Structure):
        _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

    class FILE_INFO(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD),
            ("created", FILETIME),
            ("accessed", FILETIME),
            ("written", FILETIME),
            ("volume", wintypes.DWORD),
            ("size_high", wintypes.DWORD),
            ("size_low", wintypes.DWORD),
            ("links", wintypes.DWORD),
            ("index_high", wintypes.DWORD),
            ("index_low", wintypes.DWORD),
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
        ctypes.POINTER(FILE_INFO),
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
    advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = wintypes.BOOL
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
    advapi32.ConvertSidToStringSidW.argtypes = [
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.LPWSTR),
    ]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL

    READ_CONTROL = 0x00020000
    FILE_READ_ATTRIBUTES = 0x80
    FILE_SHARE_READ = 0x1
    OPEN_EXISTING = 3
    OPEN_REPARSE = 0x00200000
    BACKUP_SEMANTICS = 0x02000000
    ATTR_DIRECTORY = 0x10
    ATTR_REPARSE = 0x400
    INVALID_HANDLE = ctypes.c_void_p(-1).value
    OWNER_AND_DACL = 0x1 | 0x4
    TOKEN_QUERY = 0x8
    TOKEN_USER = 1

    def win_error(name: str) -> OSError:
        return ctypes.WinError(ctypes.get_last_error(), f"{name} failed")

    def current_sid_string() -> str:
        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
            raise win_error("OpenProcessToken")
        try:
            size = wintypes.DWORD()
            advapi32.GetTokenInformation(token, TOKEN_USER, None, 0, ctypes.byref(size))
            if not size.value:
                raise win_error("GetTokenInformation(size)")
            buffer = ctypes.create_string_buffer(size.value)
            if not advapi32.GetTokenInformation(
                token, TOKEN_USER, buffer, size, ctypes.byref(size)
            ):
                raise win_error("GetTokenInformation")
            sid = ctypes.cast(buffer, ctypes.POINTER(wintypes.LPVOID)).contents
            text_pointer = wintypes.LPWSTR()
            if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text_pointer)):
                raise win_error("ConvertSidToStringSidW")
            try:
                return str(text_pointer.value)
            finally:
                kernel32.LocalFree(text_pointer)
        finally:
            kernel32.CloseHandle(token)

    flags = OPEN_REPARSE | (BACKUP_SEMANTICS if directory else 0)
    handle = kernel32.CreateFileW(
        str(absolute),
        READ_CONTROL | FILE_READ_ATTRIBUTES,
        FILE_SHARE_READ,
        None,
        OPEN_EXISTING,
        flags,
        None,
    )
    if handle == INVALID_HANDLE:
        raise win_error("CreateFileW")

    descriptor = wintypes.LPVOID()
    descriptor_text = wintypes.LPWSTR()
    try:
        info = FILE_INFO()
        if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise win_error("GetFileInformationByHandle")
        if bool(info.attributes & ATTR_DIRECTORY) != directory:
            raise OSError("Windows state object has the wrong type")
        if info.attributes & ATTR_REPARSE:
            raise OSError("Windows state object must not be a reparse point")
        if not directory and int(info.links) != 1:
            raise OSError("Windows state file must have exactly one hard link")

        owner = wintypes.LPVOID()
        dacl = wintypes.LPVOID()
        result = advapi32.GetSecurityInfo(
            handle,
            1,
            OWNER_AND_DACL,
            ctypes.byref(owner),
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(descriptor),
        )
        if result:
            raise ctypes.WinError(result, "GetSecurityInfo failed")
        if not owner or not dacl:
            raise OSError("Windows state owner or DACL is absent")
        length = wintypes.DWORD()
        if not advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW(
            descriptor,
            1,
            OWNER_AND_DACL,
            ctypes.byref(descriptor_text),
            ctypes.byref(length),
        ):
            raise win_error("ConvertSecurityDescriptorToStringSecurityDescriptorW")
        sddl = str(descriptor_text.value)
        current_sid = current_sid_string()
        # GROUP_SECURITY_INFORMATION is intentionally not requested, so Windows may emit
        # either ``O:<sid>D:...`` or ``O:<sid>G:...D:...``. Accept both shapes while keeping
        # the owner comparison exact.
        owner_match = re.match(r"^O:(.*?)(?:G:|D:)", sddl)
        trusted_sids = {
            current_sid,
            "SY",
            "S-1-5-18",
            "BA",
            "S-1-5-32-544",
            "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464",
        }
        owner_value = owner_match.group(1) if owner_match is not None else ""
        if (
            not owner_value
            or (_strict and owner_value != current_sid)
            or (not _strict and owner_value not in trusted_sids)
        ):
            raise OSError("Windows state object has an untrusted owner in its ancestor chain")
        dacl_marker = sddl.find("D:")
        if dacl_marker < 0:
            raise OSError("Windows state object has no DACL")

        mutation_mask = 0x500D0156

        def mutates(rights: str) -> bool:
            if rights.startswith("0x"):
                try:
                    return bool(int(rights, 16) & mutation_mask)
                except ValueError:
                    return True
            tokens = {rights[index : index + 2] for index in range(0, len(rights), 2)}
            return bool(
                tokens
                & {
                    "GA", "GW", "FA", "FW", "WD", "WO", "DC", "AD",
                    "DE", "WA", "AS", "WP", "CC", "LC", "SW", "DT",
                }
            )

        for ace in re.findall(r"\(([^()]*)\)", sddl[dacl_marker + 2 :]) if _check_dacl else ():
            fields = ace.split(";")
            if len(fields) < 6 or fields[0] not in {"A", "OA", "XA", "XU", "ZA"}:
                continue
            # An INHERIT_ONLY ace confers nothing on this object; it only seeds
            # children, whose own DACLs are checked when they are guarded in turn.
            # Split into two-character tokens rather than substring-matching, so a
            # ``CIOI`` flag pair cannot be misread as ``IO``.
            flag_tokens = {
                fields[1][index : index + 2] for index in range(0, len(fields[1]), 2)
            }
            if "IO" in flag_tokens:
                continue
            if fields[5] not in trusted_sids and (_strict or mutates(fields[2])):
                raise OSError(
                    "Windows state DACL grants unsafe access outside "
                    "owner/SYSTEM/Administrators/TrustedInstaller"
                )
        yield {
            "volume": int(info.volume),
            "index": (int(info.index_high) << 32) | int(info.index_low),
            "links": int(info.links),
        }
    finally:
        if descriptor_text:
            kernel32.LocalFree(descriptor_text)
        if descriptor:
            kernel32.LocalFree(descriptor)
        kernel32.CloseHandle(handle)
