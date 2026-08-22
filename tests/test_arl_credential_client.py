"""Offline tests for the ARL credential client and its compute proxy.

Neither module had a test before this file.  What they own between them is the
boundary the broker hands its child: two bearer variables that must not survive
into ordinary subprocess environment copies, one proxy path that must, and the
wire framing `arl_compute_proxy` speaks to ask the broker for a compute lane.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any, Iterator

sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = (
    REPO_ROOT
    / "canonical"
    / "runtime"
    / "skills"
    / "autonomous-research-loop-runtime"
)

if os.name == "nt":
    raise unittest.SkipTest("the ARL credential broker contract is POSIX-only (AF_UNIX)")

CAPABILITY_KEYS = (
    "AAS_ARL_BROKER_SOCKET",
    "AAS_ARL_BROKER_TOKEN",
    "AAS_ARL_COMPUTE_PROXY",
)


def _child_env(**overrides: str) -> dict[str, str]:
    """A clean environment: no capability unless this call puts one there."""

    env = dict(os.environ)
    for key in CAPABILITY_KEYS:
        env.pop(key, None)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(RUNTIME_DIR), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(overrides)
    return env


def _recv_exact(sock: socket.socket, count: int) -> bytes:
    chunks: list[bytes] = []
    remaining = count
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


@contextlib.contextmanager
def _fake_broker(response: dict[str, Any]) -> Iterator[tuple[str, list[Any]]]:
    """One-shot broker speaking the client's framing: 4-byte length, then JSON."""

    received: list[Any] = []
    directory = tempfile.mkdtemp()  # short path: the client caps sun_path at 100
    path = os.path.join(directory, "b.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.listen(1)

    def serve() -> None:
        try:
            conn, _ = server.accept()
        except OSError:
            return
        with conn:
            header = _recv_exact(conn, 4)
            if len(header) < 4:
                return
            length = struct.unpack("!I", header)[0]
            received.append(json.loads(_recv_exact(conn, length).decode("utf-8")))
            encoded = json.dumps(response, separators=(",", ":")).encode("utf-8")
            conn.sendall(struct.pack("!I", len(encoded)) + encoded)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield path, received
    finally:
        server.close()
        thread.join(timeout=5)
        shutil.rmtree(directory, ignore_errors=True)


class CapabilityImportTests(unittest.TestCase):
    """What importing the client does to the environment it was handed.

    The import runs once per process and mutates os.environ, so every case here
    runs in its own interpreter rather than reloading the module in this one.
    """

    PROXY = "/opt/aas/arl_compute_proxy.py"

    REPORT = (
        "import json, os\n"
        "import arl_credential_client as c\n"
        "print(json.dumps({\n"
        "    'environ': {k: os.environ.get(k) for k in "
        "('AAS_ARL_BROKER_SOCKET', 'AAS_ARL_BROKER_TOKEN', 'AAS_ARL_COMPUTE_PROXY')},\n"
        "    'active': c.broker_active(),\n"
        "    'proxy': c.compute_proxy(),\n"
        "}))\n"
    )

    def _report(self, **overrides: str) -> dict[str, Any]:
        proc = subprocess.run(
            [sys.executable, "-c", self.REPORT],
            env=_child_env(**overrides),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def _under_broker(self) -> dict[str, Any]:
        return self._report(
            AAS_ARL_BROKER_SOCKET="/run/aas/broker.sock",
            AAS_ARL_BROKER_TOKEN="capability",
            AAS_ARL_COMPUTE_PROXY=self.PROXY,
        )

    def test_the_bearer_is_taken_out_of_the_environment(self) -> None:
        """The property the pop exists for, and the reason it is not a mistake."""

        report = self._under_broker()
        self.assertIsNone(report["environ"]["AAS_ARL_BROKER_SOCKET"])
        self.assertIsNone(report["environ"]["AAS_ARL_BROKER_TOKEN"])
        self.assertTrue(report["active"])

    def test_the_proxy_pointer_is_not(self) -> None:
        """A path is not a bearer.

        Popping it confined nothing -- running the proxy still needs the socket and
        the token, which the two lines above it had already removed -- and it left
        the value in a module global nothing read, so no caller could name the proxy
        either.  The broker put the path in the environment; it stays there.
        """

        report = self._under_broker()
        self.assertEqual(report["environ"]["AAS_ARL_COMPUTE_PROXY"], self.PROXY)
        self.assertEqual(report["proxy"], self.PROXY)

    def test_a_descendant_still_inherits_the_pointer_but_not_the_bearer(self) -> None:
        """The distinction has to hold one process further down, which is where the
        proxy is actually run from."""

        inner = (
            "import json, os; print(json.dumps({k: os.environ.get(k) for k in "
            "('AAS_ARL_BROKER_SOCKET', 'AAS_ARL_BROKER_TOKEN', "
            "'AAS_ARL_COMPUTE_PROXY')}))"
        )
        outer = (
            "import subprocess, sys\n"
            "import arl_credential_client  # the import under test\n"
            f"child = subprocess.run([sys.executable, '-c', {inner!r}],\n"
            "    capture_output=True, text=True, encoding='utf-8',\n"
            "    errors='replace', check=True)\n"
            "sys.stdout.write(child.stdout)\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", outer],
            env=_child_env(
                AAS_ARL_BROKER_SOCKET="/run/aas/broker.sock",
                AAS_ARL_BROKER_TOKEN="capability",
                AAS_ARL_COMPUTE_PROXY=self.PROXY,
            ),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        seen = json.loads(proc.stdout)
        self.assertIsNone(seen["AAS_ARL_BROKER_SOCKET"])
        self.assertIsNone(seen["AAS_ARL_BROKER_TOKEN"])
        self.assertEqual(seen["AAS_ARL_COMPUTE_PROXY"], self.PROXY)

    def test_outside_a_broker_there_is_no_capability_and_no_proxy(self) -> None:
        report = self._report()
        self.assertFalse(report["active"])
        self.assertEqual(report["proxy"], "")

    def test_half_a_capability_is_no_capability(self) -> None:
        report = self._report(AAS_ARL_BROKER_SOCKET="/run/aas/broker.sock")
        self.assertFalse(report["active"])


class ComputeProxyWireTests(unittest.TestCase):
    """arl_compute_proxy end to end against a broker that speaks the framing."""

    def _run_proxy(self, argv: list[str], *, socket_path: str | None) -> Any:
        overrides = {}
        if socket_path is not None:
            overrides = {
                "AAS_ARL_BROKER_SOCKET": socket_path,
                "AAS_ARL_BROKER_TOKEN": "capability",
            }
        return subprocess.run(
            [sys.executable, str(RUNTIME_DIR / "arl_compute_proxy.py"), *argv],
            env=_child_env(**overrides),
            cwd=str(RUNTIME_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

    def test_a_lane_request_reaches_the_broker_and_its_result_comes_back(self) -> None:
        response = {
            "ok": True,
            "stdout": "plan: modal_cpu\n",
            "stderr": "warning: oversubscribed\n",
            "returncode": 7,
        }
        with _fake_broker(response) as (socket_path, received):
            proc = self._run_proxy(
                ["modal", "--", "--plan", "wide"], socket_path=socket_path
            )
        self.assertEqual(proc.returncode, 7, proc.stderr)
        self.assertEqual(proc.stdout, "plan: modal_cpu\n")
        self.assertEqual(proc.stderr, "warning: oversubscribed\n")
        self.assertEqual(len(received), 1, received)
        self.assertEqual(
            received[0],
            {
                "operation": "compute",
                "lane": "modal",
                "arguments": ["--plan", "wide"],
                "cwd": str(RUNTIME_DIR),
                "token": "capability",
            },
        )

    def test_a_rejected_request_exits_126_without_a_traceback(self) -> None:
        with _fake_broker({"ok": False, "error": "lane hetzner is not granted"}) as (
            socket_path,
            _received,
        ):
            proc = self._run_proxy(["hetzner"], socket_path=socket_path)
        self.assertEqual(proc.returncode, 126)
        self.assertIn("lane hetzner is not granted", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_without_a_capability_it_says_so_rather_than_crashing(self) -> None:
        proc = self._run_proxy(["kaggle"], socket_path=None)
        self.assertEqual(proc.returncode, 126)
        self.assertIn("credential broker capability is unavailable", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_an_unserved_lane_is_refused_by_the_parser(self) -> None:
        proc = self._run_proxy(["gha"], socket_path=None)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("invalid choice", proc.stderr)


if __name__ == "__main__":
    unittest.main()
