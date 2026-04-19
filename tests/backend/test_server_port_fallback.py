"""AC-9 tests: port fallback cascade from 54787 → 54796 → OS-assigned.

Implementation under test lives in app/cli.py:_resolve_port which is the
pure-function seam for fallback logic — we exercise it with socket binds
rather than spawning full `locally start` (fast, deterministic, OS-agnostic).
"""
from __future__ import annotations

import socket

from app.cli import _resolve_port, _DEFAULT_PORT, _PORT_RANGE_END


def _bind(host: str, port: int) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # DO NOT set SO_REUSEADDR — we want the bind to block _port_is_free's bind.
    s.bind((host, port))
    s.listen(1)
    return s


class TestSingleStepFallback:
    def test_port_54787_occupied_uses_54788(self):
        busy = _bind("127.0.0.1", _DEFAULT_PORT)
        try:
            chosen, cascade = _resolve_port("127.0.0.1", _DEFAULT_PORT)
            assert chosen == _DEFAULT_PORT + 1
            assert len(cascade) == 1
            # Expected template per plan §4.7:
            #   ▸ 포트 54787 점유, 54788로 시도...
            assert f"포트 {_DEFAULT_PORT} 점유" in cascade[0]
            assert f"{_DEFAULT_PORT + 1}로 시도" in cascade[0]
        finally:
            busy.close()


class TestAllBusyFallsBackToOsAssigned:
    def test_all_10_occupied_uses_os_assigned(self):
        busy_sockets = []
        try:
            for p in range(_DEFAULT_PORT, _PORT_RANGE_END + 1):
                try:
                    busy_sockets.append(_bind("127.0.0.1", p))
                except OSError:
                    # Already busy on this machine — acceptable; fallback still triggers.
                    pass
            chosen, cascade = _resolve_port("127.0.0.1", _DEFAULT_PORT)
            # All 10 busy → OS assigns a port >= 1024 and < 65536.
            assert 1024 <= chosen < 65536
            # Cascade must end with the "모두 점유" line.
            assert any("모두 점유" in line for line in cascade)
        finally:
            for s in busy_sockets:
                s.close()


class TestFastPathWhenPortFree:
    def test_empty_cascade_when_port_free(self, free_port):
        chosen, cascade = _resolve_port("127.0.0.1", free_port)
        assert chosen == free_port
        assert cascade == []
