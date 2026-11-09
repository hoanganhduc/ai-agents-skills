"""What a delegation validator does with input that is not the shape it expects.

The packet validators are the trust boundary between a parent and a participant
it does not control, so the cases that matter are the ones where the input is
wrong. Each case here is a shape that used to pass validation, crash out of it,
or be recorded as something other than what was sent -- never a well-formed
packet being read correctly, which the fixture-driven suite beside this one
already covers.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import pathlib
import re
import sys
import tempfile
import types
import unicodedata
import unittest

sys.dont_write_bytecode = True

from installer.ai_agents_skills import delegation_dispatch as dispatch_module
from installer.ai_agents_skills import delegation_packets as packet_module
from installer.ai_agents_skills.delegation_packets import (
    validate_delegation_constraints,
    validate_enum,
    validate_ref,
    validate_result,
    validate_task,
)
from installer.ai_agents_skills.delegation_dispatch import (
    read_task_text,
    task_ref,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
HIDDEN_TEXT_VECTORS = REPO_ROOT / "tests" / "fixtures" / "hidden_text_vectors.json"
EXAMPLES = REPO_ROOT / "canonical" / "skills" / "cross-agent-delegation" / "references" / "examples.md"
RUNTIME_SKILLS = REPO_ROOT / "canonical" / "runtime" / "skills"
DEEP_RESEARCH_RUNTIME = RUNTIME_SKILLS / "deep-research-workflow" / "deep_research_workflow.py"
REMOTE_BRIDGE_RUNTIME = RUNTIME_SKILLS / "remote-bridge" / "remote_bridge.py"
# The installer's authority keys, which the deep-research validator mirrors.
AUTHORITY_KEYS = {
    "confirmed_by_parent",
    "execute",
    "execution_target",
    "execution_targets",
    "skip_confirmation",
    "approval_receipt",
    "approval_receipts",
    "parent_acceptance",
    "accepted_by_parent",
}


def load_runtime_module(name: str, path: pathlib.Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def example_packet(fixture_id: str) -> dict:
    """A packet from the cross-agent-delegation examples, read as the fixture suite reads it."""
    text = EXAMPLES.read_text(encoding="utf-8")
    match = re.search(
        r"<!-- fixture: id=" + re.escape(fixture_id) + r" [^\n]*-->\n```json\n(.*?)\n```", text, re.S
    )
    assert match is not None, fixture_id
    return json.loads(match.group(1))


def ref(source: str) -> dict[str, str]:
    return {
        "ref_id": "r1",
        "kind": "file",
        "source": source,
        "sensitivity": "internal",
        "access_note": "read-only",
    }


class RawTargetRefTests(unittest.TestCase):
    """Both contracts forbid raw locations; the check has to recognise them."""

    def test_every_forbidden_location_shape_is_refused(self) -> None:
        """The old check knew a drive-letter path and two URL schemes.

        On the platform the gates run on, that left the most ordinary raw
        target of all -- a POSIX absolute path -- validating clean, along with
        a home-relative path, a UNC share, and every non-http scheme.
        """

        for source in (
            "/etc/shadow",
            "/root/.ssh/id_rsa",
            "~/.aws/credentials",
            "file:///etc/passwd",
            "ssh://box/etc/shadow",
            "sftp://box/secret",
            "\\\\server\\share\\secret.txt",
            "//server/share/secret.txt",
            "C:\\Users\\me\\secret.txt",
            "C:/Users" + "/me/secret.txt",
            "http://x.test/a",
            "https://x.test/a",
        ):
            with self.subTest(source=source):
                self.assertIn("RAW_TARGET_REF", validate_ref(ref(source)))

    def test_a_relative_reference_is_still_accepted(self) -> None:
        """Control: the check must not swallow the shape the contract wants."""

        for source in (
            "docs/paper.tex",
            "section-4-proof",
            "run-2026-08-21/summary.md",
            "a:b",  # a two-letter label, not a drive
        ):
            with self.subTest(source=source):
                self.assertEqual(validate_ref(ref(source)), [])


class ConstraintShapeTests(unittest.TestCase):
    """A budget the parent capped must not escape the cap by changing shape."""

    def test_a_misshaped_constraints_field_fails_instead_of_skipping(self) -> None:
        """Both silent ``continue`` branches dropped the value unreported.

        The identical over-limit budget in the documented shape is rejected, so
        the wrong container was the whole of the bypass -- and because neither
        branch recorded an error, the packet validated with an empty list.
        """

        self.assertEqual(
            validate_delegation_constraints({"constraints": ["max_tokens=999999999"]}),
            ["BUDGET_CONSTRAINT_EXCEEDS_PARENT_POLICY"],
        )
        for label, value, code in (
            ("bare string", "max_tokens=999999999", "FIELD_NOT_ARRAY"),
            ("tuple", ("max_tokens=999999999",), "FIELD_NOT_ARRAY"),
            ("nested list", [["max_hops=99999"]], "CONSTRAINT_NOT_STRING"),
            ("dict item", [{"note": "max_usd=99999.00"}], "CONSTRAINT_NOT_STRING"),
        ):
            with self.subTest(shape=label):
                self.assertEqual(
                    validate_delegation_constraints({"constraints": value}), [code]
                )

    def test_a_within_policy_budget_still_validates(self) -> None:
        """Control: the shape check must not reject a compliant budget."""

        self.assertEqual(
            validate_delegation_constraints(
                {"constraints": ["max_tokens=1000", "max_usd=1.00"]}
            ),
            [],
        )


class MalformedFieldTypeTests(unittest.TestCase):
    """Validation reports; it does not crash out of the report shape."""

    TASK_ARRAYS = ("input_refs", "artifact_refs")
    RESULT_ARRAYS = (
        "provenance",
        "findings",
        "evidence",
        "artifacts",
        "warnings",
        "errors",
    )

    def test_a_non_iterable_array_field_is_reported_not_raised(self) -> None:
        """``for x in packet.get(field, [])`` raised on ``null`` and scalars.

        ``cli.py``'s blanket handler turned that into ``{"status": "error"}``
        with a bare Python message, losing the ``{status, kind, path, errors}``
        shape every other invalid packet reports and naming no field.
        """

        for field in self.TASK_ARRAYS:
            for bad in (None, 5, True, "refs"):
                with self.subTest(packet="task", field=field, value=bad):
                    self.assertIn("FIELD_NOT_ARRAY", validate_task({field: bad}))
        for field in self.RESULT_ARRAYS:
            for bad in (None, 5, True, "items"):
                with self.subTest(packet="result", field=field, value=bad):
                    self.assertIn("FIELD_NOT_ARRAY", validate_result({field: bad}))

    def test_an_unhashable_value_where_an_enum_belongs_is_reported(self) -> None:
        """``value in allowed`` hashes, so a dict or list raised ``TypeError``."""

        for value in ({}, [], set(), None, 5):
            with self.subTest(value=value):
                self.assertEqual(
                    validate_enum(value, {"ok"}, "CODE_INVALID"), ["CODE_INVALID"]
                )
        self.assertEqual(validate_enum("ok", {"ok"}, "CODE_INVALID"), [])

    def test_a_nested_target_refs_field_is_guarded_too(self) -> None:
        """``parent_action_request.target_refs`` is iterated on the same path."""

        errors = validate_result(
            {"parent_action_request": {"target_refs": None}}
        )
        self.assertIn("FIELD_NOT_ARRAY", errors)

    def test_no_top_level_field_of_any_wrong_type_escapes_as_an_exception(
        self,
    ) -> None:
        """A sweep, so a field added later cannot reintroduce the crash."""

        from installer.ai_agents_skills.delegation_packets import (
            RESULT_FIELDS,
            TASK_FIELDS,
        )

        for validator, fields in ((validate_task, TASK_FIELDS), (validate_result, RESULT_FIELDS)):
            for field in sorted(fields):
                for bad in (None, 5, True, "text", [], {}, [{}], [None]):
                    with self.subTest(validator=validator.__name__, field=field, value=bad):
                        self.assertIsInstance(validator({field: bad}), list)


class TaskSourceAgreementTests(unittest.TestCase):
    """What is dispatched and what is recorded have to be the same text."""

    def test_an_empty_task_beside_a_task_file_is_refused(self) -> None:
        """The guard tested truthiness; the branch below tested None-ness.

        ``--task "" --task-file real.txt`` fell between them: an empty prompt
        was dispatched while the record carried the digest of a file whose
        contents were never sent.
        """

        with tempfile.TemporaryDirectory() as tmp:
            task_file = pathlib.Path(tmp) / "real_task.txt"
            task_file.write_text("audit the proof in section 4\n", encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                read_task_text("", task_file)
            self.assertIn("exactly one", str(ctx.exception))

    def test_an_empty_inline_task_is_refused_on_its_own(self) -> None:
        """An empty prompt is never a dispatch worth making."""

        for text in ("", "   ", "\n\t "):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    read_task_text(text, None)

    def test_the_record_names_the_source_the_text_came_from(self) -> None:
        """Control, both ways: the two derivations have to agree."""

        with tempfile.TemporaryDirectory() as tmp:
            task_file = pathlib.Path(tmp) / "real_task.txt"
            task_file.write_text("audit the proof in section 4\n", encoding="utf-8")

            args = types.SimpleNamespace(task=None, task_file=task_file)
            self.assertEqual(
                read_task_text(args.task, args.task_file),
                "audit the proof in section 4\n",
            )
            self.assertEqual(task_ref(args)["kind"], "task-file")

            args = types.SimpleNamespace(task="inline work", task_file=None)
            self.assertEqual(read_task_text(args.task, args.task_file), "inline work")
            self.assertEqual(task_ref(args)["kind"], "inline-task")

    def test_naming_neither_source_is_still_refused(self) -> None:
        with self.assertRaises(ValueError):
            read_task_text(None, None)


class HiddenTextVectorTests(unittest.TestCase):
    """One hidden-character predicate, copied into each module that needs it, pinned by shared vectors.

    The packet validators of the installer and of deep research refuse what it
    marks, tab, line feed and carriage return aside. The delegate-agent task
    framing and the remote-bridge inbox split relayed text into lines, drop
    what it marks and count it.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.deep_research = load_runtime_module("drw_hidden_text_vectors", DEEP_RESEARCH_RUNTIME)
        cls.remote_bridge = load_runtime_module("rb_hidden_text_vectors", REMOTE_BRIDGE_RUNTIME)
        cls.cases = json.loads(HIDDEN_TEXT_VECTORS.read_text(encoding="utf-8"))["cases"]

    def packet_copies(self) -> dict[str, types.ModuleType]:
        return {"delegation_packets": packet_module, "deep_research_workflow": self.deep_research}

    def framing_copies(self) -> dict[str, types.ModuleType]:
        return {"delegation_dispatch": dispatch_module, "remote_bridge": self.remote_bridge}

    def predicate_copies(self) -> dict[str, types.ModuleType]:
        return {**self.packet_copies(), "remote_bridge": self.remote_bridge}

    def test_shared_vectors_hold_for_every_copy(self) -> None:
        """Every copy gives each vector's packet_hidden, framed lines and removed count.

        The minimum number of vectors is a check of the fixture, not of the change.
        """
        self.assertGreaterEqual(len(self.cases), 20)
        for case in self.cases:
            for name, module in self.packet_copies().items():
                with self.subTest(case=case["name"], copy=name):
                    self.assertEqual(module.hidden_text_positions(case["text"]), case["packet_hidden"])
            for name, module in self.framing_copies().items():
                with self.subTest(case=case["name"], copy=name):
                    self.assertEqual(
                        module.frame_relayed_lines(case["text"]), (case["framed"], case["removed"])
                    )

    def test_predicate_covers_the_digest_sanitizer_class(self) -> None:
        """Whatever the digest sanitizers treat as control text ({Cc, Cf, Cs}) is hidden, tab, LF and CR aside.

        So is every code point of the tag block and of the supplementary
        variation selectors, assigned or not. That U+202E is among the swept
        characters is a check of the sweep itself and held before the change.
        """
        sanitized = [
            chr(code) for code in range(0x110000) if unicodedata.category(chr(code)) in {"Cc", "Cf", "Cs"}
        ]
        self.assertIn("\u202e", sanitized)
        blocks = [chr(code) for code in (*range(0xE0000, 0xE0080), *range(0xE0100, 0xE01F0))]
        for name, module in self.packet_copies().items():
            with self.subTest(copy=name):
                missed = [
                    f"U+{ord(char):04X}"
                    for char in sanitized + blocks
                    if char not in "\t\n\r" and module.hidden_text_positions(char) != [0]
                ]
                self.assertEqual(missed, [])
                self.assertEqual([module.hidden_text_positions(char) for char in "\t\n\r"], [[], [], []])
        for name, module in self.predicate_copies().items():
            with self.subTest(predicate=name):
                missed = [f"U+{ord(char):04X}" for char in sanitized + blocks if not module.is_hidden_char(None, char)]
                self.assertEqual(missed, [])


class HiddenTextPacketTests(unittest.TestCase):
    """Both packet validators refuse hidden characters in keys and string values; neither cleans them."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.deep_research = load_runtime_module("drw_packet_refusals", DEEP_RESEARCH_RUNTIME)

    def deep_research_codes(self, packet: dict) -> set[str]:
        """validate's codes for a valid not-ready run whose delegation/ directory holds only ``packet``."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "research"
            root.mkdir()
            self.deep_research.write_minimal_v2_not_ready(root)
            (root / "delegation" / "packet.json").write_text(json.dumps(packet), encoding="utf-8")
            result = self.deep_research.validate_directory(root)
        self.assertEqual(result["checked"]["delegation_packets"], 1)
        return {error["code"] for error in result["errors"]}

    def test_hidden_keys_and_values_fail_both_validators(self) -> None:
        """A hidden character in a nested key or in a string value fails both validators with HIDDEN_TEXT.

        The nested key is "execute" with U+200B inside, which no key-name
        comparison matches, so it is refused for the hidden character before
        names are compared. The unchanged examples and a value with tab and
        line breaks stay valid: regression checks that held before the rule,
        as did the one-packet count that deep_research_codes asserts.
        """
        task = example_packet("valid-inert-task")
        result = example_packet("valid-partial-result")
        with self.subTest("regression check: the examples are valid for both validators"):
            self.assertEqual(validate_task(task), [])
            self.assertEqual(validate_result(result), [])
            self.assertEqual(self.deep_research_codes(task), set())
            self.assertEqual(self.deep_research_codes(result), set())

        cases = {}
        nested_key = copy.deepcopy(task)
        nested_key["expected_output"]["exe\u200bcute"] = True
        cases["nested key with U+200B under expected_output"] = (nested_key, validate_task)
        for label, char in (
            ("U+202E", "\u202e"),
            ("U+200B", "\u200b"),
            ("U+E0041", "\U000e0041"),
            ("lone U+D800", "\ud800"),
        ):
            packet = copy.deepcopy(task)
            packet["intent"] = f"Review a bounded{char} implementation plan."
            cases[f"task value with {label}"] = (packet, validate_task)
        finding = copy.deepcopy(result)
        finding["findings"][0]["rationale"] = "Only a supplied\u202e excerpt was available."
        cases["result finding with U+202E"] = (finding, validate_result)
        for label, (packet, installer_validator) in cases.items():
            with self.subTest(label):
                self.assertEqual(installer_validator(packet), ["HIDDEN_TEXT"])
                self.assertEqual(self.deep_research_codes(packet), {"HIDDEN_TEXT"})

        with self.subTest("regression check: tab and line breaks in a value"):
            packet = copy.deepcopy(task)
            packet["intent"] = "a\tb\nc\r\nd"
            self.assertEqual(validate_task(packet), [])
            self.assertEqual(self.deep_research_codes(packet), set())

    def test_deep_research_scans_a_packet_before_it_looks_up_the_schema(self) -> None:
        """A packet whose schema is unsupported still gets the forbidden-material codes from deep research.

        Hiding U+200B in the schema_version key leaves no schema_version, and
        deep research returned DELEGATION_PACKET_SCHEMA_INVALID before it
        scanned the packet. So did an unsupported schema_version beside a
        nested authority key. The installer validator, which collects every
        error, already reported HIDDEN_TEXT and FORBIDDEN_AUTHORITY_FIELD for
        them: regression checks.
        """
        hidden_key = example_packet("valid-inert-task")
        hidden_key["schema\u200b_version"] = hidden_key.pop("schema_version")
        authority = example_packet("valid-inert-task")
        authority["schema_version"] = "cross-agent-delegation.task.v9"
        authority["expected_output"]["execute"] = True
        cases = {
            "schema_version key with U+200B": (hidden_key, "HIDDEN_TEXT"),
            "unsupported schema_version beside a nested execute key": (authority, "FORBIDDEN_AUTHORITY_FIELD"),
        }
        for label, (packet, code) in cases.items():
            with self.subTest(label):
                self.assertEqual(self.deep_research_codes(packet), {code, "DELEGATION_PACKET_SCHEMA_INVALID"})
            with self.subTest(f"regression check: the installer validator, {label}"):
                self.assertIn(code, validate_task(packet))

    def test_deep_research_refuses_authority_keys_and_unknown_fields(self) -> None:
        """Deep research refuses the installer's authority keys at any depth and top-level fields outside the contract.

        Its field sets and authority keys mirror the installer's validator.
        Regression checks that held before: the installer's FORBIDDEN_KEYS
        already held the nine authority keys, and validate already counted the
        one packet that deep_research_codes asserts.
        """
        task = example_packet("valid-inert-task")
        result = example_packet("valid-partial-result")
        top_level = copy.deepcopy(task)
        top_level["approval_receipt"] = "receipt-1"
        nested = copy.deepcopy(task)
        nested["expected_output"]["execute"] = True
        unknown = copy.deepcopy(task)
        unknown["surprise"] = "not in the contract"
        nested_result = copy.deepcopy(result)
        nested_result["findings"][0]["accepted_by_parent"] = True
        unknown_result = copy.deepcopy(result)
        unknown_result["surprise"] = "not in the contract"
        cases = {
            "top-level approval_receipt": (top_level, {"FORBIDDEN_AUTHORITY_FIELD", "UNKNOWN_FIELD"}),
            "execute nested under expected_output": (nested, {"FORBIDDEN_AUTHORITY_FIELD"}),
            "unknown top-level task field": (unknown, {"UNKNOWN_FIELD"}),
            "accepted_by_parent nested in a finding": (nested_result, {"FORBIDDEN_AUTHORITY_FIELD"}),
            "unknown top-level result field": (unknown_result, {"UNKNOWN_FIELD"}),
        }
        for label, (packet, expected) in cases.items():
            with self.subTest(label):
                self.assertEqual(self.deep_research_codes(packet), expected)
        with self.subTest("the mirrored sets equal the installer's"):
            self.assertEqual(self.deep_research.TASK_FIELDS, packet_module.TASK_FIELDS)
            self.assertEqual(self.deep_research.RESULT_FIELDS, packet_module.RESULT_FIELDS)
            self.assertEqual(set(self.deep_research.AUTHORITY_PACKET_KEYS), AUTHORITY_KEYS)
            self.assertLessEqual(AUTHORITY_KEYS, packet_module.FORBIDDEN_KEYS)


if __name__ == "__main__":
    unittest.main()
