from __future__ import annotations

import socket

import pytest

from no_network import OutboundNetworkDenied, install


@pytest.fixture
def no_outbound_network(monkeypatch):
    install(monkeypatch)


def test_denied_network_fails_loudly(no_outbound_network):
    with pytest.raises(OutboundNetworkDenied, match="outbound network denied"):
        socket.create_connection(("example.com", 443), timeout=0.1)
