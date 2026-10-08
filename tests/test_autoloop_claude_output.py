"""Pure parser checks for native Claude result and streaming output."""

from __future__ import annotations

import json
import sys
import traceback
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
RUNTIME_DIR = Path(__file__).resolve().parents[1] / "canonical/runtime/skills/autonomous-research-loop-runtime"
sys.path.insert(0, str(RUNTIME_DIR))

from claude_output import result_envelope  # noqa: E402


class ClaudeOutputTests(unittest.TestCase):
    def setUp(self):
        self.result = {"type": "result", "is_error": False, "result": "caf\u00e9\nanswer", "modelUsage": {"native": {"inputTokens": 3}}}
        self.encoded = json.dumps(self.result, ensure_ascii=False)

    def test_legacy_compact_and_pretty_result_are_preserved(self):
        for raw in (self.encoded, json.dumps(self.result, indent=2), " \n" + self.encoded + "\n "):
            with self.subTest(raw=raw):
                self.assertEqual(result_envelope(raw), self.result)

    def test_stream_events_and_whitespace_end_in_one_result(self):
        events = [
            {"type": "system", "subtype": "init"},
            {"type": "assistant", "message": {"text": "line\u2028separator"}},
            {"type": "user", "tool_result": {"content": "observation"}},
            {"type": "rate_limit_event", "status": "allowed"},
            self.result,
        ]
        stream = "\r\n \r\n".join(json.dumps(event, ensure_ascii=False) for event in events)
        self.assertEqual(result_envelope(stream + "\r\n"), self.result)

    def test_error_result_is_returned_for_caller_validation(self):
        result = {"type": "result", "is_error": True, "errors": ["failure"]}
        self.assertEqual(result_envelope(json.dumps(result)), result)

    def test_incomplete_malformed_nonobject_and_missing_result_are_rejected(self):
        for raw in (
            "", " \n", "null", "[]", "42", '"text"', '{}', '{"type":"system"}',
            '{"type":"result"', 'not-json\n' + self.encoded,
            '[]\n' + self.encoded, '{"type":"system"}\n',
            '{"type":"system","value":NaN}\n' + self.encoded,
            '{"type":"result","value":Infinity}', self.encoded + " trailing",
        ):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(ValueError, "^invalid Claude output envelope$"):
                    result_envelope(raw)

    def test_duplicate_results_and_all_trailing_events_are_rejected(self):
        for tail in (self.encoded, '{"type":"system"}', '{}', 'null', '{"partial":'):
            with self.subTest(tail=tail):
                with self.assertRaises(ValueError):
                    result_envelope(self.encoded + "\n" + tail)

    def test_errors_do_not_include_output_or_parser_exception_context(self):
        raw = '{"private":"synthetic-secret"}\nmalformed'
        try:
            result_envelope(raw)
        except ValueError as exc:
            self.assertEqual(str(exc), "invalid Claude output envelope")
            rendered = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            self.assertNotIn("synthetic-secret", rendered)
            self.assertNotIn("JSONDecodeError", rendered)
        else:
            self.fail("malformed output was accepted")

    def test_nontext_input_is_rejected(self):
        for raw in (None, b"{}", {}, []):
            with self.subTest(kind=type(raw).__name__):
                with self.assertRaisesRegex(ValueError, "^invalid Claude output envelope$"):
                    result_envelope(raw)


if __name__ == "__main__":
    unittest.main()
