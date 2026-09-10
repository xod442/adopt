"""Shared test fixtures: run the mock Mist server + several mock switch
servers as real uvicorn instances in background threads, so tests exercise
the actual HTTP/pyaoscx integration path rather than mocking it away.
"""
from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mock"))

# Must happen before any `app.*` module is imported anywhere (including by
# test modules at collection time) — app/config.py and app/db.py read
# ADOPT_DB_PATH at import time via a module-level create_engine() call.
_TEST_DB_DIR = tempfile.mkdtemp(prefix="adopt-test-db-")
os.environ["ADOPT_DB_PATH"] = str(Path(_TEST_DB_DIR) / "adopt-test.db")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _ServerHandle:
    def __init__(self, app, port: int):
        self.app = app
        self.port = port
        self.base_url = f"http://127.0.0.1:{port}"
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self):
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                return
            time.sleep(0.05)
        raise RuntimeError(f"Mock server on port {self.port} did not start in time")

    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=5)


@pytest.fixture
def mock_mist():
    import mock_mist as mist_module

    port = _free_port()
    handle = _ServerHandle(mist_module.app, port)
    handle.start()
    yield handle
    handle.stop()


@pytest.fixture
def make_mock_switch():
    handles: list[_ServerHandle] = []

    def _make(**kwargs):
        import mock_switch

        port = _free_port()
        app = mock_switch.create_app(**kwargs)
        handle = _ServerHandle(app, port)
        handle.start()
        handles.append(handle)
        return handle

    yield _make

    for h in handles:
        h.stop()


@pytest.fixture
def make_mock_ssh_switch():
    """Real mock SSH server (paramiko server-side, not a mocked client) —
    see mock/mock_ssh_switch.py."""
    servers = []

    def _make(**kwargs):
        from mock_ssh_switch import MockSshSwitch

        server = MockSshSwitch(**kwargs)
        server.start()
        servers.append(server)
        return server

    yield _make

    for s in servers:
        s.stop()


@pytest.fixture(autouse=True)
def _fresh_schema():
    """Recreate tables per test so each test starts with an empty db."""
    from app.db import Base, engine, init_db

    init_db()
    yield
    Base.metadata.drop_all(engine)
