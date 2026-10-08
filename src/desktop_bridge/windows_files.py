"""Windows context IO: reject reparse points and pin directories during writes."""
from __future__ import annotations

import ctypes as C
import os
from contextlib import contextmanager
from functools import lru_cache

from .state import BridgeError


class BasicInfo(C.Structure):
    _fields_ = [("CreationTime", C.c_int64), ("LastAccessTime", C.c_int64),
                ("LastWriteTime", C.c_int64), ("ChangeTime", C.c_int64),
                ("FileAttributes", C.c_uint32)]


class Overlapped(C.Structure):
    _fields_ = [("Internal", C.c_size_t), ("InternalHigh", C.c_size_t),
                ("Offset", C.c_uint32), ("OffsetHigh", C.c_uint32), ("hEvent", C.c_void_p)]


@lru_cache
def kernel():
    api = C.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CreateFileW": ([C.c_wchar_p, C.c_uint32, C.c_uint32, C.c_void_p,
                         C.c_uint32, C.c_uint32, C.c_void_p], C.c_void_p),
        "CloseHandle": ([C.c_void_p], C.c_int),
        "GetFileInformationByHandleEx": ([C.c_void_p, C.c_int, C.c_void_p, C.c_uint32], C.c_int),
        "GetFileType": ([C.c_void_p], C.c_uint32),
        "LockFileEx": ([C.c_void_p, C.c_uint32, C.c_uint32, C.c_uint32,
                        C.c_uint32, C.POINTER(Overlapped)], C.c_int),
    }
    for name, (args, result) in signatures.items():
        function = getattr(api, name)
        function.argtypes, function.restype = args, result
    return api


def open_handle(path, *, directory=False, write=False, create=False, exclusive=False):
    api = kernel()
    # Omitting FILE_SHARE_DELETE pins this directory/file against rename/unlink.
    access = 0 if directory else (0xC0000000 if write else 0x80000000)
    disposition = 1 if exclusive else (4 if create else 3)
    flags = 0x00200000 | (0x02000000 if directory else 0x80)
    handle = api.CreateFileW(str(path), access, 3, None, disposition, flags, None)
    if handle == C.c_void_p(-1).value:
        raise C.WinError(C.get_last_error())
    try:
        info = BasicInfo()
        if not api.GetFileInformationByHandleEx(handle, 0, C.byref(info), C.sizeof(info)):
            raise C.WinError(C.get_last_error())
        if (info.FileAttributes & 0x400 or bool(info.FileAttributes & 0x10) != directory
                or api.GetFileType(handle) != 1):
            raise BridgeError("UNSAFE_CONTEXT_PATH", "Context paths must not be reparse points")
        return handle
    except BaseException:
        api.CloseHandle(handle)
        raise


def open_file(path, *, write=False, exclusive=False):
    import msvcrt

    handle = open_handle(path, write=write, exclusive=exclusive)
    try:
        return msvcrt.open_osfhandle(handle, (os.O_RDWR if write else os.O_RDONLY) | os.O_BINARY)
    except BaseException:
        kernel().CloseHandle(handle)
        raise


def read_admin_file(path, limit):
    """Require an NTFS DACL whose write grants are limited to trusted principals."""
    import msvcrt

    import win32api
    import win32con
    import win32security

    fd = open_file(path)
    with os.fdopen(fd, "rb") as stream:
        handle = msvcrt.get_osfhandle(stream.fileno())
        security = _security_call(win32security.GetSecurityInfo,
            handle, win32security.SE_FILE_OBJECT,
            win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION)
        dacl = security.GetSecurityDescriptorDacl()
        if dacl is None:
            raise ValueError("Plugin configuration requires an explicit DACL")
        token = _security_call(win32security.OpenProcessToken,
                               win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
        try:
            user = _security_call(win32security.GetTokenInformation,
                                  token, win32security.TokenUser)[0]
        finally:
            token.Close()
        allowed = {win32security.ConvertSidToStringSid(user),
                   win32security.ConvertSidToStringSid(security.GetSecurityDescriptorOwner()),
                   "S-1-5-18", "S-1-5-32-544"}  # SYSTEM and Administrators
        write_mask = 0x40000000 | 0x10000000 | 0x000D0116
        for index in range(dacl.GetAceCount()):
            ace = dacl.GetAce(index)
            kind = ace[0][0]
            if kind == win32security.ACCESS_DENIED_ACE_TYPE:
                continue
            if kind != win32security.ACCESS_ALLOWED_ACE_TYPE:
                raise ValueError("Unsupported plugin configuration DACL entry")
            if ace[1] & write_mask and win32security.ConvertSidToStringSid(ace[2]) not in allowed:
                raise ValueError("Plugin configuration is writable by untrusted principals")
        if os.fstat(stream.fileno()).st_size > limit:
            raise ValueError("Plugin configuration is too large")
        return stream.read(limit + 1)


def _security_call(function, *args):
    import pywintypes

    try:
        return function(*args)
    except pywintypes.error:
        raise ValueError("Cannot inspect plugin configuration security") from None


@contextmanager
def locked_directory(workspace, directory, *, create=False):
    api, handles = kernel(), []
    try:
        handles.append(open_handle(workspace, directory=True))
        if not directory.exists() and not directory.is_symlink():
            if not create:
                yield None
                return
            directory.mkdir(exist_ok=True)
        handles.append(open_handle(directory, directory=True))
        lock = open_handle(directory / ".context.lock", write=True, create=True)
        handles.append(lock)
        operation = Overlapped()
        if not api.LockFileEx(lock, 2, 0, 1, 0, C.byref(operation)):
            raise C.WinError(C.get_last_error())
        yield directory
    except OSError as error:
        raise BridgeError("CONTEXT_STORAGE_ERROR", "Personal context storage is unavailable or unsafe") from error
    finally:
        for handle in reversed(handles):
            api.CloseHandle(handle)
