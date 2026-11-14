"""Offline tests for the open-math-os client against a loopback fake gateway.

Key-like strings and email addresses are assembled at run time, so the
repository never holds a string that looks like a real credential or address.
"""

from __future__ import annotations

import contextlib
import hashlib
import http.server
import importlib.util
import io
import json
import math
import os
import random
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import types
import unicodedata
import unittest
from pathlib import Path
from unittest import mock

try:
    import fcntl
except ImportError:  # pragma: no cover - native Windows
    fcntl = None  # type: ignore[assignment]

REPO = Path(__file__).resolve().parents[1]
RUNTIME = REPO / "canonical" / "runtime"
SKILL_RUNTIME = RUNTIME / "skills" / "open-math-os"
PANEL_DIR = RUNTIME / "skills" / "autonomous-research-loop-runtime"


def _load_client() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("open_math_os_under_test", SKILL_RUNTIME / "open_math_os.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


om = _load_client()

MODEL = "deepseek/deepseek-v4.1-flash"
PROMPT = "Count the spanning trees of the wheel W_6."
# Runs the client's main() in a child process against a loopback origin.
CHILD_MAIN = (
    "import importlib.util, sys\n"
    "spec = importlib.util.spec_from_file_location('open_math_os_child', sys.argv[1])\n"
    "module = importlib.util.module_from_spec(spec)\n"
    "spec.loader.exec_module(module)\n"
    "raise SystemExit(module.main(sys.argv[3:], _origin=sys.argv[2]))\n"
)


def model_key(fill: str = "a") -> str:
    return "nuc_" + "ak_" + fill * 24


def platform_key() -> str:
    return "nuc_" + "oc_" + "p" * 24


def address(local: str, domain: str) -> str:
    return local + "@" + domain


CATALOG = {
    "object": "list",
    "data": [
        {"id": MODEL, "supported_reasoning_efforts": ["low", "high", "max"]},
        {"id": "nvidia/nemotron-3-super-120b-a12b", "supported_reasoning_efforts": ["low", "medium"]},
        {"id": "not a valid id", "supported_reasoning_efforts": ["low"]},
    ],
}
# Shaped like the gateway's spec: a chat body is `ModelCall`, which names only `model` (the gateway
# keeps the fields OpenAI's chat completion API defines), so the spec never names
# `reasoning_effort` or `max_completion_tokens`.
OPENAPI = {
    "openapi": "3.1.0",
    "info": {"title": "gateway", "version": "0.51.4"},
    "paths": {"/v1/chat/completions": {"post": {"requestBody": {"content": {"application/json": {
        "schema": {"$ref": "#/components/schemas/ModelCall"}}}}}}, "/v1/models": {}},
    "components": {"schemas": {"Model": {"properties": {"supported_reasoning_efforts": {},
                                                        "default_reasoning_effort": {}}},
                               "ModelCall": {"properties": {"model": {"type": "string"}}}}},
}


def completion(content: str = "320", *, reasoning: str = "Use the matrix-tree theorem.",
               finish: str = "stop", total: int = 30) -> dict:
    return {
        "id": "chatcmpl-1",
        "model": MODEL,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content, "reasoning": reasoning,
                        "reasoning_details": [{"type": "reasoning.text", "text": "raw\x1b[2J"}]},
            "finish_reason": finish,
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": total - 10, "reasoning_tokens": 5, "total_tokens": total},
    }


def json_reply(status: int, payload: object, headers: dict[str, str] | None = None):
    def act(handler: http.server.BaseHTTPRequestHandler) -> None:
        body = json.dumps(payload).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("x-request-id", "rq-test0001")
        for name, value in (headers or {}).items():
            handler.send_header(name, value)
        handler.end_headers()
        handler.wfile.write(body)
    return act


def text_reply(status: int, text: str):
    def act(handler: http.server.BaseHTTPRequestHandler) -> None:
        body = text.encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "text/plain")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
    return act


def sse_reply(events: list[dict], *, done: bool = True, stall: float = 0.0):
    def act(handler: http.server.BaseHTTPRequestHandler) -> None:
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("x-request-id", "rq-stream01")
        handler.end_headers()
        handler.wfile.write(b": keep-alive\n\n")
        for event in events:
            handler.wfile.write(b"data: " + json.dumps(event).encode("utf-8") + b"\n\n")
            handler.wfile.flush()
        if stall:
            time.sleep(stall)
        if done:
            handler.wfile.write(b"data: [DONE]\n\n")
    return act


def stream_events(content: str = "320", *, finish: str = "stop") -> list[dict]:
    return [
        {"id": "c1", "model": MODEL, "choices": [{"index": 0, "delta": {"reasoning": "Matrix-tree."}}]},
        {"id": "c1", "model": MODEL, "choices": [{"index": 0, "delta": {"content": content}}]},
        {"id": "c1", "model": MODEL, "choices": [{"index": 0, "delta": {
            "reasoning_details": [{"type": "reasoning.encrypted", "data": "opaque"}]}}]},
        {"id": "c1", "model": MODEL, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
        {"id": "c1", "model": MODEL, "choices": [],
         "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}},
    ]


class _QuietServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def handle_error(self, request, client_address) -> None:  # noqa: D401 - silence aborted clients
        pass


class FakeGateway:
    """Loopback OpenAI-style gateway with a scripted queue of chat replies."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.chat: list = []
        self.catalog = CATALOG
        self.openapi = OPENAPI
        gateway = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def _record(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                gateway.requests.append({
                    "method": self.command,
                    "path": self.path,
                    "headers": {name.lower(): value for name, value in self.headers.items()},
                    "body": json.loads(body) if body else None,
                })

            def do_GET(self) -> None:
                self._record()
                if self.path == "/v1/models":
                    json_reply(200, gateway.catalog)(self)
                elif self.path == "/v1/openapi.json":
                    json_reply(200, gateway.openapi)(self)
                else:
                    json_reply(404, {"error": {"code": "not_found"}})(self)

            def do_POST(self) -> None:
                self._record()
                if self.path == "/v1/chat/completions" and gateway.chat:
                    gateway.chat.pop(0)(self)
                else:
                    json_reply(500, {"error": {"code": "unscripted"}})(self)

        self.server = _QuietServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self) -> str:
        self.thread.start()
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def chat_requests(self) -> list[dict]:
        return [request for request in self.requests if request["path"] == "/v1/chat/completions"]


@unittest.skipIf(os.name == "nt", "the loopback suite uses POSIX state permissions")
class ClientTestCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir(mode=0o700)
        self.gateway = FakeGateway()
        self.origin = self.gateway.start()
        self.addCleanup(self.gateway.stop)
        self.key = model_key()
        self.environ = {"HOME": str(self.home), "OPENMATH_API_KEY": self.key}
        self.sleeps: list[float] = []
        for patcher in (
            mock.patch.object(om, "_sleep", self.sleeps.append),
            mock.patch.object(om, "LIMITER_WAIT_S", 0.0),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    @property
    def state(self) -> Path:
        return self.home / ".local" / "state" / "ai-agents-skills" / "open-math-os"

    def run_cli(self, *argv: str, environ: dict[str, str] | None = None) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = om.main(list(argv), _origin=self.origin, _environ=self.environ if environ is None else environ)
        return code, stdout.getvalue(), stderr.getvalue()

    def ask(self, *extra: str, prompt: str = PROMPT, model: str = MODEL, json_mode: bool = True,
            environ: dict[str, str] | None = None) -> tuple[int, str, str]:
        argv = ["ask", "--model", model, "--prompt", prompt, *extra]
        if json_mode:
            argv.append("--json")
        return self.run_cli(*argv, environ=environ)

    @staticmethod
    def envelope(stdout: str) -> dict:
        return json.loads(stdout.strip().splitlines()[-1])

    def assert_no_secret(self, *streams: str) -> None:
        for stream in streams:
            self.assertNotIn(self.key, stream)


class ResponseTests(ClientTestCase):
    def test_non_stream_answer_carries_untrusted_output_and_full_provenance(self) -> None:
        self.gateway.chat.append(json_reply(200, completion()))

        code, out, err = self.ask("--no-stream")

        self.assertEqual(code, 0, err)
        envelope = self.envelope(out)
        self.assertEqual(envelope["status"], "ok")
        self.assertEqual(list(envelope)[-1], "final")
        self.assertIs(envelope["final"], True)
        self.assertEqual(envelope["output"]["trust"], "untrusted-remote-output")
        self.assertEqual(envelope["output"]["content"], "320")
        self.assertEqual(envelope["output"]["reasoning"], "Use the matrix-tree theorem.")
        self.assertEqual(envelope["output"]["reasoning_details"], [{"type": "reasoning.text", "text": "raw\x1b[2J"}])
        self.assertNotIn("\x1b", out)
        provenance = envelope["provenance"]
        self.assertEqual(provenance["provider_id"], "openmathos")
        self.assertEqual(provenance["provenance_label"], "unverified-gateway:openmathos")
        self.assertEqual(provenance["family"], "unverified")
        self.assertIs(provenance["independence_eligible"], False)
        self.assertEqual(provenance["requested_model"], MODEL)
        self.assertEqual(provenance["served_model_claimed"], MODEL)
        self.assertEqual(provenance["request_id"], "rq-test0001")
        self.assertRegex(provenance["captured_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertIsNotNone(provenance["catalog_fetched_at"])
        self.assertEqual(envelope["usage"]["total_tokens"], 30)
        request = self.gateway.chat_requests()[0]
        self.assertEqual(request["headers"]["user-agent"], om.USER_AGENT)
        self.assertRegex(om.USER_AGENT, r"^ai-agents-skills-open-math-os/\d+\.\d+\.\d+$")
        self.assertEqual(request["headers"]["authorization"], "Bearer " + self.key)
        self.assertEqual(request["body"]["model"], MODEL)
        self.assertEqual(request["body"]["reasoning_effort"], "low")
        self.assertEqual(request["body"]["max_completion_tokens"], 4096)
        self.assertIs(request["body"]["stream"], False)
        self.assertEqual(request["body"]["messages"], [{"role": "user", "content": PROMPT}])
        self.assert_no_secret(out, err)

    def test_stream_answer_keeps_reasoning_and_usage_and_strips_controls(self) -> None:
        self.gateway.chat.append(sse_reply(stream_events("A\x1b[31mB\x1b]0;title\x07C\x00D\u009bE")))

        code, out, err = self.ask()

        self.assertEqual(code, 0, err)
        envelope = self.envelope(out)
        self.assertEqual(envelope["output"]["content"], "ABCD")
        self.assertEqual(envelope["output"]["reasoning"], "Matrix-tree.")
        self.assertEqual(envelope["output"]["reasoning_details"], [{"type": "reasoning.encrypted", "data": "opaque"}])
        self.assertEqual(envelope["usage"]["total_tokens"], 20)
        self.assertEqual(envelope["finish_reason"], "stop")
        self.assertEqual(envelope["provenance"]["request_id"], "rq-stream01")
        body = self.gateway.chat_requests()[0]["body"]
        self.assertIs(body["stream"], True)
        self.assertEqual(body["stream_options"], {"include_usage": True})

    def test_text_mode_streams_sanitized_content_between_nonce_markers(self) -> None:
        self.gateway.chat.append(sse_reply(stream_events("A\x1b[31mB\x07C")))

        code, out, err = self.ask(json_mode=False)

        self.assertEqual(code, 0, err)
        self.assertNotIn("\x1b", out)
        lines = out.splitlines()
        begin = re.fullmatch(r"\[open-math-os ([0-9a-f]{8})\] begin trust=untrusted-remote-output requested_model=(.+)", lines[0])
        self.assertIsNotNone(begin)
        nonce = begin.group(1)
        self.assertEqual(lines[1], "ABC")
        self.assertEqual(lines[2], f"[open-math-os {nonce}] end trust=untrusted-remote-output status=ok")
        self.assertTrue(lines[3].startswith(f"[open-math-os {nonce}] provenance "))
        provenance = json.loads(lines[3].split(" provenance ", 1)[1])
        self.assertIs(provenance["independence_eligible"], False)
        self.assertEqual(provenance["served_model_claimed"], MODEL)

    def test_finish_reason_length_is_reported_as_truncated(self) -> None:
        self.gateway.chat.append(sse_reply(stream_events("partial proof", finish="length")))

        code, out, err = self.ask()

        self.assertEqual(code, 0, err)
        self.assertEqual(self.envelope(out)["status"], "truncated")
        self.assertIn("finish_reason=length", err)


class ErrorClassTests(ClientTestCase):
    def test_401_openai_envelope_is_auth_and_never_echoes_the_message(self) -> None:
        self.gateway.chat.append(json_reply(401, {"error": {
            "message": "Incorrect API key provided", "type": "invalid_request_error", "code": "invalid_api_key"}}))

        code, out, err = self.ask()

        self.assertEqual(code, 7)
        self.assertEqual(err.strip().splitlines()[-1], "HTTP 401 auth: invalid_api_key")
        self.assertNotIn("Incorrect API key", out + err)
        self.assertEqual(self.envelope(out)["error"]["class"], "auth")
        self.assert_no_secret(out, err)

    def test_403_cloudflare_1010_is_edge_block(self) -> None:
        self.gateway.chat.append(text_reply(403, "error code: 1010"))

        code, _out, err = self.ask()

        self.assertEqual(code, 6)
        self.assertEqual(err.strip(), "HTTP 403 edge_block: 1010")

    def test_405_is_client_bug(self) -> None:
        self.gateway.chat.append(json_reply(405, {"error": {"code": "method_not_allowed"}}))

        code, _out, err = self.ask()

        self.assertEqual(code, 12)
        self.assertEqual(err.strip(), "HTTP 405 client_bug: method_not_allowed")

    def test_400_404_and_422_are_bad_requests(self) -> None:
        for status in (400, 404, 422):
            with self.subTest(status=status):
                self.gateway.chat.append(json_reply(status, {"error": {"code": "invalid"}}))
                code, _out, err = self.ask()
                self.assertEqual(code, 11)
                self.assertIn(f"HTTP {status} bad_request:", err)

    def test_429_retry_after_is_honoured_once_then_succeeds(self) -> None:
        self.gateway.chat += [json_reply(429, {"error": {"code": "rate_limited"}}, {"Retry-After": "7"}),
                              json_reply(200, completion())]

        code, _out, err = self.ask("--no-stream")

        self.assertEqual(code, 0, err)
        self.assertEqual(self.sleeps, [7.0])
        self.assertEqual(len(self.gateway.chat_requests()), 2)

    def test_retry_after_above_300_seconds_stops_retrying(self) -> None:
        self.gateway.chat.append(json_reply(429, {"error": {"code": "rate_limited"}}, {"Retry-After": "301"}))

        code, _out, err = self.ask()

        self.assertEqual(code, 5)
        self.assertEqual(self.sleeps, [])
        self.assertEqual(len(self.gateway.chat_requests()), 1)
        self.assertIn("HTTP 429 quota: rate_limited", err)

    def test_5xx_before_the_first_byte_is_retried_with_capped_backoff(self) -> None:
        self.gateway.chat += [json_reply(503, {"error": {"code": "overloaded"}}),
                              json_reply(502, {"error": {"code": "bad_gateway"}}),
                              json_reply(200, completion())]

        code, _out, err = self.ask("--no-stream")

        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.gateway.chat_requests()), 3)
        self.assertEqual(len(self.sleeps), 2)
        self.assertTrue(0.0 <= self.sleeps[0] <= 20.0, self.sleeps)
        self.assertTrue(0.0 <= self.sleeps[1] <= 40.0, self.sleeps)

    def test_retries_stop_after_two(self) -> None:
        self.gateway.chat += [json_reply(503, {"error": {"code": "overloaded"}}) for _ in range(4)]

        code, _out, _err = self.ask()

        self.assertEqual(code, 13)
        self.assertEqual(len(self.gateway.chat_requests()), 3)

    def test_failure_after_the_first_stream_byte_is_not_retried(self) -> None:
        events = [{"id": "c1", "model": MODEL, "choices": [{"index": 0, "delta": {"content": "partial"}}]},
                  {"error": {"code": "upstream_error", "status": 502}}]
        self.gateway.chat += [sse_reply(events, done=False), json_reply(200, completion())]

        code, out, err = self.ask()

        self.assertEqual(code, 13)
        self.assertEqual(len(self.gateway.chat_requests()), 1)
        self.assertEqual(self.sleeps, [])
        envelope = self.envelope(out)
        self.assertEqual(envelope["status"], "incomplete")
        self.assertIs(envelope["output"]["partial"], True)
        self.assertEqual(envelope["output"]["content"], "partial")
        self.assertIn("transient: upstream_error", err)

    def test_stream_without_done_is_incomplete(self) -> None:
        events = [{"id": "c1", "model": MODEL, "choices": [{"index": 0, "delta": {"content": "half"}}]}]
        self.gateway.chat.append(sse_reply(events, done=False))

        code, out, err = self.ask()

        self.assertEqual(code, 13)
        self.assertIn("stream_incomplete", err)
        self.assertEqual(self.envelope(out)["output"]["content"], "half")

    def test_idle_stream_times_out(self) -> None:
        events = [{"id": "c1", "model": MODEL, "choices": [{"index": 0, "delta": {"content": "slow"}}]}]
        self.gateway.chat.append(sse_reply(events, stall=1.5))

        with mock.patch.object(om, "STREAM_IDLE_TIMEOUT_S", 0.3):
            code, _out, err = self.ask()

        self.assertEqual(code, 13)
        self.assertIn("idle_timeout", err)
        self.assertEqual(len(self.gateway.chat_requests()), 1)

    def test_orchestrator_labels_never_retry(self) -> None:
        self.gateway.chat += [json_reply(503, {"error": {"code": "overloaded"}}), json_reply(200, completion())]

        code, _out, _err = self.ask("--label", "agd:run-1:breaker")

        self.assertEqual(code, 13)
        self.assertEqual(len(self.gateway.chat_requests()), 1)
        self.assertEqual(self.sleeps, [])

    def test_the_runner_arl_flag_never_retries(self) -> None:
        self.gateway.chat += [json_reply(503, {"error": {"code": "overloaded"}}), json_reply(200, completion())]

        code, _out, _err = self.ask(environ={**self.environ, "AAS_OPENMATH_ARL_LAUNCH": "1"})

        self.assertEqual(code, 13)
        self.assertEqual(len(self.gateway.chat_requests()), 1)
        self.assertEqual(self.sleeps, [])

    def test_deadline_stops_a_peer_that_trickles_inside_one_line(self) -> None:
        stop = threading.Event()
        self.addCleanup(stop.set)

        def trickle(stream: bool):
            def act(handler: http.server.BaseHTTPRequestHandler) -> None:
                handler.send_response(200)
                if stream:
                    handler.send_header("Content-Type", "text/event-stream")
                else:
                    handler.send_header("Content-Type", "application/json")
                    handler.send_header("Content-Length", "100000")
                handler.end_headers()
                ends = time.monotonic() + 12.0  # bounds the hang if the deadline regresses
                with contextlib.suppress(OSError):
                    handler.wfile.write(b"data: {" if stream else b"{")
                    while not stop.is_set() and time.monotonic() < ends:
                        handler.wfile.write(b"x")  # never a newline, and never the full body
                        handler.wfile.flush()
                        time.sleep(0.05)
            return act

        client = om.GatewayClient(self.origin)
        for stream in (True, False):
            with self.subTest(stream=stream):
                self.gateway.chat.append(trickle(stream))
                body = {"model": MODEL, "messages": [{"role": "user", "content": PROMPT}],
                        "reasoning_effort": "low", "max_completion_tokens": 16, "stream": stream}
                started = time.monotonic()
                with self.assertRaises(om.OpenMathError) as caught:
                    om.send_chat_once(client, self.key, body, deadline_s=1.0, sink=None)
                self.assertEqual(caught.exception.code, "deadline_exceeded")
                self.assertLess(time.monotonic() - started, 6.0)

    def test_redirect_is_refused_and_not_followed(self) -> None:
        target = self.origin + "/v1/elsewhere"

        def redirect(handler: http.server.BaseHTTPRequestHandler) -> None:
            handler.send_response(302)
            handler.send_header("Location", target)
            handler.send_header("Content-Length", "0")
            handler.end_headers()

        self.gateway.chat.append(redirect)

        code, _out, err = self.ask()

        self.assertEqual(code, 12)
        self.assertEqual(err.strip(), "HTTP 302 client_bug: redirect_refused")
        self.assertNotIn("/v1/elsewhere", [request["path"] for request in self.gateway.requests])

    def test_three_rate_limits_open_the_machine_wide_breaker(self) -> None:
        self.gateway.chat += [json_reply(429, {"error": {"code": "rate_limited"}}, {"Retry-After": "0"})
                              for _ in range(3)]

        code, _out, err = self.ask()

        self.assertEqual(code, 5)
        self.assertEqual(len(self.gateway.chat_requests()), 3)
        self.assertIn("circuit breaker opened", err)
        code, _out, err = self.ask()
        self.assertEqual(code, 5)
        self.assertIn("refused quota: breaker_open", err)
        self.assertEqual(len(self.gateway.chat_requests()), 3)
        later = time.time() + om.BREAKER_OPEN_S + 1
        self.gateway.chat.append(json_reply(200, completion()))
        with mock.patch.object(om, "_wall", lambda: later):
            code, _out, err = self.ask("--no-stream")
        self.assertEqual(code, 0, err)

    def test_402_writes_a_hard_quota_marker_every_process_obeys(self) -> None:
        self.gateway.chat.append(json_reply(402, {"error": {"code": "insufficient_quota"}}))

        code, _out, err = self.ask()

        self.assertEqual(code, 18)
        self.assertIn("HTTP 402 hard_quota: insufficient_quota", err)
        marker = self.state / "hard_quota.json"
        self.assertTrue(marker.is_file())
        code, _out, err = self.ask()
        self.assertEqual(code, 18)
        self.assertIn("refused hard_quota: marker_present", err)
        self.assertEqual(len(self.gateway.chat_requests()), 1)
        marker.unlink()
        self.gateway.chat.append(json_reply(200, completion()))
        code, _out, err = self.ask("--no-stream")
        self.assertEqual(code, 0, err)


class RequestValidationTests(ClientTestCase):
    def test_unsupported_effort_is_refused_before_any_chat_request(self) -> None:
        code, _out, err = self.ask("--effort", "medium")

        self.assertEqual(code, 2)
        self.assertIn("effort_not_supported", err)
        self.assertEqual(self.gateway.chat_requests(), [])

    def test_model_id_must_match_the_rule_and_the_catalog(self) -> None:
        code, _out, err = self.ask(model="../etc/passwd")
        self.assertEqual(code, 2)
        self.assertIn("model_id_invalid", err)
        self.assertEqual(self.gateway.requests, [])

        code, _out, err = self.ask(model="vendor/unknown-model")
        self.assertEqual(code, 2)
        self.assertIn("model_not_in_catalog", err)
        self.assertEqual(self.gateway.chat_requests(), [])

    def test_max_completion_tokens_is_always_sent_from_the_effort_table(self) -> None:
        self.gateway.chat += [json_reply(200, completion()), json_reply(200, completion())]

        self.assertEqual(self.ask("--effort", "high", "--no-stream")[0], 0)
        self.assertEqual(self.ask("--max-tokens", "100", "--no-stream")[0], 0)
        code, _out, err = self.ask("--max-tokens", "40000")

        bodies = [request["body"] for request in self.gateway.chat_requests()]
        self.assertEqual([body["max_completion_tokens"] for body in bodies], [12288, 100])
        self.assertEqual(code, 2)
        self.assertIn("max_tokens_out_of_range", err)
        self.assertEqual(om.EFFORT_MAX_COMPLETION_TOKENS,
                         {"low": 4096, "medium": 12288, "high": 12288, "xhigh": 32768, "max": 32768})

    def test_only_the_fixed_origin_or_a_loopback_test_origin_is_accepted(self) -> None:
        self.assertEqual(om.validated_origin(om.API_ORIGIN), "https://os.openmathmodel.org")
        for origin in ("https://evil.example", "http://example.org:8080", "http://127.0.0.1",
                       "https://os.openmathmodel.org/v2", "http://user@127.0.0.1:8080"):
            with self.subTest(origin=origin), self.assertRaises(om.OpenMathError):
                om.validated_origin(origin)
        handlers = om.build_opener(om.API_ORIGIN).handlers
        self.assertFalse(any(isinstance(handler, om.urllib.request.HTTPHandler) for handler in handlers))
        self.assertFalse(any(isinstance(handler, (om.urllib.request.HTTPRedirectHandler,
                                                  om.urllib.request.HTTPErrorProcessor)) for handler in handlers))

    def test_ask_refuses_a_platform_key_in_either_slot(self) -> None:
        for environ, code_name in (
            ({**self.environ, "OPENMATH_PLATFORM_KEY": platform_key()}, "platform_key_present"),
            ({"HOME": str(self.home), "OPENMATH_API_KEY": platform_key()}, "key_platform_key_in_model_slot"),
        ):
            with self.subTest(code=code_name):
                code, out, err = self.ask(environ=environ)
                self.assertEqual(code, 7)
                self.assertIn(f"refused auth: {code_name}", err)
                self.assertNotIn(platform_key(), out + err)
        self.assertEqual(self.gateway.requests, [])

    def test_proxy_environment_is_ignored(self) -> None:
        self.gateway.chat.append(json_reply(200, completion()))
        dead = "http://127.0.0.1:9"
        with mock.patch.dict(os.environ, {"HTTP_PROXY": dead, "http_proxy": dead, "HTTPS_PROXY": dead,
                                          "https_proxy": dead, "ALL_PROXY": dead, "NO_PROXY": ""}):
            code, _out, err = self.ask("--no-stream")

        self.assertEqual(code, 0, err)


class OutboundScreenTests(ClientTestCase):
    def test_secret_patterns_block_before_any_network_io(self) -> None:
        cases = {
            "content:github-token": "token " + "gh" + "p_" + "Z9" * 18,
            "content:sk-key": "key " + "sk" + "-proj-" + "q" * 30,
            "content:openmath-key": "key " + "nuc_" + "oc_" + "w" * 20,
            "content:authorization-header": "Authorization: " + "Bearer xyz",
            "content:private-key-block": "-----BEGIN " + "OPENSSH PRIVATE KEY-----",
            "content:github-pat": "github" + "_pat_" + "a" * 30,
        }
        for label, prompt in cases.items():
            with self.subTest(label=label):
                code, out, err = self.ask(prompt=prompt)
                self.assertEqual(code, 15)
                self.assertTrue(err.startswith("refused blocked:"), err)
                self.assertIn(label, err)
                self.assertNotIn(prompt, out + err)
                self.assertEqual(self.envelope(out)["status"], "blocked")
        self.assertEqual(self.gateway.requests, [])

    def test_credential_names_in_json_repr_env_and_netrc_forms_are_blocked(self) -> None:
        # Under run_skill.sh none of these values is in the environment, so only
        # the content patterns can find them. The values are random at run time.
        store = {name: secrets.token_urlsafe(18) for name in (
            "ZOTERO_API_KEY", "SEMANTIC_SCHOLAR_API_KEY", "WEBDAV_PASSWORD", "HCLOUD_TOKEN", "TELEGRAM_BOT_TOKEN",
            "MODAL_TOKEN_SECRET", "KAGGLE_KEY", "OCRSPACE_KEY", "SMTP_PGP_PASSPHRASE")}
        store["GDRIVE_CREDENTIALS"] = str(self.root / "gdrive" / "service-account.json")
        extended = {f"JSON entry {name}": json.dumps({name: value}) for name, value in store.items()}
        extended.update({
            "JSON store": json.dumps(store),
            "JSON store, indent=2": json.dumps(store, indent=2) + "\n",
            "JSON store, compact": json.dumps(store, separators=(",", ":")),
            "JSON, space before the colon": "\n".join(f'"{name}" : "{value}"' for name, value in store.items()),
            "Python dict repr": repr(store),
            "Python dict literal": "cfg = {'api_key': '" + store["ZOTERO_API_KEY"] + "', 'password': '"
                                   + store["WEBDAV_PASSWORD"] + "'}",
            "OAuth client JSON": json.dumps({"type": "authorized_user", "client_secret": store["HCLOUD_TOKEN"],
                                             "refresh_token": store["MODAL_TOKEN_SECRET"]}, indent=2),
            "MCP server env block": json.dumps({"mcpServers": {"zotero": {
                "command": "uvx", "env": {"ZOTERO_API_KEY": store["ZOTERO_API_KEY"]}}}}, indent=2),
            "env line KAGGLE_KEY": "KAGGLE_KEY=" + store["KAGGLE_KEY"],
            "env line SMTP_PGP_PASSPHRASE": "SMTP_PGP_PASSPHRASE=" + store["SMTP_PGP_PASSPHRASE"],
            "env line AWS_SECRET_ACCESS_KEY": "AWS_SECRET_ACCESS_KEY=" + store["HCLOUD_TOKEN"],
        })
        netrc = {
            ".netrc line": "machine api.example.org login reader password " + store["WEBDAV_PASSWORD"],
            ".netrc entry over three lines": "machine api.example.org\n  login reader\n  password "
                                             + store["WEBDAV_PASSWORD"] + "\n",
        }
        for label, cases in (("content:credential-assignment-extended", extended), ("content:netrc-credential", netrc)):
            for form, prompt in cases.items():
                with self.subTest(form=form):
                    code, out, err = self.ask(prompt=prompt)
                    self.assertEqual(code, 15, err)
                    self.assertIn(label, err)
                    self.assertEqual(self.envelope(out)["status"], "blocked")
                    for value in store.values():
                        self.assertNotIn(value, out + err)
        self.assertEqual(self.gateway.requests, [])

    def test_escaped_subscript_header_flag_markdown_and_netrc_variants_are_blocked(self) -> None:
        # Forms from notebooks, transcripts, code, shell history and Markdown. The
        # values are random at run time and never in the environment.
        value = secrets.token_hex(16)
        token = secrets.token_hex(24)
        notebook = {"cells": [{"cell_type": "code", "source": [
            "from pyzotero import zotero\n", 'zot = zotero.Zotero("1234567", "user", api_key="' + value + '")\n']}]}
        cases = {
            "content:credential-assignment-extended": {
                "notebook cell, api_key=\\\"...\\\"": json.dumps(notebook, indent=1),
                "notebook cell, subscript": json.dumps({"source": ['os.environ["HCLOUD_TOKEN"] = "' + value + '"\n']}),
                "store held in a JSON string": json.dumps(json.dumps({"ZOTERO_API_KEY": value})),
                "store nested in two JSON strings": json.dumps(json.dumps(json.dumps({"ZOTERO_API_KEY": value}))),
                "transcript tool_result": json.dumps({"type": "tool_result",
                                                      "content": json.dumps({"WEBDAV_PASSWORD": value}, indent=2)}),
                "subscript assignment": 'os.environ["ZOTERO_API_KEY"] = "' + value + '"\n',
                "subscript, single quotes": "config['password'] = '" + value + "'\n",
                "JSON Authorization: Bearer": json.dumps({"Authorization": "Bearer " + token}),
                "headers repr": repr({"Accept": "*/*", "Authorization": "Bearer " + token}),
                "JSON Authorization: Basic": json.dumps({"Authorization": "Basic " + value}),
                "header Authorization: Token": "Authorization: Token " + token + "\n",
                "PHP =>": "return ['db' => ['password' => '" + value + "']];\n",
                "Ruby =>": 'config = { "api_key" => "' + value + '" }\n',
                "numbered name": "HCLOUD_TOKEN_2=" + token + "\n",
                "Markdown list with code spans": "- `ZOTERO_API_KEY`: `" + value + "`\n",
                "Markdown code span value": "ZOTERO_API_KEY: `" + value + "`\n",
                "Markdown bold label": "**Password:** " + value + "\n",
                "Markdown bold API key": "**API key**: `" + value + "`\n",
            },
            "content:bearer-token": {
                "HAR name/value pair": json.dumps({"headers": [{"name": "Authorization", "value": "Bearer " + token}]}),
            },
            "content:credential-env-call": {
                "os.environ.setdefault": 'os.environ.setdefault("HCLOUD_TOKEN", "' + value + '")\n',
                "monkeypatch.setenv": "monkeypatch.setenv('ZOTERO_API_KEY', '" + value + "')\n",
            },
            "content:credential-flag": {
                "--api-key value": "zulip-send --api-key " + value + " -m hi\n",
                "--password value": "rclone sync --user zelda --password " + value + "\n",
                "--token value": "hcloud context create lab --token " + token + "\n",
            },
            "content:netrc-credential": {
                ".netrc default entry": "default login zelda password " + value + "\n",
                ".netrc entry without login": "machine dav.example.org password " + value + "\n",
                ".netrc password before login": "machine dav.example.org password " + value + " login zelda\n",
                ".netrc account before password": "machine dav.example.org login zelda account lab password "
                                                  + value + "\n",
            },
        }
        for label, forms in cases.items():
            for form, prompt in forms.items():
                with self.subTest(form=form):
                    code, out, err = self.ask(prompt=prompt)
                    self.assertEqual(code, 15, err)
                    self.assertIn(label, err)
                    self.assertEqual(self.envelope(out)["status"], "blocked")
                    self.assertNotIn(value, out + err)
                    self.assertNotIn(token, out + err)
        self.assertEqual(self.gateway.requests, [])

    def test_ordinary_text_near_credential_names_is_still_sent(self) -> None:
        prompts = (
            "The key: commutativity of the ring gives the bound.",
            "Run sorted(E, key=itemgetter(2)) and report the weight.",
            "L.sort(key=functools.cmp_to_key(compare)); count inversions.",
            "monkey: bananas_and_coconuts problem, with n sailors.",
            "Prove or disprove: if deg(u) + deg(v) >= n - 1 for nonadjacent u, v then G is Hamiltonian.",
            "Let f@g denote composition; compute (f@g)(x) for f(x) = x^2.",
            "Session 3 of the seminar: the token-sliding problem on 1234 567 vertices.",
            "Use a passphrase-free argument: credentials of the proof are the lemmas above.",
            "author: Jane-Q-Public\ntitle: Notes on chromatic polynomials\n",
            '{"author": "Erdos-Renyi", "title": "On random graphs I"}',
            "tests passed: 1234/5678 in 3.2s\n",
            "tokens_per_second: 12345678\n",
            "the secretary_problem: 1/e-optimal stopping\n",
            '{"passes": "multi-pass-sweep", "seed": 7}',
            '{"tokens": "subword-units", "n": 3}',
            "Change the default password before the workshop.\n",
            "A .netrc entry names a machine, a login and a password.\n",
            "  token := Nat.succ_le_of_lt h\n",
            "gpg --key-file notes.asc --verify notes.sig\n",
            'hcloud context create lab --token "$HCLOUD_TOKEN"\n',
            "zulip-send --api-key <your key> -m hi\n",
            "The bearer of the hypothesis is the induction step.\n",
            "**Token sliding:** reconfiguration of independent sets on trees.\n",
            "Run pass1 = sweep(graph) before pass2 = sweep(graph).\n",
        )
        self.gateway.chat += [json_reply(200, completion()) for _ in prompts]

        for prompt in prompts:
            with self.subTest(prompt=prompt):
                code, _out, err = self.ask("--no-stream", prompt=prompt)
                self.assertEqual(code, 0, err)

        sent = [request["body"]["messages"][-1]["content"] for request in self.gateway.chat_requests()]
        self.assertEqual(sent, list(prompts))

    def test_loaded_openmath_values_are_matched_exactly(self) -> None:
        environ = {**self.environ, "OPENMATH_TEAM_HINT": "plugh-xyzzy-42"}

        code, _out, err = self.ask(prompt="see plugh-xyzzy-42", environ=environ)
        self.assertEqual(code, 15)
        self.assertIn("environment:OPENMATH_TEAM_HINT", err)

        code, _out, err = self.ask(prompt="key " + self.key)
        self.assertEqual(code, 15)
        self.assertIn("environment:OPENMATH_API_KEY", err)
        self.assert_no_secret(err)
        self.assertEqual(self.gateway.requests, [])

    def test_the_model_key_is_found_hard_wrapped_split_or_recased(self) -> None:
        key = "nuc_" + "ak_" + secrets.token_hex(16)
        environ = {**self.environ, "OPENMATH_API_KEY": key}
        variants = {
            "hard-wrapped": ("--prompt", "Use " + key[:20] + "\n" + key[20:] + " for the call."),
            "zero-width space": ("--prompt", "Use " + key[:7] + "\u200b" + key[7:]),
            "upper-cased": ("--prompt", "Use " + key.upper()),
            "split between system and user": ("--system", "Context: " + key[:15], "--prompt", key[15:] + " Explain."),
        }
        for variant, flags in variants.items():
            with self.subTest(variant=variant):
                code, out, err = self.run_cli("ask", "--model", MODEL, *flags, "--json", environ=environ)
                self.assertEqual(code, 15, err)
                # Only the canonical copy finds these: no key-shape pattern fires.
                self.assertEqual(err.strip(), "refused blocked: environment:OPENMATH_API_KEY")
                self.assertNotIn(key[7:], out)
        self.assertEqual(self.gateway.requests, [])

    def test_email_addresses_are_blocked_by_pattern_and_by_email_bearing_values(self) -> None:
        code, _out, err = self.ask(prompt="write to " + address("ada", "example.org"))
        self.assertEqual(code, 15)
        self.assertIn("pii:email", err)

        environ = {**self.environ, "UNPAYWALL_EMAIL": address("duc.h", "uni.example.edu")}
        code, _out, err = self.ask(prompt="cc " + address("DUC.H", "UNI.EXAMPLE.EDU"), environ=environ)
        self.assertEqual(code, 15)
        self.assertIn("environment:UNPAYWALL_EMAIL", err)

        unicode_address = address("người", "trường.vn")
        environ = {**self.environ, "SMTP_FROM": "Duc <" + unicode_address + ">"}
        self.assertEqual(om.PANEL_EMAIL_PATTERN.search(unicode_address), None)
        code, _out, err = self.ask(prompt="from " + unicode_address, environ=environ)
        self.assertEqual(code, 15)
        self.assertIn("environment:SMTP_FROM", err)
        self.assertNotIn("pii:email", err)
        self.assertEqual(self.gateway.requests, [])

    def test_email_bearing_values_from_the_runner_list_are_matched(self) -> None:
        unicode_address = address("người", "trường.vn")
        carried = "\n".join(["SMTP_FROM=Duc <" + unicode_address + ">",
                             "OTHER_NAME=" + address("x", "y.example"), ""])
        environ = {**self.environ, "AAS_OPENMATH_SCREEN_VALUES": carried}

        self.assertEqual(om.email_bearing_values(environ), [("SMTP_FROM", "Duc <" + unicode_address + ">")])
        code, out, err = self.ask(prompt="from " + unicode_address, environ=environ)

        self.assertEqual(code, 15)
        self.assertEqual(err.strip(), "refused blocked: environment:SMTP_FROM")
        self.assertNotIn(unicode_address, out + err)
        self.assertEqual(self.gateway.requests, [])

    def test_carried_email_values_are_matched_on_the_normalized_copy(self) -> None:
        # The pattern cannot match this address (non-ASCII domain), so only the
        # carried value, checked on the normalized copy, blocks these forms.
        unicode_address = address("người", "trường.example.edu")
        environ = {**self.environ, "SMTP_FROM": "Duc <" + unicode_address + ">"}
        local, domain = unicode_address.split("@")
        forms = {
            "fullwidth at sign": local + "\uff20" + domain,
            "zero-width space before the at sign": local + "\u200b" + "@" + domain,
            "HTML entity": local + "&#64;" + domain,
            "upper case with a fullwidth at sign": (local + "\uff20" + domain).upper(),
            "decomposed (NFD)": unicodedata.normalize("NFD", unicode_address),
        }
        for form, text in forms.items():
            with self.subTest(form=form):
                code, out, err = self.ask(prompt="from " + text, environ=environ)
                self.assertEqual(code, 15, err)
                self.assertEqual(err.strip(), "refused blocked: environment:SMTP_FROM")
                self.assertNotIn(local, out + err)
        self.assertEqual(self.gateway.requests, [])

    def test_carried_email_values_are_folded_the_way_the_copy_is(self) -> None:
        # casefold decomposes U+0390 and NFKC composes it again, so a carried value folded
        # before NFKC never occurs in the copy, which is NFKC first and then casefolded.
        unicode_address = address("πΐ", "example.org")
        environ = {**self.environ, "SMTP_FROM": unicode_address}
        local, domain = unicode_address.split("@")

        code, out, err = self.ask(prompt="from " + local + "＠" + domain, environ=environ)

        self.assertEqual(code, 15, err)
        self.assertEqual(err.strip(), "refused blocked: environment:SMTP_FROM")
        self.assertEqual(self.gateway.requests, [])

    def test_email_matcher_finds_exactly_what_the_vendored_pattern_finds(self) -> None:
        # '#' stands for '@', so this file holds no address-like literal.
        generator = random.Random(20261106)
        alphabet = list("ab.Z9_%+-# \n,<") + ["ư", "K", "ſ", "ı", "é"]
        samples = ["".join(generator.choice(alphabet) for _ in range(generator.randint(0, 24))) for _ in range(4000)]
        samples += ["x" * 70 + "#y.org", "ưa#b.cd", ".a#b.cd", "a#b.cd.x#e.fg", "a##b.cd", "-#b.cd",
                    "a#b.c", "a#b.cdefg1", "a#b.cd_", "a#b-.cd", "x0+x1+x2#y.org"]
        for sample in (text.replace("#", "@") for text in samples):
            expected = [match.span() for match in om.PANEL_EMAIL_PATTERN.finditer(sample)]
            self.assertEqual([match.span() for match in om.email_matches(sample)], expected, repr(sample))

    def test_email_screen_stays_linear_on_long_unspaced_runs(self) -> None:
        total = "Simplify S = " + "+".join(f"x{index}" for index in range(16_000))
        compositions = ("f" + "@" + "g+") * 25_000

        started = time.monotonic()
        for text in (total, compositions):
            self.assertEqual(om.screen_outbound([{"role": "user", "content": text}], {}), [])
        # A quadratic search needs tens of seconds for the 106 KB sum alone.
        self.assertLess(time.monotonic() - started, 5.0)
        middle = len(total) // 2
        hidden = total[:middle] + "+" + address("ada", "example.org") + "+" + total[middle:]
        self.assertEqual(om.screen_outbound([{"role": "user", "content": hidden}], {}), ["pii:email"])

    def test_email_forms_from_papers_and_web_pages_are_blocked(self) -> None:
        local, coauthor, domain = "zelda", "link", "hyrule.example.edu"
        plain = address(local, domain)
        forms = {
            "plain": "Contact " + plain,
            "LNCS \\email{\\{a,b\\}@d}": "\\email{\\{" + local + "," + coauthor + "\\}" + "@" + domain + "}",
            "{a,b}@d": "{" + local + "," + coauthor + "}" + "@" + domain,
            "{a, b}@d": "{" + local + ", " + coauthor + "}" + "@" + domain,
            "{a, b} @ d": "{" + local + ", " + coauthor + "} " + "@" + " " + domain,
            "a @ d": local + " " + "@" + " " + domain,
            "line break after the at sign": local + "@" + "\n" + domain,
            "[at] and [dot]": local + " [at] hyrule [dot] example [dot] edu",
            "(at)": local + " (at) " + domain,
            "fullwidth at sign": local + "\uff20" + domain,
            "&#64;": local + "&#64;" + domain,
            "&#x40;": local + "&#x40;" + domain,
            "&commat;": local + "&commat;" + domain,
            "\\u0040": local + "\\u0040" + domain,
            "mailto with %40": "mailto:" + local + "%40" + domain,
            "zero-width space before the at sign": local + "\u200b" + "@" + domain,
            "NUL before the at sign": local + "\x00" + "@" + domain,
            "non-ASCII word glued before": "liênhệ" + plain,
            # Spacing and line breaks from PDF copy and hard-wrapped text.
            "two spaces around the at sign": local + "  " + "@" + "  " + domain,
            "tabs around the at sign": local + "\t" + "@" + "\t" + domain,
            "a space after the at sign only": local + "@" + " " + domain,
            "line break before '@ '": local + "\n" + "@" + " " + domain,
            "line break after ' @'": local + " " + "@" + "\n" + domain,
            "line break after ' @ '": local + " " + "@" + " \n" + domain,
            "line break after a dot in the domain": local + "@" + "hyrule.\nexample.edu",
            "one-label domain broken after its dot": local + "@" + "example.\nedu",
            "line breaks after the at sign and a dot": local + "@" + "\nhyrule.\nexample.edu",
            "spaced form broken in the domain": local + " " + "@" + " hyrule.\nexample.edu",
            "grouped, line break before the at sign": "{" + local + ", " + coauthor + "}\n" + "@" + domain,
            "line break before [at]": local + "\n[at] hyrule [dot] example [dot] edu",
            "line break after [dot]": local + " [at] hyrule [dot]\nexample [dot] edu",
            "line break before [dot]": local + " [at] hyrule\n[dot] example [dot] edu",
            "math-mode braces in LaTeX source": "$\\{$" + local + ", " + coauthor + "$\\}$" + "@" + domain,
            "CJK text glued after a one-label domain": "请发至" + address(local, "example.edu") + "联系",
            "footnote digit glued after a one-label domain": address(local, "example.edu") + "1, " + coauthor,
            "superscript footnote mark glued after the domain": address(local, "example.edu") + "\u00b9",
        }
        for form, prompt in forms.items():
            with self.subTest(form=form):
                code, out, err = self.ask(prompt=prompt)
                self.assertEqual(code, 15, err)
                self.assertEqual(err.strip(), "refused blocked: pii:email")
                self.assertNotIn(local, out + err)
        self.assertEqual(self.gateway.requests, [])

    def test_code_and_markup_around_at_signs_is_still_sent(self) -> None:
        prompts = (
            "\\begin{tabular}{l@{}r} a & b \\end{tabular}",
            "}\n@[simp] theorem foo.bar : True := trivial",
            "  exact @Nat.rec _ h0 hs n",
            "y = x @ self.weight.T",
            "    return x\n" + "@" + "functools.cache\ndef f(): pass",
            # Matrix products: the last part of the right operand is longer than
            # four letters, or a call, an index or an attribute follows it.
            "y = hidden @ self.weight.T",
            "out = hidden @ self.proj.weight",
            "A = evecs @ np.diag(evals) @ evecs.T",
            "x = Ainv @ rhs.ravel()",
            "r = ab @ df.loc[0]",
            "out = (hidden\n       @ self.weight)",
            "# computes P^-1 @ x.\nmatvec = lambda v: v",
            "  exact\n    @Nat.rec _ h0 hs n",
            "Connect with ssh alice" + "@" + "server.\nThen run the job.",
        )
        self.gateway.chat += [json_reply(200, completion()) for _ in prompts]

        for prompt in prompts:
            with self.subTest(prompt=prompt):
                code, _out, err = self.ask("--no-stream", prompt=prompt)
                self.assertEqual(code, 0, err)

        sent = [request["body"]["messages"][-1]["content"] for request in self.gateway.chat_requests()]
        self.assertEqual(sent, list(prompts))

    def test_secret_and_email_screens_stay_linear_on_1_mib_inputs(self) -> None:
        size = om.MAX_INPUT_FILE_BYTES
        token_run = ("token" * (size // 5 + 1))[:size]
        digit_run = ("31415926535897932384" * (size // 20 + 1))[:size]
        blank_run = "a" + " " * (size - 2) + "b"
        backslash_run = "token" + "\\" * (size - 6) + '"'
        flag_run = ("--a-b-c-" * (size // 8 + 1))[:size]
        label_run = "ab @ " + ("a." * (size // 2))[:size - 6] + "("

        started = time.monotonic()
        for text in (token_run, digit_run, blank_run, backslash_run, flag_run, label_run):
            self.assertEqual(om.screen_outbound([{"role": "user", "content": text}], self.environ), [])
        # A quadratic pattern needs hours for one of these runs.
        self.assertLess(time.monotonic() - started, 30.0)
        middle = size // 2
        credential = json.dumps({"KAGGLE_KEY": secrets.token_hex(16)})
        hidden = digit_run[:middle] + " " + credential + " " + digit_run[middle:]
        self.assertEqual(om.screen_outbound([{"role": "user", "content": hidden}], {}),
                         ["content:credential-assignment-extended"])
        hidden = blank_run[:middle] + "zelda [at] hyrule [dot] example [dot] edu" + blank_run[middle:]
        self.assertEqual(om.screen_outbound([{"role": "user", "content": hidden}], {}), ["pii:email"])
        hidden = digit_run[:middle] + " zelda " + "@" + "\nhyrule.\nexample.edu " + digit_run[middle:]
        self.assertEqual(om.screen_outbound([{"role": "user", "content": hidden}], {}), ["pii:email"])

    def test_other_pii_categories_are_reported_and_logged_but_not_blocked(self) -> None:
        self.gateway.chat.append(json_reply(200, completion()))
        phone = "+1 " + "555" + " 010 " + "4477"

        code, out, err = self.ask("--no-stream", prompt=f"{PROMPT} Call {phone} when done.")

        self.assertEqual(code, 0, err)
        self.assertIn("note: the prompt matches pii:phone; it is sent", err)
        self.assertNotIn(phone, out + err)
        self.assertEqual(len(self.gateway.chat_requests()), 1)
        raw = (self.state / "sends.jsonl").read_text(encoding="utf-8")
        self.assertNotIn(phone, raw)
        self.assertEqual([json.loads(line)["pii_reported"] for line in raw.splitlines()],
                         [["pii:phone"], ["pii:phone"]])

    def test_system_prompt_file_and_prompt_file_are_screened(self) -> None:
        system_file = self.root / "system.md"
        system_file.write_text("You are careful. token " + "xox" + "b-" + "1" * 20, encoding="utf-8")
        prompt_file = self.root / "prompt.md"
        prompt_file.write_text("Mail me at " + address("me", "example.net"), encoding="utf-8")

        code, _out, err = self.run_cli("ask", "--model", MODEL, "--system-file", str(system_file),
                                       "--prompt", PROMPT, "--json")
        self.assertEqual(code, 15)
        self.assertIn("content:slack-token", err)

        code, _out, err = self.run_cli("ask", "--model", MODEL, "--prompt-file", str(prompt_file), "--json")
        self.assertEqual(code, 15)
        self.assertIn("pii:email", err)
        self.assertEqual(self.gateway.requests, [])

    def test_input_files_in_credential_stores_are_refused_before_any_read(self) -> None:
        # The content passes the screen, so only the location or the protected
        # identity can refuse these files.
        marker = secrets.token_hex(12)

        def plant(path: Path) -> Path:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.write_text(json.dumps({"collection": "Graphs", "note": marker}), encoding="utf-8")
            path.chmod(0o600)
            return path

        home = self.home
        stores = [plant(home / relative) for relative in (
            ".config/ai-agents-skills/zotero-secrets.json", ".claude/secrets.json", ".claude/.credentials.json",
            ".codex/auth.json", ".openclaw/secrets.json", ".openclaw/openclaw.json", ".openclaw/credentials/oauth.json",
            ".ssh/id_ed25519", ".gnupg/pubring.kbx", ".aws/credentials", ".docker/config.json", ".kaggle/kaggle.json",
            ".netrc")]
        managed = stores[0]
        bundle = self.root / "bundle"
        bundle.mkdir()
        (bundle / "absolute.md").symlink_to(managed)
        (bundle / "relative.md").symlink_to(os.path.relpath(managed, bundle))
        (bundle / "docs").symlink_to(managed.parent, target_is_directory=True)
        (bundle / "system.md").symlink_to(home / ".claude" / "secrets.json")
        # A dotfile manager links single store files out to its own tree.
        (home / ".aws" / "config").symlink_to(plant(self.root / "dotfiles" / "aws-config"))
        (bundle / "chain.md").symlink_to(home / ".aws" / "config")
        (bundle / "aws").symlink_to(home / ".aws", target_is_directory=True)
        projected = plant(self.root / "projected" / "openmath.env")
        hard_link = self.root / "projected" / "copy.env"
        os.link(projected, hard_link)
        info = projected.stat()
        protected = {**self.environ, "AAS_PROTECTED_SECRET_FILE_IDS": f"1:1,{info.st_dev}:{info.st_ino}"}
        home_link = self.root / "home-link"
        home_link.symlink_to(home, target_is_directory=True)
        linked_home = {**self.environ, "HOME": str(home_link)}

        cases = [("--prompt-file", str(path), self.environ) for path in stores]
        cases += [
            ("--prompt-file", str(bundle / "absolute.md"), self.environ),
            ("--prompt-file", str(bundle / "relative.md"), self.environ),
            ("--prompt-file", str(bundle / "docs" / managed.name), self.environ),
            ("--system-file", str(bundle / "system.md"), self.environ),
            ("--prompt-file", str(projected), protected),
            ("--prompt-file", str(hard_link), protected),
            ("--prompt-file", str(managed), linked_home),
            ("--prompt-file", str(home / ".aws" / "config"), self.environ),
            ("--prompt-file", str(bundle / "chain.md"), self.environ),
            ("--prompt-file", str(bundle / "aws" / "config"), self.environ),
        ]
        if os.path.isfile("/proc/self/stat"):
            cases.append(("--prompt-file", "/proc/self/stat", self.environ))
        if os.path.isdir("/proc/self/fd"):
            # A descriptor path open on a store reaches it through /proc.
            descriptor = os.open(home / ".claude" / "secrets.json", os.O_RDONLY | getattr(os, "O_BINARY", 0))
            self.addCleanup(os.close, descriptor)
            cases.append(("--prompt-file", f"/proc/self/fd/{descriptor}", self.environ))
        for flag, path, environ in cases:
            with self.subTest(flag=flag, path=path):
                prompt = ("--prompt", PROMPT) if flag == "--system-file" else ()
                code, out, err = self.run_cli("ask", "--model", MODEL, flag, path, *prompt, "--json", environ=environ)
                self.assertEqual(code, 15, err)
                self.assertEqual(err.strip(), "refused blocked: input_file_is_managed_secret")
                self.assertEqual(self.envelope(out)["status"], "blocked")
                self.assertNotIn(marker, out + err)

        self.assertEqual(self.gateway.requests, [])
        raw = (self.state / "sends.jsonl").read_text(encoding="utf-8")
        self.assertNotIn(marker, raw)
        records = [json.loads(line) for line in raw.splitlines()]
        self.assertEqual([(record["status"], record["findings"]) for record in records],
                         [("blocked", ["input_file_is_managed_secret"])] * len(cases))

    def test_ordinary_prompt_files_and_links_to_them_are_still_sent(self) -> None:
        def write(path: Path, text: str, mode: int) -> Path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            path.chmod(mode)
            return path

        shared = write(self.root / "problem.md", "Count spanning trees of the wheel W_6.\n", 0o664)
        # Orchestrators write their prompt files owner-private.
        orchestrated = write(self.root / "runs" / "iter-1" / "prompt.md", "Find a counterexample.\n", 0o600)
        in_home = write(self.home / "notes" / "question.md", "Show that every tree is bipartite.\n", 0o600)
        beside_store = write(self.home / ".config" / "ai-agents-skills-notes" / "q.md", "Is K_5 planar?\n", 0o644)
        # A linked ancestor directory, as /tmp is on macOS.
        linked_tmp = self.root / "tmp"
        linked_tmp.symlink_to(write(self.root / "private" / "tmp" / "q.md", "Is K_4 planar?\n", 0o644).parent,
                              target_is_directory=True)
        bundle = self.root / "bundle"
        bundle.mkdir()
        (bundle / "absolute.md").symlink_to(shared)
        (bundle / "relative.md").symlink_to(os.path.relpath(orchestrated, bundle))
        files = [shared, orchestrated, in_home, beside_store, linked_tmp / "q.md", bundle / "absolute.md",
                 bundle / "relative.md"]
        self.gateway.chat += [json_reply(200, completion()) for _ in range(len(files) + 1)]

        for path in files:
            with self.subTest(path=path):
                code, _out, err = self.run_cli("ask", "--model", MODEL, "--prompt-file", str(path), "--no-stream",
                                               "--json")
                self.assertEqual(code, 0, err)
        code, _out, err = self.run_cli("ask", "--model", MODEL, "--system-file", str(bundle / "absolute.md"),
                                       "--prompt", PROMPT, "--no-stream", "--json")
        self.assertEqual(code, 0, err)

        sent = [request["body"]["messages"] for request in self.gateway.chat_requests()]
        self.assertEqual([messages[-1]["content"] for messages in sent[:-1]],
                         [path.read_text(encoding="utf-8") for path in files])
        self.assertEqual(sent[-1], [{"role": "system", "content": shared.read_text(encoding="utf-8")},
                                    {"role": "user", "content": PROMPT}])

    @unittest.skipUnless(os.path.isdir("/proc/self/fd"), "descriptor paths need /proc/self/fd")
    def test_here_documents_and_pipes_behind_descriptor_paths_behave_as_before(self) -> None:
        # zsh, and bash for a long one, hand a here-document to /dev/stdin as an
        # unlinked file, so the resolved path ends in " (deleted)" and names nothing.
        text = "Show that every tree is bipartite.\n"
        here_document = tempfile.TemporaryFile(dir=self.root)
        self.addCleanup(here_document.close)
        here_document.write(text.encode("utf-8"))
        here_document.flush()
        paths = [f"/proc/self/fd/{here_document.fileno()}"]
        if os.path.isdir("/dev/fd"):
            paths.append(f"/dev/fd/{here_document.fileno()}")
        self.gateway.chat += [json_reply(200, completion()) for _ in paths]

        for path in paths:
            with self.subTest(path=path):
                code, _out, err = self.run_cli("ask", "--model", MODEL, "--prompt-file", path, "--no-stream", "--json")
                self.assertEqual(code, 0, err)
        self.assertEqual([request["body"]["messages"][-1]["content"] for request in self.gateway.chat_requests()],
                         [text] * len(paths))

        # A pipe, as from `cat file |` or process substitution, is not a regular
        # file, and nothing records it as a credential store.
        read_end, write_end = os.pipe()
        self.addCleanup(os.close, read_end)
        os.write(write_end, text.encode("utf-8"))
        os.close(write_end)
        code, _out, err = self.run_cli("ask", "--model", MODEL, "--prompt-file", f"/proc/self/fd/{read_end}", "--json")
        self.assertEqual(code, 2, err)
        self.assertEqual(err.strip(), "refused usage: input_file_not_regular")
        log = self.state / "sends.jsonl"
        statuses = [json.loads(line)["status"] for line in log.read_text(encoding="utf-8").splitlines()]
        self.assertNotIn("blocked", statuses)

    def test_conversation_history_is_screened(self) -> None:
        history = [{"role": "user", "content": "first"},
                   {"role": "assistant", "content": "earlier token " + "gh" + "o_" + "H" * 30}]
        messages = om.build_messages("You are careful.", history, PROMPT)

        self.assertIn("content:github-token", om.screen_outbound(messages, {}))
        self.assertEqual(om.screen_outbound(om.build_messages("You are careful.", history[:1], PROMPT), {}), [])

    def test_ordinary_mathematics_is_not_blocked(self) -> None:
        prompts = (
            "Prove or disprove: if deg(u) + deg(v) >= n - 1 for nonadjacent u, v then G is Hamiltonian.",
            "Let f@g denote composition; compute (f@g)(x) for f(x) = x^2.",
            "Session 3 of the seminar: the token-sliding problem on 1234 567 vertices.",
        )
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                self.assertEqual(om.screen_outbound([{"role": "user", "content": prompt}], self.environ), [])

    def test_vendored_patterns_stay_in_sync_with_panel_parent(self) -> None:
        sys.path.insert(0, str(PANEL_DIR))
        self.addCleanup(sys.path.remove, str(PANEL_DIR))
        import panel_parent

        def shape(patterns):
            return [(label, pattern.pattern, pattern.flags) for label, pattern in patterns]

        self.assertEqual(shape(om.PANEL_SECRET_CONTENT_PATTERNS), shape(panel_parent.PANEL_SECRET_CONTENT_PATTERNS))
        self.assertEqual(shape(om.PANEL_PII_CONTENT_PATTERNS), shape(panel_parent.PANEL_PII_CONTENT_PATTERNS))
        self.assertEqual((om.PANEL_SECRET_ENV_NAME.pattern, om.PANEL_SECRET_ENV_NAME.flags),
                         (panel_parent.PANEL_SECRET_ENV_NAME.pattern, panel_parent.PANEL_SECRET_ENV_NAME.flags))
        self.assertIs(om.PANEL_EMAIL_PATTERN, dict(om.PANEL_PII_CONTENT_PATTERNS)["email"])
        self.assertTrue(panel_parent.PANEL_SECRET_ENV_NAME.search("OPENMATH_PLATFORM_KEY"))


class StateTests(ClientTestCase):
    def test_state_directory_and_files_are_owner_private(self) -> None:
        self.gateway.chat.append(json_reply(200, completion()))

        self.assertEqual(self.ask("--no-stream")[0], 0)

        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o700)
        for name in ("sends.jsonl", "budget.jsonl", "catalog.json", "state.lock", "limiter-slot-0.lock"):
            with self.subTest(name=name):
                self.assertEqual(stat.S_IMODE((self.state / name).stat().st_mode), 0o600)

    def test_send_log_holds_metadata_only(self) -> None:
        answer = "The unique answer is three hundred and twenty."
        self.gateway.chat.append(json_reply(200, completion(answer)))

        self.assertEqual(self.ask("--no-stream", "--label", "probe:r1:t1")[0], 0)

        raw = (self.state / "sends.jsonl").read_text(encoding="utf-8")
        self.assertNotIn(PROMPT, raw)
        self.assertNotIn(answer, raw)
        self.assertNotIn(self.key, raw)
        record = json.loads(raw.splitlines()[-1])
        messages = [{"role": "user", "content": PROMPT}]
        canonical = json.dumps(messages, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.assertEqual(record["prompt_sha256"], hashlib.sha256(canonical).hexdigest())
        self.assertEqual(record["prompt_bytes"], len(canonical))
        self.assertEqual(record["response_sha256"], hashlib.sha256(answer.encode("utf-8")).hexdigest())
        self.assertEqual(record["response_bytes"], len(answer.encode("utf-8")))
        for field, value in (("label", "probe:r1:t1"), ("model", MODEL), ("effort", "low"),
                             ("request_id", "rq-test0001"), ("status", "ok")):
            self.assertEqual(record[field], value, field)
        self.assertEqual(record["usage"]["total_tokens"], 30)
        self.assertIsInstance(record["latency_s"], float)
        self.assertRegex(record["ts"], r"Z$")

    def test_spent_tokens_survive_log_rotation_and_a_restart(self) -> None:
        self.gateway.chat += [json_reply(200, completion(total=40_000)) for _ in range(3)]
        with mock.patch.object(om, "SENDS_MAX_BYTES", 300):
            for _ in range(3):
                self.assertEqual(self.ask("--no-stream")[0], 0)

        self.assertTrue((self.state / "sends.jsonl.1").is_file())
        ledger = (self.state / "budget.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(sum(json.loads(row)["tokens"] for row in ledger), 120_000)
        self.assertFalse((self.state / "budget.jsonl.1").exists())
        # A fresh invocation stands in for a restarted client: only disk state carries over.
        with mock.patch.object(om, "DAY_MAX_TOKENS", 100_000):
            code, _out, err = self.ask()
        self.assertEqual(code, 14)
        self.assertIn("refused budget: day_max_tokens", err)
        self.assertEqual(len(self.gateway.chat_requests()), 3)

    def test_an_attempt_killed_in_flight_stays_charged_and_logged(self) -> None:
        received, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def hold(handler: http.server.BaseHTTPRequestHandler) -> None:
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            handler.end_headers()
            handler.wfile.write(b"data: " + json.dumps(stream_events("par")[1]).encode("utf-8") + b"\n\n")
            handler.wfile.flush()
            received.set()
            release.wait(60)

        self.gateway.chat.append(hold)
        child = subprocess.Popen(
            # -B: -I ignores PYTHONDONTWRITEBYTECODE, and a cache under canonical/ fails the inventory gate.
            [sys.executable, "-I", "-B", "-c", CHILD_MAIN, str(SKILL_RUNTIME / "open_math_os.py"), self.origin,
             "ask", "--model", MODEL, "--prompt", PROMPT, "--json"],
            env={"HOME": str(self.home), "OPENMATH_API_KEY": self.key, "PATH": "/usr/bin:/bin"},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            self.assertTrue(received.wait(60), "the gateway never received the request")
        finally:
            child.terminate()  # an orchestrator timeout
            child.wait(30)
        release.set()

        messages = json.dumps([{"role": "user", "content": PROMPT}], ensure_ascii=False, sort_keys=True,
                              separators=(",", ":")).encode("utf-8")
        reserved = 4096 + math.ceil(len(messages) / 4)
        rows = [json.loads(line) for line in (self.state / "budget.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([(row["phase"], row["tokens"]) for row in rows], [("reserve", reserved)])
        records = [json.loads(line) for line in (self.state / "sends.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([record["status"] for record in records], ["inflight"])
        self.assertEqual(records[0]["attempt_id"], rows[0]["attempt_id"])
        # The next invocation still counts the reservation of the killed attempt.
        with mock.patch.object(om, "DAY_MAX_TOKENS", reserved + 1):
            code, _out, err = self.ask()
        self.assertEqual(code, 14)
        self.assertIn("refused budget: day_max_tokens", err)
        self.assertEqual(len(self.gateway.chat_requests()), 1)

    def test_a_settled_attempt_is_charged_its_usage_not_its_reservation(self) -> None:
        self.gateway.chat.append(json_reply(200, completion(total=30)))

        self.assertEqual(self.ask("--no-stream")[0], 0)

        rows = [json.loads(line) for line in (self.state / "budget.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([row["phase"] for row in rows], ["reserve", "settle"])
        self.assertEqual(rows[0]["ts"], rows[1]["ts"])
        self.assertEqual(rows[0]["tokens"] + rows[1]["tokens"], 30)
        self.assertEqual(om.budget_usage(om.StateStore(self.state), time.time(), None)["day_tokens"], 30)

    def test_run_budget_is_shared_by_labels_that_name_the_same_run(self) -> None:
        self.gateway.chat += [json_reply(200, completion()) for _ in range(4)]
        with mock.patch.object(om, "RUN_MAX_REQUESTS", 2):
            self.assertEqual(self.ask("--no-stream", "--label", "probe:r1:a")[0], 0)
            self.assertEqual(self.ask("--no-stream", "--label", "probe:r1:b")[0], 0)
            code, _out, err = self.ask("--label", "probe:r1:c")
            self.assertEqual(code, 14)
            self.assertIn("run_max_requests", err)
            self.assertEqual(self.ask("--no-stream", "--label", "probe:r2:a")[0], 0)
            self.assertEqual(self.ask("--no-stream", "--label", "manual")[0], 0)

    def test_a_fresh_quota_snapshot_applies_the_remaining_floor(self) -> None:
        self.gateway.chat.append(json_reply(200, completion()))
        self.assertEqual(self.ask("--no-stream")[0], 0)
        snapshot = self.state / "quota.json"

        def write(payload: object) -> None:
            snapshot.write_text(json.dumps(payload), encoding="utf-8")
            snapshot.chmod(0o600)

        now = om._iso(time.time())
        write({"fetched_at": now, "windows": {"five_hour": {"remaining_usd": 10}, "week": {"remaining_usd": 200}}})
        code, _out, err = self.ask()
        self.assertEqual(code, 14)
        self.assertIn("quota_five_hour_below_floor", err)

        write({"fetched_at": now, "windows": {"five_hour": {"remaining_usd": "lots"}}})
        code, _out, err = self.ask()
        self.assertEqual(code, 14)
        self.assertIn("quota_snapshot_invalid", err)

        write({"fetched_at": om._iso(time.time() - 3600),
               "windows": {"five_hour": {"remaining_usd": 1}, "week": {"remaining_usd": 1}}})
        self.gateway.chat.append(json_reply(200, completion()))
        self.assertEqual(self.ask("--no-stream")[0], 0)


@unittest.skipIf(fcntl is None, "POSIX flock limiter test")
class LimiterTests(ClientTestCase):
    def _store(self) -> object:
        store = om.StateStore(self.state)
        store.ensure()
        return store

    def test_every_slot_held_refuses_without_sending(self) -> None:
        store = self._store()
        held = []
        for index in range(om.MAX_INFLIGHT_REQUESTS):
            descriptor = os.open(self.state / f"limiter-slot-{index}.lock", os.O_RDWR | os.O_CREAT, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            held.append(descriptor)
        self.addCleanup(lambda: [os.close(descriptor) for descriptor in held])
        self.assertTrue(store.exists())

        code, _out, err = self.ask()

        self.assertEqual(code, 13)
        self.assertIn("limiter_busy", err)
        self.assertEqual(self.gateway.requests, [])

    def test_a_stale_slot_left_by_a_dead_process_is_recovered(self) -> None:
        store = self._store()
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        slot = self.state / "limiter-slot-0.lock"
        slot.write_text(json.dumps({"pid": dead.pid, "started_at": "2026-11-05T00:00:00Z"}), encoding="utf-8")
        slot.chmod(0o600)

        with om.machine_slot(store, "probe", wait_s=0) as held:
            self.assertEqual(held["slot"], 0)
            self.assertIs(held["stale_recovered"], True)
            self.assertEqual(held["stale_pid"], dead.pid)
            self.assertEqual(json.loads(slot.read_text(encoding="utf-8"))["pid"], os.getpid())

        self.assertEqual(slot.read_text(encoding="utf-8"), "")

    def test_slots_are_released_after_each_request(self) -> None:
        self.gateway.chat.append(json_reply(200, completion()))

        self.assertEqual(self.ask("--no-stream")[0], 0)

        for index in range(om.MAX_INFLIGHT_REQUESTS):
            path = self.state / f"limiter-slot-{index}.lock"
            if not path.exists():
                continue
            descriptor = os.open(path, os.O_RDWR)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.assertEqual(os.read(descriptor, 64), b"")
            finally:
                os.close(descriptor)


class CommandTests(ClientTestCase):
    def _loader_runtime(self) -> str:
        runtime = self.root / "runtime"
        runtime.mkdir(mode=0o755, exist_ok=True)
        loader = runtime / "load_secret_env.py"
        shutil.copyfile(RUNTIME / "runners" / "load_secret_env.py", loader)
        loader.chmod(0o644)
        return str(runtime)

    def _claude_secrets(self, value: str, *, mode: int = 0o600) -> Path:
        directory = self.home / ".claude"
        directory.mkdir(mode=0o700, exist_ok=True)
        path = directory / "secrets.json"
        path.write_text(json.dumps({"OPENMATH_API_KEY": value, "OTHER_TOKEN": "not-read"}), encoding="utf-8")
        path.chmod(mode)
        return path

    def test_doctor_reports_key_states_without_printing_values(self) -> None:
        cases = (
            ({"HOME": str(self.home)}, 7, "missing"),
            ({"HOME": str(self.home), "OPENMATH_API_KEY": "abc" + "d" * 30}, 7, "wrong_prefix"),
            ({"HOME": str(self.home), "OPENMATH_API_KEY": platform_key()}, 7, "platform_key_in_model_slot"),
            (self.environ, 0, "present"),
        )
        for environ, expected_code, status in cases:
            with self.subTest(status=status):
                code, out, err = self.run_cli("doctor", environ=environ)
                self.assertEqual(code, expected_code, err)
                payload = json.loads(out)
                self.assertEqual(payload["key"]["status"], status)
                self.assertIs(payload["provenance"]["independence_eligible"], False)
                self.assertIs(payload["network_attempted"], False)
                for value in environ.values():
                    if value.startswith("nuc_") or value.startswith("abc"):
                        self.assertNotIn(value, out + err)

        code, out, err = self.run_cli("doctor", environ={**self.environ, "OPENMATH_PLATFORM_KEY": platform_key()})
        self.assertEqual(code, 7)
        self.assertIn("platform_key_present", err)
        self.assertNotIn(platform_key(), out + err)
        self.assertEqual(self.gateway.requests, [])

    def test_doctor_compares_the_key_fingerprint_with_its_source(self) -> None:
        def fingerprint() -> str:
            code, out, err = self.run_cli("doctor")
            self.assertEqual(code, 0, err)
            self.assert_no_secret(out, err)
            return json.loads(out)["key"]["source_fingerprint"]

        self.assertEqual(fingerprint(), "source_unavailable")
        self._claude_secrets(self.key)
        self.assertEqual(fingerprint(), "match")
        self._claude_secrets(model_key("b"))
        self.assertEqual(fingerprint(), "stale")
        self._claude_secrets(self.key, mode=0o644)
        self.assertEqual(fingerprint(), "source_not_private")

    def test_doctor_live_reads_openapi_and_catalog_without_sending_a_prompt(self) -> None:
        code, out, err = self.run_cli("doctor", "--live")

        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["live"]["status"], "ok")
        self.assertEqual(payload["live"]["openapi"]["version"], "0.51.4")
        self.assertIs(payload["live"]["models"]["fields_ok"], True)
        self.assertEqual(payload["live"]["models"]["rejected_entries"], 1)
        paths = sorted(request["path"] for request in self.gateway.requests)
        self.assertEqual(paths, ["/v1/models", "/v1/openapi.json"])
        openapi = next(request for request in self.gateway.requests if request["path"] == "/v1/openapi.json")
        self.assertNotIn("authorization", openapi["headers"])
        self.assertTrue(all(request["method"] == "GET" for request in self.gateway.requests))
        self.assert_no_secret(out, err)

    def test_doctor_live_refuses_a_spec_without_reasoning_efforts(self) -> None:
        self.gateway.openapi = {**OPENAPI, "components": {"schemas": {"ModelCall": {"properties": {"model": {}}}}}}

        code, out, err = self.run_cli("doctor", "--live")

        self.assertEqual(code, 12, err)
        payload = json.loads(out)
        self.assertEqual(payload["live"]["status"], "degraded")
        self.assertIs(payload["live"]["openapi"]["fields_ok"], False)
        self.assertEqual(payload["problem"]["code"], "api_contract_mismatch")
        self.assertTrue(all(request["method"] == "GET" for request in self.gateway.requests))

    def test_models_lists_only_valid_catalog_entries_and_uses_the_cache(self) -> None:
        code, out, err = self.run_cli("models")
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual([entry["id"] for entry in payload["models"]], [MODEL, "nvidia/nemotron-3-super-120b-a12b"])
        self.assertEqual(payload["rejected_entries"], 1)
        self.assertEqual(payload["source"], "live")
        self.assertEqual(payload["provenance"]["provenance_label"], "unverified-gateway:openmathos")

        code, out, _err = self.run_cli("models")
        self.assertEqual(json.loads(out)["source"], "cache")
        self.assertEqual(len(self.gateway.requests), 1)
        code, out, _err = self.run_cli("models", "--refresh")
        self.assertEqual(json.loads(out)["source"], "live")

    def test_setup_key_from_stdin_writes_one_owner_private_file(self) -> None:
        environ = {"HOME": str(self.home), "AAS_RUNTIME_ROOT": self._loader_runtime()}

        def setup(raw: bytes) -> tuple[int, str, str]:
            fake = types.SimpleNamespace(isatty=lambda: False, buffer=io.BytesIO(raw))
            with mock.patch.object(sys, "stdin", fake):
                return self.run_cli("setup-key", "--from-stdin", environ=environ)

        code, out, err = setup((self.key + "\n").encode("utf-8"))

        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(set(payload), {"path", "bytes"})
        path = Path(payload["path"])
        self.assertEqual(path, self.home / ".config" / "ai-agents-skills" / "openmath.env")
        self.assertEqual(path.read_text(encoding="utf-8"), f"OPENMATH_API_KEY={self.key}\n")
        self.assertEqual(payload["bytes"], len(f"OPENMATH_API_KEY={self.key}\n"))
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        self.assert_no_secret(out, err)

        code, _out, err = setup((self.key + "\n").encode("utf-8"))
        self.assertEqual(code, 16)
        self.assertIn("key_file_exists", err)
        path.unlink()
        code, _out, err = setup(platform_key().encode("utf-8"))
        self.assertEqual(code, 2)
        self.assertIn("platform_key_refused", err)
        code, _out, err = setup(("sk" + "-" + "x" * 30).encode("utf-8"))
        self.assertEqual(code, 2)
        self.assertFalse(path.exists())

    def test_setup_key_from_claude_secrets(self) -> None:
        self._claude_secrets(self.key)
        environ = {"HOME": str(self.home), "AAS_RUNTIME_ROOT": self._loader_runtime()}

        code, out, err = self.run_cli("setup-key", "--from-claude-secrets", environ=environ)

        self.assertEqual(code, 0, err)
        path = Path(json.loads(out)["path"])
        self.assertEqual(path.read_text(encoding="utf-8"), f"OPENMATH_API_KEY={self.key}\n")
        self.assert_no_secret(out, err)

    def test_log_shows_metadata_records_and_budget(self) -> None:
        self.gateway.chat.append(json_reply(200, completion()))
        self.assertEqual(self.ask("--no-stream")[0], 0)

        code, out, err = self.run_cli("log", "--limit", "5")

        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        provisional, final = payload["records"]
        self.assertEqual((provisional["status"], final["status"]), ("inflight", "ok"))
        self.assertEqual(provisional["attempt_id"], final["attempt_id"])
        self.assertIs(payload["content_logged"], False)
        self.assertEqual(payload["budget"]["day_tokens"], 30)
        self.assertNotIn(PROMPT, out)

    def test_offline_smoke_needs_no_network_and_writes_nothing(self) -> None:
        canary = "OPENMATH-SMOKE-CANARY"
        empty_home = self.root / "smoke-home"
        empty_home.mkdir(mode=0o700)

        code, out, err = self.run_cli("smoke", environ={"HOME": str(empty_home), "OPENMATH_API_KEY": canary})

        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["failed"], [])
        self.assertIn("projected_key_screened", payload["checks"])
        self.assertNotIn(canary, out + err)
        self.assertEqual(list(empty_home.rglob("*")), [])
        self.assertEqual(self.gateway.requests, [])


@unittest.skipIf(os.name == "nt", "POSIX launcher test")
@unittest.skipUnless(os.path.isfile("/usr/bin/python3"), "the managed wrapper requires the attested OS python3")
class ManagedLaunchTests(unittest.TestCase):
    def _stage(self, root: Path) -> Path:
        runtime = root / "runtime"
        skill = runtime / "workspace" / "skills" / "open-math-os"
        skill.mkdir(parents=True)
        shutil.copyfile(RUNTIME / "runners" / "run_skill.sh", runtime / "run_skill.sh")
        shutil.copyfile(RUNTIME / "runners" / "load_secret_env.py", runtime / "load_secret_env.py")
        for name in ("run_open_math_os.sh", "open_math_os.py"):
            shutil.copyfile(SKILL_RUNTIME / name, skill / name)
        (runtime / "run_skill.sh").chmod(0o755)
        (runtime / "load_secret_env.py").chmod(0o644)
        (skill / "run_open_math_os.sh").chmod(0o755)
        (skill / "open_math_os.py").chmod(0o644)
        for directory in (root, runtime, runtime / "workspace", runtime / "workspace" / "skills", skill):
            directory.chmod(0o755)
        return runtime

    def _launch(self, runtime: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/bash", str(runtime / "run_skill.sh"), "skills/open-math-os/run_open_math_os.sh", *args],
            check=False, text=True, encoding="utf-8", errors="replace", capture_output=True, env=env, timeout=60,
        )

    def test_runner_projects_only_the_model_key_into_the_real_client(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            runtime = self._stage(root)
            home = root / "home"
            home.mkdir(mode=0o700)
            key = model_key("m")
            pointer = root / "openmath.env"
            pointer.write_text(f"OPENMATH_API_KEY={key}\n", encoding="utf-8")
            pointer.chmod(0o600)
            env = {
                "HOME": str(home),
                "PATH": "/usr/bin:/bin",
                "PYTHONDONTWRITEBYTECODE": "1",
                "AAS_OPENMATH_SECRETS_FILE": str(pointer),
                "OPENMATH_PLATFORM_KEY": platform_key(),
                "UNPAYWALL_EMAIL": address("someone", "example.org"),
            }

            completed = self._launch(runtime, env, "doctor")

            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["key"]["status"], "present")
            self.assertIs(payload["platform_key_present"], False)
            self.assertNotIn(key, completed.stdout + completed.stderr)
            self.assertNotIn(platform_key(), completed.stdout + completed.stderr)

    def test_runner_smoke_screens_the_projected_canary_without_echoing_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            runtime = self._stage(root)
            home = root / "home"
            home.mkdir(mode=0o700)
            canary = "OPENMATH-SMOKE-CANARY"
            pointer = root / "canary.env"
            pointer.write_text(f"OPENMATH_API_KEY={canary}\n", encoding="utf-8")
            pointer.chmod(0o600)
            env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "AAS_OPENMATH_SECRETS_FILE": str(pointer)}

            completed = self._launch(runtime, env, "smoke")

            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["status"], "ok")
            self.assertIn("projected_key_screened", payload["checks"])
            self.assertNotIn(canary, completed.stdout + completed.stderr)
            self.assertEqual(list(home.rglob("*")), [])

    def test_runner_and_wrapper_hand_email_bearing_values_to_the_real_screen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            runtime = self._stage(root)
            home = root / "home"
            home.mkdir(mode=0o700)
            unicode_address = address("người", "trường.vn")
            env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "SMTP_FROM": "Duc <" + unicode_address + ">"}
            argv = ("ask", "--model", MODEL, "--prompt", "from " + unicode_address, "--json")
            wrapper = runtime / "workspace" / "skills" / "open-math-os" / "run_open_math_os.sh"
            launches = {
                "runner": lambda: self._launch(runtime, env, *argv),
                "wrapper": lambda: subprocess.run(
                    ["/bin/bash", str(wrapper), *argv], check=False, text=True, encoding="utf-8",
                    errors="replace", capture_output=True, env=env, timeout=60,
                ),
            }
            for name, launch in launches.items():
                with self.subTest(launch=name):
                    completed = launch()
                    self.assertEqual(completed.returncode, 15, completed.stderr + completed.stdout)
                    self.assertEqual(completed.stderr.strip(), "refused blocked: environment:SMTP_FROM")
                    self.assertNotIn(unicode_address, completed.stdout + completed.stderr)

    def test_runner_passes_only_the_screen_values_and_the_arl_flag(self) -> None:
        names = ("AAS_OPENMATH_SCREEN_VALUES", "AAS_OPENMATH_ARL_LAUNCH", "AAS_AUTOLOOP_RUN_ID",
                 "AAS_ARL_BROKER_SOCKET", "SMTP_FROM", "GIT_AUTHOR_EMAIL", "SEND_EMAIL_SECRETS_FILE")
        probe = f"import json, os\nprint(json.dumps({{name: os.environ.get(name) for name in {names!r}}}))\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            runtime = self._stage(root)
            (runtime / "workspace" / "skills" / "open-math-os" / "open_math_os.py").write_text(probe, encoding="utf-8")
            home = root / "home"
            home.mkdir(mode=0o700)
            sender = address("ada", "example.org")
            base = {"HOME": str(home), "PATH": "/usr/bin:/bin"}
            cases = {
                "run marker": ({
                    **base, "AAS_AUTOLOOP_RUN_ID": "run-1", "SMTP_FROM": "Ada <" + sender + ">",
                    "GIT_AUTHOR_EMAIL": "first\n" + sender, "SEND_EMAIL_ADDRESS_BOOK": str(root / "book.json"),
                    "SEND_EMAIL_SECRETS_FILE": str(root / ("pointer" + "@" + "dir")),
                    "AAS_OPENMATH_SCREEN_VALUES": "CALLER_EMAIL=" + sender,
                }, "1", ["GIT_AUTHOR_EMAIL=first " + sender, "SMTP_FROM=Ada <" + sender + ">"]),
                "broker socket": ({**base, "AAS_ARL_BROKER_SOCKET": str(root / "broker.sock")}, "1", None),
                "caller flag only": ({**base, "AAS_OPENMATH_ARL_LAUNCH": "1"}, None, None),
            }
            for name, (env, flag, lines) in cases.items():
                with self.subTest(case=name):
                    completed = self._launch(runtime, env, "doctor")

                    self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
                    child = json.loads(completed.stdout)
                    self.assertEqual(child["AAS_OPENMATH_ARL_LAUNCH"], flag)
                    for raw in names[2:]:
                        self.assertIsNone(child[raw], raw)
                    values = child["AAS_OPENMATH_SCREEN_VALUES"]
                    self.assertEqual(sorted(values.splitlines()) if values else None, lines)


if __name__ == "__main__":
    unittest.main()
