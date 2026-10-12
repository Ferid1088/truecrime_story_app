"""Test-only outbound network denial with loopback preserved for local services."""

from __future__ import annotations

import socket


class OutboundNetworkDenied(ConnectionError):
    """Raised when a hermetic test attempts to leave the local machine."""


def _is_loopback(address: object) -> bool:
    host = address[0] if isinstance(address, tuple) and address else address
    return host in {"localhost", "127.0.0.1", "::1"}


def install(monkeypatch) -> None:
    original_connect = socket.socket.connect
    original_create = socket.create_connection

    def connect(sock, address):
        if not _is_loopback(address):
            raise OutboundNetworkDenied(f"outbound network denied: {address!r}")
        return original_connect(sock, address)

    def create_connection(address, *args, **kwargs):
        if not _is_loopback(address):
            raise OutboundNetworkDenied(f"outbound network denied: {address!r}")
        return original_create(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "create_connection", create_connection)
