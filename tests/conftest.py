"""Keep the automated suite independent of local credentials and live services."""

import os
import socket
from ipaddress import ip_address

import pytest

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["SCRAPER_BACKEND"] = "mock"
os.environ["APP_ENV"] = "test"
os.environ["MASTER_ENCRYPTION_KEY"] = "test-master-key-32-bytes-padding!"
for name in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "REDIS_URL", "QSTASH_URL",
             "QSTASH_TOKEN", "QSTASH_CURRENT_SIGNING_KEY", "QSTASH_NEXT_SIGNING_KEY"):
    os.environ[name] = ""


@pytest.fixture(autouse=True)
def no_live_network(monkeypatch):
    def guarded(original):
        def connect(sock, address, *args, **kwargs):
            # Windows asyncio implements its internal wake-up socketpair over
            # loopback. Keep that local IPC working while rejecting remote IO.
            if isinstance(address, tuple):
                try:
                    if ip_address(address[0]).is_loopback:
                        return original(sock, address, *args, **kwargs)
                except ValueError:
                    pass
            elif sock.family == getattr(socket, "AF_UNIX", None):
                return original(sock, address, *args, **kwargs)
            raise AssertionError("Mock external services in automated tests; remote sockets are disabled")
        return connect

    monkeypatch.setattr(socket.socket, "connect", guarded(socket.socket.connect))
    monkeypatch.setattr(socket.socket, "connect_ex", guarded(socket.socket.connect_ex))
