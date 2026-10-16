"""Offline maintenance only. Manage.ps1 verifies the backend has stopped first."""
import argparse
import ctypes
import json
import socket
import struct
from pathlib import Path
from urllib.parse import urlparse
from gateway import InstanceLock, Journal, utc


def listening_ports():
    """Read Windows' IPv4/IPv6 listener tables, not a timeout-based network guess.

    GetExtendedTcpTable, TCP_TABLE_OWNER_PID_LISTENER=3. ROW sizes/layouts
    are the documented MIB_TCPROW_OWNER_PID / MIB_TCP6ROW_OWNER_PID.
    Fail closed on any OS enumeration error.
    """
    from ctypes import wintypes
    query = ctypes.WinDLL('iphlpapi').GetExtendedTcpTable
    query.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL,
                      wintypes.ULONG, ctypes.c_int, wintypes.ULONG]
    query.restype = wintypes.DWORD
    ports = set()
    for family, row_size, port_offset in [(socket.AF_INET, 24, 8), (socket.AF_INET6, 56, 20)]:
        size = wintypes.DWORD(0)
        error = query(None, ctypes.byref(size), False, family, 3, 0)
        if error not in (0, 122):
            raise ctypes.WinError(error)
        for attempt in range(5):
            buffer = ctypes.create_string_buffer(max(size.value, 4))
            error = query(buffer, ctypes.byref(size), False, family, 3, 0)
            if error == 122:
                continue
            if error:
                raise ctypes.WinError(error)
            count = struct.unpack_from('<I', buffer, 0)[0]
            if 4 + count * row_size > len(buffer):
                raise RuntimeError('Unexpected Windows TCP table layout')
            for index in range(count):
                value = struct.unpack_from('<I', buffer, 4 + index * row_size + port_offset)[0]
                ports.add(socket.ntohs(value & 0xffff))
            break
        else:
            raise RuntimeError('Windows TCP table changed too frequently')
    return ports


def clear_after_backend_stop(config):
    values = json.loads(Path(config).read_text(encoding="utf-8-sig"))
    url = urlparse(values["backend_url"])
    if (url.port or 11434) in listening_ports():
        raise RuntimeError("Backend is still listening; refusing to reset")
    directory = Path(values["data_dir"])
    directory.mkdir(parents=True, exist_ok=True)
    lock = InstanceLock(directory)
    try:
        journal = Journal(directory)
        journal.db.execute("UPDATE requests SET state='aborted', ended_at=?, reason='backend_stopped_by_administrator' WHERE state IN ('running','unknown','queued')", (utc(),))
        journal.db.commit()
        journal.close()
    finally:
        lock.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--backend-stopped", action="store_true", required=True)
    clear_after_backend_stop(parser.parse_args().config)
