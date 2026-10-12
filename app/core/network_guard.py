"""Optional process guard used by E2E only; local services remain reachable."""

from __future__ import annotations

import socket


class OutboundNetworkDenied(ConnectionError):
    pass


def install() -> None:
    original_connect = socket.socket.connect
    original_create = socket.create_connection

    def loopback(address: object) -> bool:
        host = address[0] if isinstance(address, tuple) and address else address
        return host in {"localhost", "127.0.0.1", "::1"}

    def connect(sock, address):
        if not loopback(address):
            raise OutboundNetworkDenied(f"outbound network denied: {address!r}")
        return original_connect(sock, address)

    def create_connection(address, *args, **kwargs):
        if not loopback(address):
            raise OutboundNetworkDenied(f"outbound network denied: {address!r}")
        return original_create(address, *args, **kwargs)

    socket.socket.connect = connect
    socket.create_connection = create_connection
