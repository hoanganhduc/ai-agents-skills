"""What a delegation validator does with input that is not the shape it expects.

The packet validators are the trust boundary between a parent and a participant
it does not control, so the cases that matter are the ones where the input is
wrong. Each case here is a shape that used to pass validation, crash out of it,
or be recorded as something other than what was sent -- never a well-formed
packet being read correctly, which the fixture-driven suite beside this one
already covers.
"""

from __future__ import annotations

import pathlib
import sys
import tempfile
import types
import unittest

sys.dont_write_bytecode = True

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
            "C:/Users/me/secret.txt",
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


if __name__ == "__main__":
    unittest.main()
