#!/usr/bin/env python3
"""Credential-blind client for the root-owned ARL credential broker."""

from __future__ import annotations

import json
import os
import socket
import struct
from pathlib import Path
from typing import Any, Mapping

BROKER_SOCKET_ENV = "AAS_ARL_BROKER_SOCKET"
BROKER_TOKEN_ENV = "AAS_ARL_BROKER_TOKEN"
BROKER_PROXY_ENV = "AAS_ARL_COMPUTE_PROXY"
MAX_MESSAGE_BYTES = 32 * 1024 * 1024

# Consume the bearer at import time.  Exact orchestration code keeps it only in
# module memory, so ordinary subprocess environment copies cannot inherit the
# parent or per-provider capability.
_BROKER_SOCKET = os.environ.pop(BROKER_SOCKET_ENV, "")
_BROKER_TOKEN = os.environ.pop(BROKER_TOKEN_ENV, "")
_BROKER_PROXY = os.environ.pop(BROKER_PROXY_ENV, "")


class BrokerError(OSError):
    """The exact-generation credential broker rejected or lost a request."""


def broker_active(environ: Mapping[str, str] | None = None) -> bool:
    if environ is None:
        return bool(_BROKER_SOCKET and _BROKER_TOKEN)
    return bool(environ.get(BROKER_SOCKET_ENV) and environ.get(BROKER_TOKEN_ENV))


def _recv_exact(sock: socket.socket, count: int) -> bytes:
    chunks: list[bytes] = []
    remaining = count
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise BrokerError("credential broker closed an incomplete response")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def request(payload: Mapping[str, Any], *, timeout_s: int) -> dict[str, Any]:
    socket_path = _BROKER_SOCKET
    token = _BROKER_TOKEN
    if not socket_path or not token:
        raise BrokerError("credential broker capability is unavailable")
    path = Path(socket_path)
    if not path.is_absolute() or len(socket_path.encode()) > 100:
        raise BrokerError("credential broker socket path is invalid")
    message = dict(payload)
    message["token"] = token
    encoded = json.dumps(message, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_MESSAGE_BYTES:
        raise BrokerError("credential broker request is oversized")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(max(1, int(timeout_s)) + 30)
        sock.connect(socket_path)
        sock.sendall(struct.pack("!I", len(encoded)) + encoded)
        length = struct.unpack("!I", _recv_exact(sock, 4))[0]
        if length > MAX_MESSAGE_BYTES:
            raise BrokerError("credential broker response is oversized")
        response = json.loads(_recv_exact(sock, length).decode("utf-8"))
    if not isinstance(response, dict):
        raise BrokerError("credential broker returned an invalid response")
    if not response.get("ok"):
        raise BrokerError(str(response.get("error") or "credential broker rejected request"))
    return response


__all__ = ["BROKER_PROXY_ENV", "BrokerError", "broker_active", "request"]
