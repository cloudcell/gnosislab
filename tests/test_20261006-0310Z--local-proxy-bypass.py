"""Local MCP endpoints must never traverse a system proxy.

The mcp SDK's default httpx client honors proxy environment variables
(trust_env=True). On a host with HTTP_PROXY/HTTPS_PROXY set — common on
Windows, where system proxy settings feed these — a streamable-http
connect to a same-host peer is routed through a proxy that cannot serve
localhost. The connect stalls until the channel timeout, so server
startup (which awaits connect() before binding) appears to hang.

``_http_client_for`` supplies a ``trust_env=False`` client for
same-host URLs; remote URLs keep the SDK default, where env proxies may
be genuinely required. The helper is duplicated in each server's client
adaptor — every copy is tested here.
"""

from __future__ import annotations

import pytest

import httpx2

from ml_episteme_mcp.clients.mcp_adaptor import (
    _http_client_for as episteme_client_for,
)
from ml_zetesis_mcp.clients.mcp_client import (
    _http_client_for as zetesis_client_for,
)
from ml_arete_mcp.clients.mcp_client import (
    _http_client_for as arete_client_for,
)
from ml_agora_mcp.clients.mcp_client import (
    _http_client_for as agora_client_for,
)

ALL_HELPERS = pytest.mark.parametrize(
    "helper",
    [
        episteme_client_for,
        zetesis_client_for,
        arete_client_for,
        agora_client_for,
    ],
    ids=["episteme", "zetesis", "arete", "agora"],
)

LOCAL_URLS = [
    "http://localhost:38090/mcp",
    "http://127.0.0.1:38090/mcp",
    "http://[::1]:38090/mcp",
    "https://localhost/mcp",
]

REMOTE_URLS = [
    "http://example.com:8080/mcp",
    "https://mcp.internal.example:443/",
    "http://192.168.1.10:8080/mcp",  # LAN-but-not-same-host keeps proxy support
]


@pytest.fixture(autouse=True)
def _proxy_env(monkeypatch):
    """Every test runs as if a dead system proxy were configured."""
    monkeypatch.setenv("HTTP_PROXY", "http://10.255.255.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://10.255.255.1:9")
    monkeypatch.setenv("http_proxy", "http://10.255.255.1:9")
    monkeypatch.setenv("https_proxy", "http://10.255.255.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)


@ALL_HELPERS
@pytest.mark.parametrize("url", LOCAL_URLS)
async def test_local_url_gets_proxy_free_client(helper, url):
    async with helper(url) as client:
        assert isinstance(client, httpx2.AsyncClient)
        # No proxy mounts installed from env — this is the whole fix.
        assert client._mounts == {}
        assert client._trust_env is False


@ALL_HELPERS
@pytest.mark.parametrize("url", REMOTE_URLS)
async def test_remote_url_keeps_sdk_default(helper, url):
    # None → streamable_http_client builds its own trust_env client,
    # preserving proxy support for genuinely remote peers.
    assert helper(url) is None


@ALL_HELPERS
async def test_local_client_preserves_mcp_default_timeouts(helper):
    # mcp's create_mcp_http_client: 30s connect, 300s read, 30s write/pool.
    async with helper("http://localhost:1/mcp") as client:
        t = client.timeout
        assert t.connect == 30.0
        assert t.read == 300.0
        assert t.write == 30.0
        assert t.pool == 30.0


async def test_default_client_would_have_proxied(monkeypatch):
    """Guard against httpx2 silently changing trust_env's meaning: a
    default client under proxy env must install a proxy mount."""
    async with httpx2.AsyncClient() as client:
        assert len(client._mounts) > 0


@ALL_HELPERS
async def test_localhost_lookalikes_are_remote(helper):
    assert helper("http://localhost2.example/mcp") is None
    assert helper("http://localhost.localdomain/mcp") is None


# --- CLI process probes: os.kill(pid, 0) is POSIX-only ---------------
#
# On Windows, os.kill maps to TerminateProcess for any non-console sig,
# so os.kill(pid, 0) KILLS the probed process (exit code 0). Using it as
# a liveness probe made `gnosislab start` kill its own child on the
# first health-miss and `gnosislab status` kill every managed server.
# _pid_alive must use OpenProcess/GetExitCodeProcess there instead.


def test_pid_alive_posix_semantics():
    from gnosislab.cli import _pid_alive
    import os as _os

    assert _pid_alive(None) is False
    assert _pid_alive(_os.getpid()) is True
    assert _pid_alive(2**22) is False  # pid that cannot exist


def test_pid_alive_windows_uses_openprocess(monkeypatch):
    """The nt branch must NOT reach os.kill — simulate Windows by
    patching os.name and the ctypes kernel32 handle calls."""
    import sys
    import types

    from gnosislab import cli

    calls = []
    fake_k32 = types.SimpleNamespace(
        OpenProcess=lambda *a: calls.append("open") or 1,
        GetExitCodeProcess=lambda h, ref: calls.append("exit") or True,
        CloseHandle=lambda h: calls.append("close"),
    )

    class FakeDWORD:
        def __init__(self):
            self.value = 259  # STILL_ACTIVE

    fake_wintypes = types.SimpleNamespace(DWORD=FakeDWORD)
    fake_ctypes = types.SimpleNamespace(
        windll=types.SimpleNamespace(kernel32=fake_k32),
        wintypes=fake_wintypes,
        byref=lambda x: x,
    )

    monkeypatch.setattr(cli.os, "name", "nt")
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)
    monkeypatch.setitem(sys.modules, "ctypes.wintypes", fake_wintypes)

    assert cli._pid_alive(1234) is True
    assert calls == ["open", "exit", "close"]


def test_port_pid_windows_netstat_parse(monkeypatch):
    import types

    from gnosislab import cli

    netstat_out = """\
  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:38080          0.0.0.0:0              LISTENING       8473
  TCP    127.0.0.1:3808         0.0.0.0:0              LISTENING       9999
  TCP    127.0.0.1:38090        0.0.0.0:0              TIME_WAIT       42
  TCP    [::1]:38090            [::]:0                 LISTENING       555
"""
    monkeypatch.setattr(cli.os, "name", "nt")
    monkeypatch.setattr(
        cli.subprocess, "run",
        lambda *a, **k: types.SimpleNamespace(stdout=netstat_out),
    )

    assert cli._port_pid(38080) == 8473
    assert cli._port_pid(3808) == 9999      # exact port, not prefix
    assert cli._port_pid(38090) == 555      # LISTENING only, not TIME_WAIT
    assert cli._port_pid(1) is None
