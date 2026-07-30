"""Focused behavior tests for the Goal-Focus v2 core."""

from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import os
import shlex
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = (
    REPO_ROOT
    / "canonical"
    / "runtime"
    / "skills"
    / "autonomous-research-loop-runtime"
)
sys.path.insert(0, str(RUNTIME_DIR))

import goal_focus as gf  # noqa: E402
import state_transaction as st  # noqa: E402
import autonomous_research_loop_runtime as rt  # noqa: E402
import panel_parent as pp  # noqa: E402


_TEST_PROVIDER_FAMILIES = {
    "codex": "openai",
    "claude": "anthropic",
}


class _ProviderAttestationFixture:
    def __init__(self) -> None:
        safe_parent = Path(os.path.realpath(Path.home()))
        self._temporary = tempfile.TemporaryDirectory(
            prefix=".aas-provider-fixture-", dir=safe_parent
        )
        self.root = Path(self._temporary.name)
        self.paths: dict[str, Path] = {}
        self.environment: dict[str, str] = {}
        python = str(Path(os.path.realpath(sys.executable)))
        for provider, family in _TEST_PROVIDER_FAMILIES.items():
            dependency_root = self.root / "providers" / provider
            dependency_root.mkdir(parents=True, mode=0o700)
            if os.name == "posix":
                (self.root / "providers").chmod(0o700)
                dependency_root.chmod(0o700)
            suffix = ".exe" if os.name == "nt" else ""
            path = dependency_root / f"{provider}{suffix}"
            if os.name == "nt":  # pragma: no cover - Windows CI fixture
                path.write_bytes(Path(python).read_bytes())
            else:
                path.write_text(
                    f"#!/bin/sh\nexec {shlex.quote(python)} \"$@\"\n",
                    encoding="utf-8",
                )
                path.chmod(0o700)
            digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            key = provider.upper()
            self.paths[provider] = path
            self.environment.update(
                {
                    f"AAS_AUTOLOOP_ATTESTED_BIN_{key}": str(path),
                    f"AAS_AUTOLOOP_ATTESTED_SHA256_{key}": digest,
                    f"AAS_AUTOLOOP_ATTESTED_UPSTREAM_{key}": family,
                    f"AAS_AUTOLOOP_ATTESTED_MODEL_{key}": f"{provider}-test-model",
                    f"AAS_AUTOLOOP_ATTESTED_DEPENDENCY_ROOT_{key}": str(
                        dependency_root
                    ),
                }
            )

    def cleanup(self) -> None:
        self._temporary.cleanup()


def _provider_attestation(provider: str, root: Path | None = None) -> dict:
    attestation = pp.attest_provider_executable(
        provider,
        required=True,
        forbidden_roots=((root,) if root is not None else ()),
    )
    if attestation is None:  # pragma: no cover - required=True fails instead
        raise AssertionError(f"missing test executable attestation for {provider}")
    return attestation


def _dispatch_evidence_path(
    root: Path, dispatch: dict, evidence_id: str
) -> Path:
    return root / str(dispatch["evidence_root"]) / evidence_id


def _provider_resource_attestation(**overrides: object) -> dict:
    limits = rt.public_resource_limits(
        rt.provider_resource_limits(60, role="primary", environ={})
    )
    attestation = {
        "schema_version": "provider_resource_attestation.v1",
        "provider_transport": "trusted-local",
        "role": "primary",
        "resource_gate": "pre-exec-cgroup-rlimit-v1",
        "scope_unit": "aas-arl-primary-1234-feedfacefeed.scope",
        "limits": limits,
        "output_capture": "bounded-pipe",
        "control_plane_masked": True,
        "cgroup_api_masked": True,
        "cleanup_verified": True,
        "capture_verified": True,
        "timed_out": False,
        "oversized_output": False,
        "sensitive_output_blocked": False,
        "finished_at": "2026-07-29T00:01:00Z",
    }
    attestation.update(copy.deepcopy(overrides))
    return attestation


_DEFAULT_RESOURCE_ATTESTATION = object()


class _AttestedGoalFocusTestCase(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.provider_fixture = _ProviderAttestationFixture()
        self.addCleanup(self.provider_fixture.cleanup)
        patcher = mock.patch.dict(
            os.environ, self.provider_fixture.environment, clear=False
        )
        patcher.start()
        self.addCleanup(patcher.stop)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _snapshot_files(root: Path, names: list[str]) -> dict[str, bytes | None]:
    return {
        name: (root / name).read_bytes() if (root / name).exists() else None
        for name in names
    }


def _migration_apply_guard(root: Path) -> dict:
    """Create the exact private claim required for a direct core apply test."""

    nonce = uuid.uuid4().hex
    payload = (
        json.dumps(
            {
                "pid": os.getpid(),
                "claimed_at": "2026-07-29T00:00:00Z",
                "nonce": nonce,
            },
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )
    claim_path = root / gf.MIGRATION_CLAIM_FILE
    fd = os.open(
        claim_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        if os.name == "posix":
            os.fchmod(fd, 0o600)
        remaining = memoryview(payload)
        while remaining:
            written = os.write(fd, remaining)
            if written <= 0:  # pragma: no cover - defensive short-write guard
                raise OSError("could not create migration claim fixture")
            remaining = remaining[written:]
        os.fsync(fd)
        info = os.fstat(fd)
    finally:
        os.close(fd)
    return {
        "schema_version": "goal_focus_migration_guard.v1",
        "run_dir": str(Path(os.path.realpath(os.path.abspath(root)))),
        "claim_identity": [int(info.st_dev), int(info.st_ino)],
        "claim_pid": os.getpid(),
        "nonce": nonce,
        "live_driver_count": 0,
    }


def _apply_migration(
    root: Path, *, active_campaign: str | None = None
) -> dict:
    return gf.migrate_v1(
        root,
        apply=True,
        active_campaign=active_campaign,
        migration_claim=_migration_apply_guard(root),
    )


def _seed_legacy_loop(root: Path, *, goal: str = "Prove the main theorem") -> None:
    _write_json(
        root / "loop_state.json",
        {
            "goal": goal,
            "success_criteria": "Produce a verified terminal theorem.",
            "status": "running",
            "last_iteration": 0,
            "next_preferred_path": "",
        },
    )
    _write_json(
        root / "budget.json",
        {
            "max_iterations": 20,
            "spent_iterations": 0,
            "spent_tokens": 0,
            "spent_usd": 0.0,
        },
    )
    (root / "iterations.jsonl").write_text("", encoding="utf-8")
    (root / "recovery.md").write_text("# Recovery\n", encoding="utf-8")


def _initialize(root: Path, *, mode: str = "enforce") -> None:
    _seed_legacy_loop(root)
    gf.initialize_goal_focus(
        root,
        goal="Prove the main theorem",
        success_criteria="Produce a verified terminal theorem.",
        mode=mode,
    )


def _install_approaches(root: Path, approaches: dict[str, dict]) -> dict:
    registry = gf.load_approach_registry(root)
    registry["registry_revision"] += 1
    campaigns: dict[str, dict] = {}
    for approach_id, approach in approaches.items():
        campaign_id = str(approach.get("campaign_id") or "C1")
        row = dict(approach)
        row.setdefault("id", approach_id)
        row.setdefault("campaign_id", campaign_id)
        row.setdefault("status", "eligible")
        campaigns.setdefault(
            campaign_id,
            {"id": campaign_id, "title": campaign_id, "approaches": {}},
        )["approaches"][approach_id] = row
    registry["campaigns"] = campaigns
    st.commit_transaction(
        root,
        json_files={gf.APPROACH_REGISTRY_FILE: registry},
        expected_revisions={gf.APPROACH_REGISTRY_FILE: ("registry_revision", 1)},
    )
    return registry


def _activate(root: Path, approach_id: str = "A1", campaign_id: str = "C1") -> dict:
    registry = _install_approaches(
        root,
        {
            approach_id: {
                "campaign_id": campaign_id,
                "status": "eligible",
                "objective": "Discharge the terminal obligation",
                "target_obligation_ids": ["GOAL-SC-1"],
                "next_action": "Prove the bounded bridge lemma.",
                "scope_lock": "full_goal",
                "estimates": {
                    "goal_resolution": "high",
                    "information_gain": "medium",
                    "execution_cost": "low",
                    "verification_cost": "low",
                },
            }
        },
    )
    selection = gf.select_direction(registry)
    committed = gf.commit_selected_direction(
        root,
        selection,
        _strategy_review(root, selection),
        "test_activation",
    )
    return committed["plan"]


def _strategy_review(
    root: Path,
    selection: dict | None = None,
    **overrides: object,
) -> dict:
    """Build the host-owned direction_review.v2 fixture from raw provider advice."""

    snapshot = gf.strategy_authority_snapshot(root)
    registry = snapshot["approach_registry"]
    requested_selection = selection if selection is not None else gf.select_direction(registry)
    campaign_id = str(requested_selection.get("selected_campaign_id") or "")
    approach_id = str(requested_selection.get("selected_approach_id") or "")
    approach = (
        registry.get("campaigns", {})
        .get(campaign_id, {})
        .get("approaches", {})
        .get(approach_id, {})
    )
    selected_override = requested_selection.get("selected_candidate")
    selected_override = selected_override if isinstance(selected_override, dict) else {}
    score = gf.score_approach(approach)
    estimates = {}
    for name in pp.ESTIMATE_FACTORS:
        registry_name = (
            "goal_resolution"
            if name == "goal_resolution_contribution"
            else name
        )
        component = score["components"][registry_name]
        estimates[name] = {
            "lower": int(component["lower"]),
            "upper": int(component["upper"]),
        }
    if isinstance(selected_override.get("estimates"), dict):
        estimates.update(copy.deepcopy(selected_override["estimates"]))
    provider_candidate = {
        "approach_id": approach_id,
        "campaign_id": campaign_id,
        "rank": 1,
        "estimates": estimates,
        "evidence_refs": list(approach.get("evidence_for") or []),
        "missing_evidence": [],
        "falsifier": str(approach.get("falsifier") or "The bounded check fails."),
        "strongest_objection": "The bridge to the terminal goal may remain open.",
        "next_action": str(
            selected_override.get("next_action")
            or approach.get("next_action")
            or "Run the bounded reviewed action."
        ),
    }
    provider_candidate.update(copy.deepcopy(selected_override))
    provider_candidate["estimates"] = estimates
    provider_advice = {
        "schema_version": "strategy_advice.v1",
        "decision": "select",
        "recommended_approach_id": approach_id,
        "candidates": [provider_candidate],
        "inspected_evidence": [],
        "uninspected_evidence": [],
        "reasoning_summary": "The selected approach is the deterministic conservative winner.",
    }
    synthesis = {
        "required_schema": "strategy_advice.v1",
        "primary_provider": "codex",
        "primary_family": _provider_attestation("codex", root)["family"],
        "valid_providers": ["claude"],
        "different_family_valid_providers": ["claude"],
        "recommendation_counts": {approach_id: 1},
        "decision_counts": {"select": 1},
        "dissent": False,
    }
    host_result = rt._strategy_selection_from_panel(
        root,
        {
            "authority_snapshot": snapshot,
            "structured_synthesis": synthesis,
            "primary_execution_attestation": _provider_attestation("codex", root),
            "provider_execution_attestations": {
                "claude": _provider_attestation("claude", root)
            },
            "results": {
                "claude": {
                    "structured_valid": True,
                    "structured_payload": provider_advice,
                }
            },
        },
    )
    if host_result.get("status") != "ready":
        raise AssertionError(f"invalid strategy review fixture: {host_result}")
    if selection is not None:
        selection.clear()
        selection.update(copy.deepcopy(host_result["selection"]))
    review = copy.deepcopy(host_result["review"])
    review.update(overrides)
    return review


def _stage_enforced_candidate(
    root: Path,
    plan: dict,
    record: dict,
    *,
    host_resource_attestation: object = _DEFAULT_RESOURCE_ATTESTATION,
) -> dict:
    """Stage a test candidate through the same host dispatch contract as production."""

    prepared = gf.prepare_iteration_dispatch(
        root,
        executor_provider="codex",
        executor_family="openai",
        executor_attestation=_provider_attestation("codex", root),
        started_at="2026-07-29T00:00:00Z",
    )
    dispatch = prepared["dispatch"]
    staged_record = copy.deepcopy(record)
    claim_ids = list(staged_record.get("claim_ids") or ["CLAIM-TEST"])
    staged_record["candidate_id"] = dispatch["candidate_id"]
    staged_record["claim_ids"] = claim_ids
    evidence_checked = staged_record.setdefault("evidence_checked", {})
    evidence_checked.setdefault("claim_ids", claim_ids)
    evidence_ids = list(staged_record.get("evidence_ids") or [])
    evidence_ids.extend(evidence_checked.get("evidence_ids") or [])
    evidence_checked["evidence_ids"] = list(
        dict.fromkeys(evidence_ids or ["EVIDENCE-TEST"])
    )
    for evidence_id in evidence_checked["evidence_ids"]:
        evidence_path = _dispatch_evidence_path(root, dispatch, evidence_id)
        if not evidence_path.exists():
            evidence_path.write_text(
                f"host-visible evidence for {evidence_id}\n", encoding="utf-8"
            )
    staged_record["goal_focus"] = {
        "plan_revision": plan["plan_revision"],
        "campaign_id": plan["campaign_id"],
        "approach_id": plan["approach_id"],
    }
    execution = staged_record.setdefault("execution", {})
    execution.setdefault("executor_provider", "codex")
    execution.setdefault(
        "compute",
        {"recording_status": "explicit", "usage": "none", "services": []},
    )
    resource_attestation = (
        _provider_resource_attestation()
        if host_resource_attestation is _DEFAULT_RESOURCE_ATTESTATION
        else host_resource_attestation
    )
    return gf.stage_iteration_candidate(
        root,
        staged_record,
        plan["plan_revision"],
        expected_dispatch_id=dispatch["dispatch_id"],
        host_resource_attestation=(
            resource_attestation
            if isinstance(resource_attestation, dict)
            else None
        ),
    )


def _bound_provider_review(staged: dict, **overrides: object) -> dict:
    """Build one provider's raw result_review.v1 payload."""

    candidate = staged["candidate"]
    record = candidate["record"]
    host_attestation = candidate["host_execution_attestation"]
    executor_attestation = copy.deepcopy(host_attestation["executor_attestation"])
    evidence_ids = list(record.get("evidence_ids") or [])
    evidence_ids.extend(record.get("evidence_checked", {}).get("evidence_ids") or [])
    evidence_refs = list(dict.fromkeys(evidence_ids))
    review = {
        "schema_version": "result_review.v1",
        "candidate_id": candidate["candidate_id"],
        "candidate_fingerprint": gf.candidate_fingerprint(candidate),
        "executor_provider": executor_attestation["provider"],
        "executor_family": executor_attestation["family"],
        "executor_attestation": executor_attestation,
        "status": "passed",
        "verdict": "pass",
        "safe_to_bank": True,
        "different_family": True,
        "reviewer_provider": "claude",
        "inspected_paths": evidence_refs,
        "uninspected_paths": [],
        "invalidation_conditions": [],
        "summary": "Independent test review inspected the staged evidence.",
        "claim_reviews": [
            {
                "claim_id": claim_id,
                "status": "supported",
                "evidence_refs": evidence_refs,
                "reason": "The complete staged evidence supports the claim.",
            }
            for claim_id in record["claim_ids"]
        ],
        "obligation_reviews": [],
        "machine_checks": [
            {
                "status": "passed",
                "artifact_ref": evidence_refs[0],
                "summary": "Host snapshot was structurally valid.",
            }
        ],
    }
    review.update(overrides)
    if overrides.get("status") == "failed" and "verdict" not in overrides:
        review["verdict"] = "fail"
        review["safe_to_bank"] = False
    for row in review.get("claim_reviews") or []:
        if isinstance(row, dict):
            row.setdefault("reason", "Test reviewer claim assessment.")
    for row in review.get("obligation_reviews") or []:
        if isinstance(row, dict):
            row.setdefault("reason", "Test reviewer transition assessment.")
    if "inspected_paths" not in overrides:
        cited = [
            str(ref)
            for key in ("claim_reviews", "obligation_reviews")
            for row in review.get(key) or []
            if isinstance(row, dict)
            for ref in row.get("evidence_refs") or []
            if str(ref)
        ]
        review["inspected_paths"] = list(dict.fromkeys(cited or evidence_refs))
    return review


def _bound_review(staged: dict, **overrides: object) -> dict:
    """Build the panel summary envelope whose embedded reviews are authoritative."""

    provider_review = _bound_provider_review(staged, **overrides)
    conservative_verdict = str(provider_review.get("verdict") or "unavailable")
    host_attestation = staged["candidate"]["host_execution_attestation"]
    executor_attestation = copy.deepcopy(host_attestation["executor_attestation"])
    reviewer_attestation = _provider_attestation("claude")
    return {
        "schema_version": "result_review_summary.v2",
        "candidate_id": provider_review["candidate_id"],
        "candidate_fingerprint": provider_review["candidate_fingerprint"],
        "status": provider_review.get("status", "passed"),
        "executor_provider": executor_attestation["provider"],
        "executor_family": executor_attestation["family"],
        "executor_attestation": executor_attestation,
        "different_family": True,
        "providers": ["claude"],
        "different_family_providers": ["claude"],
        "reviewer_families": [reviewer_attestation["family"]],
        "provider_execution_attestations": {
            "claude": reviewer_attestation
        },
        "conservative_verdict": conservative_verdict,
        "structured_synthesis": {
            "conservative_verdict": conservative_verdict
        },
        "provider_reviews": {"claude": provider_review},
        "claim_reviews": copy.deepcopy(provider_review["claim_reviews"]),
        "obligation_reviews": copy.deepcopy(provider_review["obligation_reviews"]),
        "machine_checks": copy.deepcopy(provider_review["machine_checks"]),
    }


def _accepted_terminal_fixture(root: Path) -> tuple[dict, dict]:
    """Create the exact staged candidate and panel review used by finalization."""

    args = rt.selftest_init_args(root, max_iterations=20)
    args.goal_focus_mode = "enforce"
    rt.init_loop(args)
    plan = _activate(root)
    staged = _stage_enforced_candidate(
        root,
        plan,
        {
            "mode": "bounded-research",
            "objective": "Discharge the reviewed terminal obligation",
            "output": "Verified terminal proof",
            "decision": "stop",
            "stop_reason": "proof_found",
            "campaign_delta": "closed",
            "obligation_transitions": [
                {"obligation_id": "GOAL-SC-1", "to": "satisfied"}
            ],
            "evidence_ids": ["proof.json"],
            "budget_delta": {"iterations": 1, "tokens": 50, "usd": 0.0},
        },
    )
    review = _bound_review(
        staged,
        obligation_reviews=[
            {
                "obligation_id": "GOAL-SC-1",
                "target_status": "satisfied",
                "verdict": "accept",
                "evidence_refs": ["proof.json"],
            }
        ],
    )
    return staged, review


class StateTransactionSecurityTests(unittest.TestCase):
    @staticmethod
    def _plant_transaction(
        root: Path,
        *,
        transaction_id: str = "planted",
        phase: str = "prepared",
        include_committed_at: bool = False,
    ) -> tuple[Path, Path, Path]:
        tx_dir = root / st.TRANSACTION_DIRNAME / transaction_id
        post_dir = tx_dir / "postimages"
        post_dir.mkdir(parents=True, mode=0o700)
        if os.name == "posix":
            (root / st.TRANSACTION_DIRNAME).chmod(0o700)
            tx_dir.chmod(0o700)
            post_dir.chmod(0o700)
        postimage = post_dir / "post.bin"
        payload = b"attacker-controlled replacement\n"
        postimage.write_bytes(payload)
        manifest = {
            "schema_version": "goal_focus_transaction.v1",
            "transaction_id": transaction_id,
            "phase": phase,
            "created_at": "2026-07-29T00:00:00Z",
            "targets": [
                {
                    "path": "goal_contract.json",
                    "delete": False,
                    "blob": postimage.name,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            ],
        }
        if include_committed_at:
            manifest["committed_at"] = "2026-07-29T00:00:01Z"
        manifest_path = tx_dir / "manifest.json"
        _write_json(manifest_path, manifest)
        if os.name == "posix":
            manifest_path.chmod(0o600)
            postimage.chmod(0o600)
        return tx_dir, manifest_path, postimage

    def test_reserved_lock_and_journal_targets_are_rejected_in_every_channel(self) -> None:
        reserved_paths = (
            st.LOCK_FILENAME,
            f"{st.TRANSACTION_DIRNAME}/forged/manifest.json",
        )
        for reserved in reserved_paths:
            channels = (
                ("json_files", {"json_files": {reserved: {"value": "replace"}}}),
                ("text_files", {"text_files": {reserved: "replace"}}),
                ("binary_files", {"binary_files": {reserved: b"replace"}}),
                (
                    "jsonl_appends",
                    {"jsonl_appends": {reserved: [{"event_id": "replace"}]}},
                ),
                ("deletes", {"deletes": [reserved]}),
                (
                    "expected_revisions",
                    {"expected_revisions": {reserved: ("revision", 0)}},
                ),
                ("expected_absent", {"expected_absent": [reserved]}),
                (
                    "expected_hashes",
                    {"expected_hashes": {reserved: hashlib.sha256(b"x").hexdigest()}},
                ),
            )
            for channel, reserved_kwargs in channels:
                with self.subTest(reserved=reserved, channel=channel), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    victim = root / "ordinary.txt"
                    victim.write_bytes(b"unchanged\n")
                    lock = root / st.LOCK_FILENAME
                    lock.write_bytes(b"private-lock\n")
                    if os.name == "posix":
                        lock.chmod(0o600)
                    kwargs = copy.deepcopy(reserved_kwargs)
                    text_files = dict(kwargs.get("text_files") or {})
                    text_files["ordinary.txt"] = "changed\n"
                    kwargs["text_files"] = text_files

                    with self.assertRaises(st.TransactionError):
                        st.commit_transaction(root, **kwargs)

                    self.assertEqual(victim.read_bytes(), b"unchanged\n")
                    self.assertEqual(lock.read_bytes(), b"private-lock\n")

    def test_recovery_rejects_forged_and_malformed_manifests_before_apply(self) -> None:
        base_manifest = {
            "schema_version": "goal_focus_transaction.v1",
            "transaction_id": "forged",
            "phase": "prepared",
            "targets": [],
        }
        malformed = {
            "wrong_schema": {
                **base_manifest,
                "schema_version": "attacker.v1",
            },
            "targets_not_list": {
                **base_manifest,
                "targets": {},
            },
            "target_not_object": {
                **base_manifest,
                "targets": ["victim.txt"],
            },
            "reserved_lock_delete": {
                **base_manifest,
                "targets": [{"path": st.LOCK_FILENAME, "delete": True}],
            },
            "reserved_journal_replace": {
                **base_manifest,
                "targets": [
                    {
                        "path": f"{st.TRANSACTION_DIRNAME}/other/manifest.json",
                        "delete": False,
                        "blob": "post.bin",
                        "sha256": "0" * 64,
                    }
                ],
            },
            "traversal_target": {
                **base_manifest,
                "targets": [{"path": "../victim.txt", "delete": True}],
            },
            "duplicate_target": {
                **base_manifest,
                "targets": [
                    {"path": "victim.txt", "delete": True},
                    {"path": "victim.txt", "delete": True},
                ],
            },
            "directory_id_mismatch": {
                **base_manifest,
                "transaction_id": "somebody-else",
            },
        }
        for label, manifest in malformed.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                victim = root / "victim.txt"
                victim.write_bytes(b"preserve me\n")
                tx_dir = root / st.TRANSACTION_DIRNAME / "forged"
                tx_dir.mkdir(parents=True)
                _write_json(tx_dir / "manifest.json", manifest)

                with self.assertRaises(st.TransactionError):
                    st.recover_transactions(root)

                self.assertEqual(victim.read_bytes(), b"preserve me\n")
                self.assertFalse((root / "somebody-else").exists())

    @unittest.skipUnless(os.name == "posix", "requires POSIX link and mode semantics")
    def test_lock_symlink_hardlink_and_permissive_leaf_preserve_victim(self) -> None:
        for attack in ("symlink", "hardlink", "permissive"):
            with self.subTest(attack=attack), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                victim = root / "victim.txt"
                victim.write_bytes(b"victim stays unchanged\n")
                lock = root / st.LOCK_FILENAME
                if attack == "symlink":
                    lock.symlink_to(victim)
                elif attack == "hardlink":
                    os.link(victim, lock)
                else:
                    lock.write_bytes(b"permissive lock stays unchanged\n")
                    lock.chmod(0o666)
                victim_before = victim.read_bytes()
                lock_before = None if lock.is_symlink() else lock.read_bytes()

                with self.assertRaises((OSError, st.TransactionError)):
                    st.commit_transaction(
                        root,
                        text_files={"ordinary.txt": "must not be written\n"},
                    )

                self.assertEqual(victim.read_bytes(), victim_before)
                self.assertFalse((root / "ordinary.txt").exists())
                if lock_before is not None:
                    self.assertEqual(lock.read_bytes(), lock_before)

    @unittest.skipUnless(os.name == "posix", "requires POSIX ownership and mode semantics")
    def test_recovery_rejects_unsafe_journal_roots_and_entries(self) -> None:
        for attack in (
            "root_symlink",
            "root_permissive",
            "root_nonowned",
            "entry_symlink",
            "entry_permissive",
        ):
            with self.subTest(attack=attack), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                victim = root / "goal_contract.json"
                victim.write_bytes(b"authority remains unchanged\n")
                journal = root / st.TRANSACTION_DIRNAME
                recover = lambda: st.recover_transactions(root)
                identity_patch = None
                if attack == "root_symlink":
                    outside = root / "outside-journal"
                    outside.mkdir(mode=0o700)
                    journal.symlink_to(outside, target_is_directory=True)
                elif attack == "root_permissive":
                    journal.mkdir(mode=0o700)
                    journal.chmod(0o777)
                elif attack == "root_nonowned":
                    journal.mkdir(mode=0o700)
                    identity_patch = mock.patch.object(
                        st.os, "geteuid", return_value=os.geteuid() + 1
                    )
                    recover = lambda: st._recover_locked(root)
                elif attack == "entry_symlink":
                    journal.mkdir(mode=0o700)
                    outside = root / "outside-entry"
                    outside.mkdir(mode=0o700)
                    (journal / "planted").symlink_to(
                        outside, target_is_directory=True
                    )
                else:
                    entry = journal / "planted"
                    entry.mkdir(parents=True, mode=0o700)
                    entry.chmod(0o777)
                before = victim.read_bytes()

                context = identity_patch or contextlib.nullcontext()
                with context, self.assertRaises(st.TransactionError):
                    recover()

                self.assertEqual(victim.read_bytes(), before)

    @unittest.skipUnless(os.name == "posix", "requires POSIX link and mode semantics")
    def test_recovery_rejects_linked_or_permissive_manifest_and_postimage(self) -> None:
        for attack in (
            "manifest_hardlink",
            "manifest_permissive",
            "postimage_hardlink",
            "postimage_permissive",
        ):
            with self.subTest(attack=attack), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                victim = root / "goal_contract.json"
                victim.write_bytes(b"authority remains unchanged\n")
                _, manifest_path, postimage = self._plant_transaction(root)
                attacked = manifest_path if attack.startswith("manifest") else postimage
                if attack.endswith("hardlink"):
                    payload = attacked.read_bytes()
                    attacked.unlink()
                    outside = root / f"outside-{attacked.name}"
                    outside.write_bytes(payload)
                    outside.chmod(0o600)
                    os.link(outside, attacked)
                else:
                    attacked.chmod(0o666)
                before = victim.read_bytes()

                with self.assertRaises(st.TransactionError):
                    st.recover_transactions(root)

                self.assertEqual(victim.read_bytes(), before)

    def test_recovery_rejects_malformed_committed_manifest_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            victim = root / "goal_contract.json"
            victim.write_bytes(b"authority remains unchanged\n")
            tx_dir, _, _ = self._plant_transaction(root, phase="committed")
            before = victim.read_bytes()

            with self.assertRaises(st.TransactionError):
                st.recover_transactions(root)

            self.assertEqual(victim.read_bytes(), before)
            self.assertTrue(tx_dir.exists())

    def test_committed_journal_is_retained_when_live_poststate_is_missing_or_tampered(self) -> None:
        for attack in ("missing", "tampered"):
            with self.subTest(attack=attack), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                tx_dir, _, postimage = self._plant_transaction(
                    root,
                    phase="committed",
                    include_committed_at=True,
                )
                target = root / "goal_contract.json"
                target.write_bytes(postimage.read_bytes())
                if attack == "missing":
                    target.unlink()
                else:
                    target.write_bytes(b"tampered committed post-state\n")

                with self.assertRaises(st.TransactionError):
                    st.recover_transactions(root)

                self.assertTrue(tx_dir.exists())
                if attack == "missing":
                    self.assertFalse(target.exists())
                else:
                    self.assertEqual(
                        target.read_bytes(), b"tampered committed post-state\n"
                    )


class GoalFocusContractsTests(_AttestedGoalFocusTestCase):
    def test_authority_revisions_require_exact_integer_types(self) -> None:
        with self.assertRaises(ValueError):
            gf.default_current_plan(goal_revision="1")
        with self.assertRaises(ValueError):
            gf.default_current_plan(registry_revision=1.0)

        authority_fields = (
            (gf.GOAL_CONTRACT_FILE, "goal_revision"),
            (gf.APPROACH_REGISTRY_FILE, "registry_revision"),
            (gf.CURRENT_PLAN_FILE, "plan_revision"),
        )
        for filename, field in authority_fields:
            for malformed in ("1", 1.0, True):
                with self.subTest(filename=filename, malformed=malformed), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    _initialize(root)
                    value = json.loads((root / filename).read_text(encoding="utf-8"))
                    value[field] = malformed
                    _write_json(root / filename, value)
                    result = gf.validate_goal_focus(root, require_enabled=True)
                    self.assertEqual(result["status"], "error", result)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize(root, mode="monitor")
            plan = _activate(root)
            with self.assertRaises(ValueError):
                gf.stage_iteration_candidate(
                    root,
                    {},
                    float(plan["plan_revision"]),
                )

    def test_initialize_creates_revisioned_contracts_and_managed_view(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize(root)
            self.assertEqual(gf.goal_focus_mode(root), "enforce")
            self.assertTrue(gf.is_goal_focus_enabled(root))
            bundle = gf.load_goal_focus(root)
            self.assertEqual(bundle["contract"]["schema_version"], gf.GOAL_CONTRACT_SCHEMA)
            self.assertEqual(bundle["registry"]["schema_version"], gf.APPROACH_REGISTRY_SCHEMA)
            self.assertEqual(bundle["plan"]["state"], "needs_replan")
            self.assertIn("goal-focus-managed:start", (root / "recovery.md").read_text(encoding="utf-8"))
            result = gf.validate_goal_focus(root, require_enabled=True)
            self.assertEqual(result["status"], "ok", result)
            self.assertEqual(result["errors"], [])
            self.assertIn(gf.CURRENT_PLAN_FILE, result["checked"])

    def test_initialize_refuses_zero_revision_and_concurrent_authority_creation(self) -> None:
        authority_targets = {
            gf.GOAL_CONTRACT_FILE: {"goal_revision": 0},
            gf.APPROACH_REGISTRY_FILE: {"registry_revision": 0},
            gf.CURRENT_PLAN_FILE: {"plan_revision": 0},
        }
        for target, payload in authority_targets.items():
            with self.subTest(mode="preexisting", target=target), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _seed_legacy_loop(root)
                _write_json(root / target, payload)
                before = (root / target).read_bytes()

                with self.assertRaisesRegex(ValueError, "already initialized"):
                    gf.initialize_goal_focus(
                        root,
                        goal="Prove the main theorem",
                        success_criteria="Produce a verified terminal theorem.",
                    )

                self.assertEqual((root / target).read_bytes(), before)
                self.assertFalse((root / gf.DIRECTION_DECISIONS_FILE).exists())

        concurrent_targets: dict[str, bytes] = {
            **{
                target: (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
                for target, payload in authority_targets.items()
            },
            gf.DIRECTION_DECISIONS_FILE: b'{"decision_type":"foreign"}\n',
        }
        for target, injected in concurrent_targets.items():
            with self.subTest(mode="concurrent", target=target), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _seed_legacy_loop(root)
                real_commit = gf.commit_transaction

                def create_before_commit(*args: object, **kwargs: object) -> dict:
                    (root / target).write_bytes(injected)
                    return real_commit(*args, **kwargs)

                with mock.patch.object(
                    gf, "commit_transaction", side_effect=create_before_commit
                ), self.assertRaises(st.RevisionConflict):
                    gf.initialize_goal_focus(
                        root,
                        goal="Prove the main theorem",
                        success_criteria="Produce a verified terminal theorem.",
                    )

                self.assertEqual((root / target).read_bytes(), injected)
                for other in (
                    gf.GOAL_CONTRACT_FILE,
                    gf.APPROACH_REGISTRY_FILE,
                    gf.CURRENT_PLAN_FILE,
                    gf.DIRECTION_DECISIONS_FILE,
                ):
                    if other != target:
                        self.assertFalse((root / other).exists(), other)

    def test_reconcile_rejects_exact_byte_and_absent_projection_races(self) -> None:
        race_targets = (
            gf.GOAL_CONTRACT_FILE,
            gf.APPROACH_REGISTRY_FILE,
            gf.CURRENT_PLAN_FILE,
            "loop_state.json",
            "recovery.md",
        )
        for target in race_targets:
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _initialize(root)
                state = json.loads((root / "loop_state.json").read_text(encoding="utf-8"))
                state["next_preferred_path"] = "stale projection"
                _write_json(root / "loop_state.json", state)
                target_path = root / target
                before = target_path.read_bytes()
                raced = before + b"\n"
                real_commit = gf.commit_transaction

                def change_before_commit(*args: object, **kwargs: object) -> dict:
                    target_path.write_bytes(raced)
                    return real_commit(*args, **kwargs)

                with mock.patch.object(
                    gf, "commit_transaction", side_effect=change_before_commit
                ), self.assertRaises(st.RevisionConflict):
                    gf.reconcile_goal_focus(root, apply=True)

                self.assertEqual(target_path.read_bytes(), raced)
                self.assertEqual(
                    json.loads((root / "loop_state.json").read_text(encoding="utf-8"))[
                        "next_preferred_path"
                    ],
                    "stale projection",
                )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize(root)
            state = json.loads((root / "loop_state.json").read_text(encoding="utf-8"))
            state["next_preferred_path"] = "stale projection"
            _write_json(root / "loop_state.json", state)
            recovery = root / "recovery.md"
            recovery.unlink()
            raced = b"concurrently created recovery view\n"
            real_commit = gf.commit_transaction

            def create_absent_before_commit(*args: object, **kwargs: object) -> dict:
                recovery.write_bytes(raced)
                return real_commit(*args, **kwargs)

            with mock.patch.object(
                gf, "commit_transaction", side_effect=create_absent_before_commit
            ), self.assertRaises(st.RevisionConflict):
                gf.reconcile_goal_focus(root, apply=True)

            self.assertEqual(recovery.read_bytes(), raced)

    def test_active_direction_updates_plan_path_recovery_and_decision_together(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize(root)
            plan = _activate(root)
            self.assertEqual(plan["state"], "active")
            self.assertEqual(plan["campaign_id"], "C1")
            state = json.loads((root / "loop_state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["next_preferred_path"], gf.render_current_path(plan))
            self.assertEqual(state["goal_focus_projection"]["plan_revision"], plan["plan_revision"])
            self.assertIn(gf.render_current_path(plan), (root / "recovery.md").read_text(encoding="utf-8"))
            decisions = gf.load_direction_decisions(root)
            self.assertEqual(decisions[-1]["decision_type"], "select_direction")
            validation = gf.validate_goal_focus(root, require_enabled=True)
            self.assertEqual(validation["status"], "ok", validation)

    def test_enforce_validation_rejects_stale_legacy_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize(root)
            _activate(root)
            state = json.loads((root / "loop_state.json").read_text(encoding="utf-8"))
            state["next_preferred_path"] = "campaign `C2` — stale path"
            _write_json(root / "loop_state.json", state)
            result = gf.validate_goal_focus(root, require_enabled=True)
            self.assertEqual(result["status"], "error")
            self.assertTrue(any("next_preferred_path" in error for error in result["errors"]))
            gate = gf.pre_dispatch_gate(root, regenerate_views=True)
            self.assertTrue(gate["ok"], gate)
            self.assertEqual(gate["action"], "dispatch")
            self.assertTrue(any("regenerated" in warning for warning in gate["warnings"]))

    def test_invalid_enforcement_mode_cannot_silently_disable_v2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize(root)
            plan = gf.load_current_plan(root)
            plan["enforcement_mode"] = "typo-off"
            _write_json(root / gf.CURRENT_PLAN_FILE, plan)
            with self.assertRaises(ValueError):
                gf.goal_focus_mode(root)
            gate = gf.pre_dispatch_gate(root)
            self.assertFalse(gate["ok"])
            self.assertEqual(gate["action"], "reconcile")
            self.assertTrue(any("enforcement_mode" in error for error in gate["errors"]))

    def test_authenticated_enforce_mode_cannot_be_downgraded_by_plan_edit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize(root)
            decisions = gf.load_direction_decisions(root)
            self.assertEqual(decisions[-1]["enforcement_mode"], "enforce")
            plan = gf.load_current_plan(root)
            plan["enforcement_mode"] = "monitor"
            _write_json(root / gf.CURRENT_PLAN_FILE, plan)

            validation = gf.validate_goal_focus(root, require_enabled=True)
            self.assertTrue(
                any(
                    "enforcement_mode lacks a complete decision row bound to the exact plan postimage"
                    in error
                    for error in validation["errors"]
                ),
                validation,
            )
            gate = gf.pre_dispatch_gate(root)
            self.assertFalse(gate["ok"], gate)
            self.assertEqual(gate["action"], "reconcile")

    def test_duplicate_success_criterion_ids_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize(root)
            contract = gf.load_goal_contract(root)
            contract["success_criteria"].append(
                copy.deepcopy(contract["success_criteria"][0])
            )
            _write_json(root / gf.GOAL_CONTRACT_FILE, contract)

            validation = gf.validate_goal_focus(root, require_enabled=True)

            self.assertTrue(
                any("success criterion ids must be unique" in error for error in validation["errors"]),
                validation,
            )

    def test_precompleted_obligation_requires_evidence_and_completed_dependencies(self) -> None:
        for status in ("satisfied", "closed"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _initialize(root)
                contract = gf.load_goal_contract(root)
                contract["obligations"]["BRIDGE"] = {
                    "id": "BRIDGE",
                    "kind": "bridge",
                    "description": "Required bridge",
                    "status": "open",
                    "depends_on": [],
                    "evidence_refs": [],
                }
                terminal = contract["obligations"]["GOAL-SC-1"]
                terminal["status"] = status
                terminal["depends_on"] = ["BRIDGE"]
                terminal["evidence_refs"] = []
                _write_json(root / gf.GOAL_CONTRACT_FILE, contract)

                validation = gf.validate_goal_focus(root, require_enabled=True)

                self.assertTrue(
                    any("requires at least one evidence_ref" in error for error in validation["errors"]),
                    validation,
                )
                self.assertTrue(
                    any("has incomplete dependencies: BRIDGE" in error for error in validation["errors"]),
                    validation,
                )

    def test_partial_authority_without_current_plan_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_legacy_loop(root)
            _write_json(root / gf.GOAL_CONTRACT_FILE, {"schema_version": "partial"})

            with self.assertRaisesRegex(ValueError, "partial Goal-Focus authority"):
                gf.goal_focus_mode(root)
            gate = gf.pre_dispatch_gate(root)

            self.assertFalse(gate["ok"])
            self.assertEqual(gate["action"], "reconcile")
            self.assertTrue(
                any(gf.CURRENT_PLAN_FILE in error for error in gate["errors"]), gate
            )

    def test_force_init_cannot_partially_overwrite_goal_focus_authority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = rt.selftest_init_args(root, max_iterations=2)
            args.goal_focus_mode = "enforce"
            rt.init_loop(args)
            tracked = [
                "loop_state.json",
                "budget.json",
                "iterations.jsonl",
                "recovery.md",
                gf.GOAL_CONTRACT_FILE,
                gf.APPROACH_REGISTRY_FILE,
                gf.CURRENT_PLAN_FILE,
                gf.DIRECTION_DECISIONS_FILE,
            ]
            before = _snapshot_files(root, tracked)

            replacement = rt.selftest_init_args(root, max_iterations=99)
            replacement.force = True
            replacement.goal = "replacement goal"
            replacement.goal_focus_mode = "off"
            with self.assertRaisesRegex(ValueError, "cannot overwrite"):
                rt.init_loop(replacement)

            self.assertEqual(_snapshot_files(root, tracked), before)

    def test_active_plan_postimage_must_match_reviewed_direction_authority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize(root)
            _activate(root)
            plan = gf.load_current_plan(root)
            plan["next_action"] = "Unreviewed replacement action."
            _write_json(root / gf.CURRENT_PLAN_FILE, plan)
            tampered = (root / gf.CURRENT_PLAN_FILE).read_bytes()

            validation = gf.validate_goal_focus(root, require_enabled=True)
            self.assertTrue(
                any("not bound" in error for error in validation["errors"]),
                validation,
            )
            gate = gf.pre_dispatch_gate(root, regenerate_views=True)
            self.assertFalse(gate["ok"], gate)
            self.assertEqual(gate["action"], "reconcile")
            self.assertEqual((root / gf.CURRENT_PLAN_FILE).read_bytes(), tampered)

    def test_obligation_dependency_cycle_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize(root)
            contract = gf.load_goal_contract(root)
            contract["obligations"]["BRIDGE-1"] = {
                "id": "BRIDGE-1",
                "kind": "bridge",
                "description": "Bridge into the terminal theorem",
                "status": "open",
                "depends_on": ["GOAL-SC-1"],
                "evidence_refs": [],
            }
            contract["obligations"]["GOAL-SC-1"]["depends_on"] = ["BRIDGE-1"]
            _write_json(root / gf.GOAL_CONTRACT_FILE, contract)

            validation = gf.validate_goal_focus(root, require_enabled=True)
            self.assertTrue(
                any("obligation dependency cycle" in error for error in validation["errors"]),
                validation,
            )

    def test_monitor_pending_review_advises_but_does_not_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_legacy_loop(root)
            gf.initialize_goal_focus(
                root,
                goal="Prove the main theorem",
                success_criteria="Produce a verified terminal theorem.",
                mode="monitor",
            )
            plan = _activate(root)
            gf.stage_iteration_candidate(root, {"output": "candidate"}, plan["plan_revision"])
            gate = gf.pre_dispatch_gate(root)
            self.assertTrue(gate["ok"])
            self.assertEqual(gate["action"], "review_pending")

    def test_reviewed_candidate_overrides_stale_registered_next_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize(root)
            registry = _install_approaches(
                root,
                {
                    "A1": {
                        "campaign_id": "C1",
                        "status": "eligible",
                        "next_action": "Old migrated action.",
                    }
                },
            )
            selection = gf.select_direction(registry)
            selection["selected_candidate"] = {
                "approach_id": "A1",
                "next_action": "Run the fresh bounded discriminator.",
                "falsifier": "The discriminator returns zero.",
                "estimates": {"information_gain": {"lower": 3, "upper": 4}},
            }
            committed = gf.commit_selected_direction(
                root,
                selection,
                _strategy_review(root, selection),
                "fresh_strategy_review",
            )
            self.assertEqual(
                committed["plan"]["next_action"], "Run the fresh bounded discriminator."
            )
            self.assertEqual(committed["plan"]["falsifier"], "The discriminator returns zero.")


class GoalFocusSelectionTests(_AttestedGoalFocusTestCase):
    def test_estimates_reject_malformed_nonfinite_or_inverted_values(self) -> None:
        malformed = (
            {"goal_resolution": "certainly-high"},
            {"goal_resolution": float("nan")},
            {"goal_resolution": float("inf")},
            {"goal_resolution": -1},
            {"goal_resolution": 5},
            {"goal_resolution": True},
            {"goal_resolution": [3, 2]},
            {"goal_resolution": {}},
            {"goal_resolution": {"lower": 1, "upper": 2, "typo": 4}},
        )
        for estimates in malformed:
            with self.subTest(estimates=estimates), self.assertRaises(ValueError):
                gf.score_approach({"estimates": estimates})
        with self.assertRaises(ValueError):
            gf.score_approach({"estimates": []})

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize(root)
            _install_approaches(
                root,
                {
                    "A1": {
                        "campaign_id": "C1",
                        "estimates": {"goal_resolution": float("nan")},
                    }
                },
            )
            result = gf.validate_goal_focus(root, require_enabled=True)
            self.assertTrue(
                any("must be finite and between 0 and 4" in error for error in result["errors"]),
                result,
            )

    def test_score_uses_conservative_benefits_and_upper_penalties(self) -> None:
        score = gf.score_approach(
            {
                "id": "A",
                "campaign_id": "C",
                "estimates": {
                    "goal_resolution": {"lower": 2, "upper": 3},
                    "information_gain": [1, 2],
                    "option_value": 1,
                    "diversity": 0,
                    "execution_cost": [1, 2],
                    "verification_cost": 1,
                    "bridge_debt": [0, 1],
                    "dependency_risk": 0,
                    "redundancy": 0,
                },
            }
        )
        self.assertEqual(score["conservative"], 5.0)
        self.assertEqual(score["optimistic"], 19.0)

    def test_interval_dominance_selects_exploitation(self) -> None:
        registry = gf.default_approach_registry()
        registry["campaigns"] = {
            "C": {
                "approaches": {
                    "strong": {
                        "status": "eligible",
                        "estimates": {"goal_resolution": [3, 4]},
                    },
                    "weak": {
                        "status": "eligible",
                        "estimates": {"goal_resolution": [0, 1]},
                    },
                }
            }
        }
        result = gf.select_direction(registry)
        self.assertEqual(result["selection_mode"], "dominant_exploitation")
        self.assertEqual(result["selected_approach_id"], "strong")

    def test_overlapping_intervals_select_information_per_cost(self) -> None:
        registry = gf.default_approach_registry()
        registry["campaigns"] = {
            "C": {
                "approaches": {
                    "informative": {
                        "status": "eligible",
                        "diversity_tags": ["constructive"],
                        "estimates": {
                            "goal_resolution": [1, 3],
                            "information_gain": [3, 4],
                            "execution_cost": 1,
                            "verification_cost": 1,
                        },
                    },
                    "expensive": {
                        "status": "eligible",
                        "diversity_tags": ["algebraic"],
                        "estimates": {
                            "goal_resolution": [2, 4],
                            "information_gain": [1, 3],
                            "execution_cost": 3,
                            "verification_cost": 2,
                        },
                    },
                }
            }
        }
        result = gf.select_direction(registry, max_portfolio=3)
        self.assertEqual(result["selection_mode"], "bounded_exploration")
        self.assertEqual(result["selected_approach_id"], "informative")
        self.assertEqual(set(result["portfolio"]), {"informative", "expensive"})

    def test_blocked_and_unsatisfied_dependency_are_excluded(self) -> None:
        registry = gf.default_approach_registry()
        registry["campaigns"] = {
            "C": {
                "approaches": {
                    "blocked": {"status": "blocked", "reopen_condition": "new mechanism"},
                    "dependent": {"status": "eligible", "dependencies": ["blocked"]},
                    "open": {"status": "eligible", "estimates": {"information_gain": "low"}},
                }
            }
        }
        result = gf.select_direction(registry)
        self.assertEqual(result["selected_approach_id"], "open")
        self.assertEqual(len(result["excluded"]), 2)

    def test_closed_campaign_is_excluded_and_cannot_be_committed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize(root)
            registry = gf.load_approach_registry(root)
            registry["registry_revision"] = 2
            registry["campaigns"] = {
                "closed-campaign": {
                    "id": "closed-campaign",
                    "status": "closed",
                    "approaches": {
                        "closed-approach": {
                            "id": "closed-approach",
                            "campaign_id": "closed-campaign",
                            "status": "eligible",
                            "next_action": "Do not dispatch this action.",
                            "estimates": {"goal_resolution": "very_high"},
                        }
                    },
                },
                "open-campaign": {
                    "id": "open-campaign",
                    "status": "open",
                    "approaches": {
                        "open-approach": {
                            "id": "open-approach",
                            "campaign_id": "open-campaign",
                            "status": "eligible",
                            "next_action": "Run the eligible action.",
                            "estimates": {"information_gain": "low"},
                        }
                    },
                },
            }
            st.commit_transaction(
                root,
                json_files={gf.APPROACH_REGISTRY_FILE: registry},
                expected_revisions={
                    gf.APPROACH_REGISTRY_FILE: ("registry_revision", 1)
                },
            )

            selected = gf.select_direction(registry)
            self.assertEqual(selected["selected_approach_id"], "open-approach")
            self.assertEqual(
                selected["excluded"],
                [
                    {
                        "campaign_id": "closed-campaign",
                        "approach_id": "closed-approach",
                        "reason": "campaign_status:closed",
                    }
                ],
            )
            tracked = [gf.CURRENT_PLAN_FILE, gf.DIRECTION_DECISIONS_FILE]
            before = _snapshot_files(root, tracked)
            with self.assertRaisesRegex(ValueError, "campaign_status:closed"):
                gf.commit_selected_direction(
                    root,
                    {
                        "selected_campaign_id": "closed-campaign",
                        "selected_approach_id": "closed-approach",
                    },
                    _strategy_review(root),
                    "crafted_closed_selection",
                )
            self.assertEqual(_snapshot_files(root, tracked), before)

    def test_reviewed_compute_policy_may_narrow_but_not_widen_operator_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_legacy_loop(root)
            state = json.loads((root / "loop_state.json").read_text(encoding="utf-8"))
            state["standing_orders"] = {
                "compute": {
                    "allowed_services": ["modal", "kaggle"],
                    "forbidden_services": ["hetzner"],
                }
            }
            _write_json(root / "loop_state.json", state)
            gf.initialize_goal_focus(
                root,
                goal="Prove the main theorem",
                success_criteria="Produce a verified terminal theorem.",
            )
            initial_policy = gf.load_current_plan(root)["compute_policy"]
            self.assertEqual(initial_policy["allowed_services"], ["kaggle", "modal"])
            self.assertEqual(initial_policy["forbidden_services"], ["hetzner"])

            registry = _install_approaches(
                root,
                {
                    "A1": {
                        "campaign_id": "C1",
                        "next_action": "Run the bounded computation.",
                    }
                },
            )
            selection = gf.select_direction(registry)
            selection["selected_candidate"] = {
                "approach_id": "A1",
                "compute_policy": {"allowed_services": ["modal", "hetzner"]},
            }
            tracked = [gf.CURRENT_PLAN_FILE, gf.DIRECTION_DECISIONS_FILE]
            before = _snapshot_files(root, tracked)
            with self.assertRaisesRegex(ValueError, "widens the user allowlist"):
                gf.commit_selected_direction(
                    root,
                    selection,
                    _strategy_review(root, selection),
                    "reviewed_compute_policy",
                )
            self.assertEqual(_snapshot_files(root, tracked), before)

            selection["selected_candidate"]["compute_policy"] = {
                "allowed_services": ["modal"]
            }
            committed = gf.commit_selected_direction(
                root,
                selection,
                _strategy_review(root, selection),
                "reviewed_compute_policy",
            )
            self.assertEqual(
                committed["plan"]["compute_policy"]["allowed_services"], ["modal"]
            )
            self.assertEqual(
                committed["plan"]["compute_policy"]["user_allowed_services"],
                ["kaggle", "modal"],
            )

    def test_strategy_commit_rejects_same_revision_authority_mutation(self) -> None:
        for mutation in (
            "goal",
            "registry",
            "plan",
            "goal_source_bytes",
            "registry_source_bytes",
            "plan_source_bytes",
        ):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _initialize(root)
                registry = _install_approaches(
                    root,
                    {
                        "A1": {
                            "campaign_id": "C1",
                            "status": "eligible",
                            "next_action": "Run the reviewed action.",
                        }
                    },
                )
                selection = gf.select_direction(registry)
                review = _strategy_review(root, selection)
                if mutation == "goal":
                    path = root / gf.GOAL_CONTRACT_FILE
                    value = gf.load_goal_contract(root)
                    value["goal"] = "Unreviewed same-revision goal."
                    _write_json(path, value)
                elif mutation == "registry":
                    path = root / gf.APPROACH_REGISTRY_FILE
                    value = gf.load_approach_registry(root)
                    value["campaigns"]["C1"]["approaches"]["A1"][
                        "next_action"
                    ] = "Unreviewed same-revision action."
                    _write_json(path, value)
                elif mutation == "plan":
                    path = root / gf.CURRENT_PLAN_FILE
                    value = gf.load_current_plan(root)
                    value["next_action"] = "Unreviewed same-revision plan."
                    _write_json(path, value)
                else:
                    authority_name, binding_field = {
                        "goal_source_bytes": (
                            gf.GOAL_CONTRACT_FILE,
                            "goal_contract_source_sha256",
                        ),
                        "registry_source_bytes": (
                            gf.APPROACH_REGISTRY_FILE,
                            "approach_registry_source_sha256",
                        ),
                        "plan_source_bytes": (
                            gf.CURRENT_PLAN_FILE,
                            "current_plan_source_sha256",
                        ),
                    }[mutation]
                    path = root / authority_name
                    value = json.loads(path.read_text(encoding="utf-8"))
                    path.write_text(
                        json.dumps(value, sort_keys=True, separators=(",", ":")),
                        encoding="utf-8",
                    )
                    self.assertNotEqual(
                        hashlib.sha256(path.read_bytes()).hexdigest(),
                        review["authority_snapshot"][binding_field],
                    )
                before = _snapshot_files(
                    root, [gf.CURRENT_PLAN_FILE, gf.DIRECTION_DECISIONS_FILE]
                )

                with self.assertRaisesRegex(
                    st.RevisionConflict,
                    "strategy-reviewed authority changed|transaction preimage changed",
                ):
                    gf.commit_selected_direction(
                        root, selection, review, "same_revision_mutation"
                    )

                self.assertEqual(
                    _snapshot_files(
                        root, [gf.CURRENT_PLAN_FILE, gf.DIRECTION_DECISIONS_FILE]
                    ),
                    before,
                )

    def test_direction_commit_revalidates_host_summary_and_raw_advice(self) -> None:
        for case in (
            "missing_provider_advice",
            "invalid_raw_schema",
            "spoofed_primary_family",
            "mutated_selected_action",
        ):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _initialize(root)
                registry = _install_approaches(
                    root,
                    {
                        "A1": {
                            "campaign_id": "C1",
                            "status": "eligible",
                            "next_action": "Run the independently reviewed action.",
                        }
                    },
                )
                selection = gf.select_direction(registry)
                review = _strategy_review(root, selection)
                if case == "missing_provider_advice":
                    review.pop("provider_advice")
                elif case == "invalid_raw_schema":
                    review["provider_advice"]["claude"]["schema_version"] = "forged"
                elif case == "spoofed_primary_family":
                    review["primary_family"] = "anthropic"
                else:
                    selection["selected_candidate"]["next_action"] = "Unreviewed action."
                tracked = [gf.CURRENT_PLAN_FILE, gf.DIRECTION_DECISIONS_FILE]
                before = _snapshot_files(root, tracked)

                with self.assertRaises(ValueError):
                    gf.commit_selected_direction(
                        root,
                        selection,
                        review,
                        "adversarial_direction_review",
                    )

                self.assertEqual(_snapshot_files(root, tracked), before)


class GoalFocusProgressTests(_AttestedGoalFocusTestCase):
    def test_encoding_construction_without_bridge_evidence_is_not_global_progress(self) -> None:
        contract = gf.default_goal_contract("Goal", "Terminal theorem")
        result = gf.classify_progress(
            {
                "campaign_delta": "substantial",
                "scope_lock": "encoding_only",
                "obligation_transitions": [
                    {"obligation_id": "GOAL-SC-1", "to": "partial"}
                ],
                "evidence_ids": [],
            },
            contract,
        )
        self.assertEqual(result["campaign_delta"], "substantial")
        self.assertEqual(result["global_delta"], "none")

    def test_verified_terminal_transition_counts_global_progress(self) -> None:
        contract = gf.default_goal_contract("Goal", "Terminal theorem")
        result = gf.classify_progress(
            {
                "campaign_delta": "substantial",
                "obligation_transitions": [
                    {"obligation_id": "GOAL-SC-1", "to": "satisfied"}
                ],
                "evidence_ids": ["proof.json"],
            },
            contract,
        )
        self.assertEqual(result["global_delta"], "satisfied")

    def test_goal_satisfaction_is_conjunctive_across_success_criteria(self) -> None:
        contract = gf.default_goal_contract("Goal", ["First", "Second"])
        first = gf.classify_progress(
            {
                "obligation_transitions": [
                    {"obligation_id": "GOAL-SC-1", "to": "satisfied"}
                ],
                "evidence_ids": ["first.json"],
            },
            contract,
        )
        self.assertEqual(first["global_delta"], "reduced")
        contract["obligations"]["GOAL-SC-1"]["status"] = "satisfied"
        contract["obligations"]["GOAL-SC-1"]["evidence_refs"] = ["first.json"]
        second = gf.classify_progress(
            {
                "obligation_transitions": [
                    {"obligation_id": "GOAL-SC-2", "to": "satisfied"}
                ],
                "evidence_ids": ["second.json"],
            },
            contract,
        )
        self.assertEqual(second["global_delta"], "satisfied")

    def test_transition_cannot_skip_open_dependency(self) -> None:
        contract = gf.default_goal_contract("Goal", "Terminal")
        contract["obligations"]["BRIDGE"] = {
            "id": "BRIDGE",
            "kind": "bridge",
            "description": "Required bridge",
            "status": "open",
            "depends_on": [],
            "evidence_refs": [],
        }
        contract["obligations"]["GOAL-SC-1"]["depends_on"] = ["BRIDGE"]
        skipped = gf.classify_progress(
            {
                "obligation_transitions": [
                    {"obligation_id": "GOAL-SC-1", "to": "satisfied"}
                ],
                "evidence_ids": ["terminal.json"],
            },
            contract,
        )
        self.assertEqual(skipped["global_delta"], "none")
        self.assertEqual(skipped["obligation_transitions"], [])
        same_result = gf.classify_progress(
            {
                "obligation_transitions": [
                    {"obligation_id": "GOAL-SC-1", "to": "satisfied"},
                    {"obligation_id": "BRIDGE", "to": "satisfied"},
                ],
                "evidence_ids": ["bridge.json", "terminal.json"],
            },
            contract,
        )
        self.assertEqual(same_result["global_delta"], "satisfied")

    def test_three_scope_only_rows_force_replan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize(root)
            plan = _activate(root)
            rows = [
                {
                    "iteration": index,
                    "plan_revision": plan["plan_revision"],
                    "bank_status": "accepted",
                    "campaign_delta": "incremental",
                    "global_delta": "none",
                    "scope_lock": "encoding_only",
                }
                for index in range(1, 4)
            ]
            (root / "iterations.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
            )
            codes = {row["code"] for row in gf.evaluate_replan_triggers(root)}
            self.assertIn("global_progress_stall", codes)
            self.assertIn("scope_only_streak", codes)
            gate = gf.pre_dispatch_gate(root)
            self.assertFalse(gate["ok"])
            self.assertEqual(gate["action"], "replan")

    def test_stall_streak_resets_after_reviewed_plan_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize(root)
            active_plan = _activate(root)
            rows = [
                {
                    "iteration": index,
                    "plan_revision": active_plan["plan_revision"],
                    "bank_status": "accepted",
                    "campaign_delta": "incremental",
                    "global_delta": "none",
                    "scope_lock": "encoding_only",
                }
                for index in range(1, 4)
            ]
            (root / "iterations.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
            )
            before_codes = {
                row["code"] for row in gf.evaluate_replan_triggers(root)
            }
            self.assertIn("global_progress_stall", before_codes)
            registry = gf.load_approach_registry(root)
            selection = gf.select_direction(registry)
            committed = gf.commit_selected_direction(
                root,
                selection,
                _strategy_review(root, selection),
                "stall_replan",
            )
            self.assertEqual(
                committed["plan"]["plan_revision"],
                active_plan["plan_revision"] + 1,
            )

            codes = {row["code"] for row in gf.evaluate_replan_triggers(root)}
            self.assertNotIn("global_progress_stall", codes)
            self.assertNotIn("scope_only_streak", codes)


class GoalFocusMigrationTests(_AttestedGoalFocusTestCase):
    def _legacy(self, root: Path, *, path_campaign: str, recovery_campaign: str, ledger_campaign: str) -> None:
        _seed_legacy_loop(root)
        state = json.loads((root / "loop_state.json").read_text(encoding="utf-8"))
        state["next_preferred_path"] = (
            f"Pivot to campaign `{path_campaign}`. Do not restart campaign `A2`."
        )
        state["standing_orders"] = {
            "goal_priority": {
                "enabled": True,
                "discipline_mode": "hard",
                "primary_campaign": "A2",
                "next_campaigns_ordered": ["A2", "A3", "A1"],
            }
        }
        _write_json(root / "loop_state.json", state)
        _write_json(
            root / "goal_priority.json",
            {
                "enabled": True,
                "primary_campaign": "A2",
                "next_campaigns_ordered": ["A2", "A3", "A1"],
                "campaign_registry": {
                    "A2": {"objective": "old"},
                    "A3": {"objective": "new"},
                    "A1": {"objective": "alternative"},
                },
            },
        )
        (root / "recovery.md").write_text(
            f"# Recovery\n\n- Next safe action: execute campaign `{recovery_campaign}`.\n",
            encoding="utf-8",
        )
        (root / "iterations.jsonl").write_text(
            json.dumps({"iteration": 1, "campaign_id": ledger_campaign}) + "\n",
            encoding="utf-8",
        )

    def test_direct_apply_without_verified_claim_guard_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._legacy(
                root,
                path_campaign="A3",
                recovery_campaign="A3",
                ledger_campaign="A3",
            )
            legacy_sources = list(gf.LEGACY_MIGRATION_SOURCE_FILES)
            before = _snapshot_files(root, legacy_sources)

            with self.assertRaisesRegex(
                ValueError,
                "migration apply requires a verified host migration claim guard",
            ):
                gf.migrate_v1(root, apply=True)

            self.assertEqual(_snapshot_files(root, legacy_sources), before)
            for authority in (
                gf.GOAL_CONTRACT_FILE,
                gf.APPROACH_REGISTRY_FILE,
                gf.CURRENT_PLAN_FILE,
                gf.DIRECTION_DECISIONS_FILE,
            ):
                self.assertFalse((root / authority).exists(), authority)
            self.assertFalse((root / ".goal_focus_backups").exists())
            self.assertFalse((root / gf.MIGRATION_CLAIM_FILE).exists())

    def test_migration_backup_manifest_is_exact_and_bound_to_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._legacy(
                root,
                path_campaign="A3",
                recovery_campaign="A3",
                ledger_campaign="A3",
            )
            source_snapshot = _snapshot_files(
                root, list(gf.LEGACY_MIGRATION_SOURCE_FILES)
            )

            applied = _apply_migration(root)

            self.assertEqual(applied["status"], "migrated", applied)
            metadata = applied["migration_backup"]
            backup_relative = str(metadata["backup_relative_path"])
            self.assertRegex(
                backup_relative,
                r"^\.goal_focus_backups/[0-9]{8}T[0-9]{6}Z-[0-9a-f]{32}$",
            )
            backup_root = root / backup_relative
            self.assertEqual(Path(applied["backup_dir"]), backup_root)
            manifest_path = backup_root / "backup_manifest.json"
            self.assertEqual(Path(applied["backup_manifest"]), manifest_path)
            manifest_bytes = manifest_path.read_bytes()
            self.assertEqual(
                metadata["manifest_sha256"],
                hashlib.sha256(manifest_bytes).hexdigest(),
            )
            manifest = json.loads(manifest_bytes.decode("utf-8"))
            self.assertEqual(manifest["schema_version"], gf.MIGRATION_BACKUP_SCHEMA)
            self.assertEqual(manifest["backup_relative_path"], backup_relative)
            self.assertEqual(
                manifest["manifest_relative_path"],
                f"{backup_relative}/backup_manifest.json",
            )
            self.assertEqual(
                manifest["restore_instructions"], metadata["restore_instructions"]
            )
            self.assertTrue(all(manifest["restore_instructions"]))
            records = {
                str(record["source_path"]): record
                for record in manifest["sources"]
            }
            self.assertEqual(set(records), set(gf.LEGACY_MIGRATION_SOURCE_FILES))
            self.assertEqual(manifest["sources"], metadata["sources"])
            for source_name, source_bytes in source_snapshot.items():
                record = records[source_name]
                self.assertIs(record["present"], source_bytes is not None)
                if source_bytes is None:
                    self.assertEqual(
                        record,
                        {"source_path": source_name, "present": False},
                    )
                    continue
                expected_backup = backup_root / source_name
                self.assertEqual(
                    record["backup_relative_path"],
                    expected_backup.relative_to(root).as_posix(),
                )
                self.assertEqual(record["size_bytes"], len(source_bytes))
                self.assertEqual(
                    record["sha256"], hashlib.sha256(source_bytes).hexdigest()
                )
                self.assertEqual(expected_backup.read_bytes(), source_bytes)

            decisions = gf.load_direction_decisions(root)
            self.assertEqual(len(decisions), 1)
            decision = decisions[0]
            self.assertEqual(decision["decision_type"], "migration")
            self.assertEqual(decision["migration_backup"], metadata)
            self.assertEqual(manifest["decision_id"], decision["decision_id"])
            self.assertEqual(
                manifest["migration_transaction_id"],
                decision["migration_transaction_id"],
            )
            self.assertEqual(
                decision["migration_transaction_id"],
                applied["transaction"]["transaction_id"],
            )

    def test_preseeded_exact_uuid_backup_target_blocks_before_authority_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._legacy(
                root,
                path_campaign="A3",
                recovery_campaign="A3",
                ledger_campaign="A3",
            )
            legacy_before = _snapshot_files(
                root, list(gf.LEGACY_MIGRATION_SOURCE_FILES)
            )
            stamp = "20260729T120000Z"
            fixed_uuid = uuid.UUID("01234567-89ab-cdef-0123-456789abcdef")
            backup_parent = root / ".goal_focus_backups"
            backup_root = backup_parent / f"{stamp}-{fixed_uuid.hex}"
            backup_root.mkdir(parents=True, mode=0o700)
            if os.name == "posix":
                backup_parent.chmod(0o700)
                backup_root.chmod(0o700)
            planted = backup_root / "loop_state.json"
            planted.write_bytes(b"planted backup victim\n")
            planted_before = planted.read_bytes()
            fake_uuid_module = mock.Mock()
            fake_uuid_module.uuid4.return_value = fixed_uuid

            with mock.patch.object(
                gf, "_compact_stamp", return_value=stamp
            ), mock.patch.object(gf, "uuid", fake_uuid_module):
                result = _apply_migration(root)

            self.assertEqual(result["status"], "source_changed", result)
            self.assertFalse(result["applied"])
            self.assertEqual(planted.read_bytes(), planted_before)
            self.assertEqual(
                _snapshot_files(root, list(gf.LEGACY_MIGRATION_SOURCE_FILES)),
                legacy_before,
            )
            self.assertEqual(
                sorted(
                    path.relative_to(backup_root).as_posix()
                    for path in backup_root.rglob("*")
                    if path.is_file()
                ),
                ["loop_state.json"],
            )
            for authority in (
                gf.GOAL_CONTRACT_FILE,
                gf.APPROACH_REGISTRY_FILE,
                gf.CURRENT_PLAN_FILE,
                gf.DIRECTION_DECISIONS_FILE,
            ):
                self.assertFalse((root / authority).exists(), authority)

    @unittest.skipUnless(os.name == "posix", "requires POSIX symlink and mode semantics")
    def test_unsafe_migration_backup_parent_fails_closed(self) -> None:
        for attack in ("symlink", "permissive"):
            with self.subTest(attack=attack), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self._legacy(
                    root,
                    path_campaign="A3",
                    recovery_campaign="A3",
                    ledger_campaign="A3",
                )
                legacy_before = _snapshot_files(
                    root, list(gf.LEGACY_MIGRATION_SOURCE_FILES)
                )
                backup_parent = root / ".goal_focus_backups"
                if attack == "symlink":
                    outside = root / "outside-backups"
                    outside.mkdir(mode=0o700)
                    victim = outside / "victim.txt"
                    victim.write_bytes(b"outside remains unchanged\n")
                    backup_parent.symlink_to(outside, target_is_directory=True)
                else:
                    backup_parent.mkdir(mode=0o700)
                    backup_parent.chmod(0o777)
                    victim = backup_parent / "victim.txt"
                    victim.write_bytes(b"permissive victim remains unchanged\n")
                victim_before = victim.read_bytes()

                with self.assertRaisesRegex(
                    ValueError,
                    "migration backup namespace is not",
                ):
                    _apply_migration(root)

                self.assertEqual(victim.read_bytes(), victim_before)
                self.assertEqual(
                    _snapshot_files(root, list(gf.LEGACY_MIGRATION_SOURCE_FILES)),
                    legacy_before,
                )
                for authority in (
                    gf.GOAL_CONTRACT_FILE,
                    gf.APPROACH_REGISTRY_FILE,
                    gf.CURRENT_PLAN_FILE,
                    gf.DIRECTION_DECISIONS_FILE,
                ):
                    self.assertFalse((root / authority).exists(), authority)

    def test_agreeing_dynamic_signals_override_stale_primary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._legacy(root, path_campaign="A3", recovery_campaign="A3", ledger_campaign="A3")
            report = gf.plan_migration(root)
            self.assertEqual(report["status"], "ready")
            self.assertEqual(report["selected_campaign_id"], "A3")
            self.assertEqual(report["legacy_primary_campaign"], "A2")
            applied = _apply_migration(root)
            self.assertEqual(applied["status"], "migrated")
            self.assertEqual(gf.load_current_plan(root)["campaign_id"], "A3")
            self.assertEqual(gf.load_current_plan(root)["state"], "provisional")
            self.assertTrue(Path(applied["backup_dir"]).is_dir())
            self.assertEqual(applied["validation"]["errors"], [], applied)

    def test_migration_dry_run_is_read_only_and_apply_preserves_backup_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._legacy(root, path_campaign="A3", recovery_campaign="A3", ledger_campaign="A3")
            legacy_budget = b'{"legacy_note":"raw-\xff-byte"}\r\n'
            (root / "budget.json").write_bytes(legacy_budget)
            legacy_files = [
                "goal_priority.json",
                "loop_state.json",
                "budget.json",
                "iterations.jsonl",
                "recovery.md",
            ]
            before = _snapshot_files(root, legacy_files)

            dry_run = gf.migrate_v1(root, apply=False)
            self.assertFalse(dry_run["applied"])
            self.assertEqual(_snapshot_files(root, legacy_files), before)
            self.assertFalse((root / gf.GOAL_CONTRACT_FILE).exists())

            applied = _apply_migration(root)
            backup = Path(applied["backup_dir"])
            self.assertEqual((backup / "budget.json").read_bytes(), legacy_budget)
            self.assertEqual(applied["validation"]["errors"], [], applied)

    def test_disagreeing_dynamic_signals_refuse_to_guess(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._legacy(root, path_campaign="A3", recovery_campaign="A1", ledger_campaign="A3")
            report = gf.plan_migration(root)
            self.assertEqual(report["status"], "ambiguous")
            applied = _apply_migration(root)
            self.assertEqual(applied["status"], "migrated")
            self.assertTrue(applied["applied"])
            plan = gf.load_current_plan(root)
            self.assertEqual(plan["state"], "needs_replan")
            self.assertEqual(plan["campaign_id"], "")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._legacy(root, path_campaign="A3", recovery_campaign="A1", ledger_campaign="A3")
            override = _apply_migration(root, active_campaign="A3")
            self.assertEqual(override["status"], "migrated")
            self.assertEqual(gf.load_current_plan(root)["campaign_id"], "A3")

    def test_migration_binds_every_source_byte_and_absent_source(self) -> None:
        for source_name in gf.LEGACY_MIGRATION_SOURCE_FILES:
            with self.subTest(source_name=source_name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self._legacy(
                    root,
                    path_campaign="A3",
                    recovery_campaign="A3",
                    ledger_campaign="A3",
                )
                source_path = root / source_name
                was_present = source_path.exists()
                real_commit = gf.commit_transaction

                def mutate_before_commit(*args: object, **kwargs: object) -> dict:
                    if was_present:
                        source_path.write_bytes(source_path.read_bytes() + b"\n")
                    else:
                        source_path.parent.mkdir(parents=True, exist_ok=True)
                        source_path.write_text(
                            json.dumps({"campaign_id": "A3"}) + "\n",
                            encoding="utf-8",
                        )
                    return real_commit(*args, **kwargs)

                with mock.patch.object(
                    gf, "commit_transaction", side_effect=mutate_before_commit
                ):
                    result = _apply_migration(root)

                self.assertEqual(result["status"], "source_changed", result)
                self.assertFalse(result["applied"])
                self.assertRegex(
                    result["error"],
                    "transaction preimage changed|expected transaction target to be absent",
                )
                self.assertTrue(source_path.exists())
                self.assertFalse((root / gf.GOAL_CONTRACT_FILE).exists())
                self.assertFalse((root / ".goal_focus_backups").exists())

    def test_migration_post_apply_validation_failure_reports_recovery_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._legacy(
                root,
                path_campaign="A3",
                recovery_campaign="A3",
                ledger_campaign="A3",
            )
            legacy_state = (root / "loop_state.json").read_bytes()
            failed_validation = {
                "status": "error",
                "errors": ["simulated post-apply authority failure"],
                "warnings": [],
                "checked": [
                    gf.GOAL_CONTRACT_FILE,
                    gf.APPROACH_REGISTRY_FILE,
                    gf.CURRENT_PLAN_FILE,
                ],
            }
            with mock.patch.object(
                gf,
                "reconcile_goal_focus",
                return_value={"status": "ok", "applied": True},
            ), mock.patch.object(
                gf, "validate_goal_focus", return_value=failed_validation
            ):
                result = _apply_migration(root)

            self.assertEqual(result["status"], "post_apply_validation_failed")
            self.assertFalse(result["applied"])
            self.assertTrue(result["authority_written"])
            self.assertTrue(result["recovery_required"])
            self.assertEqual(result["validation"], failed_validation)
            self.assertEqual(result["reconciliation"]["status"], "ok")
            self.assertIn("transaction", result)
            self.assertIn("Keep the loop quiesced", result["recovery"])
            backup = Path(result["backup_dir"])
            self.assertTrue(backup.is_dir())
            self.assertEqual((backup / "loop_state.json").read_bytes(), legacy_state)
            for filename in (
                gf.GOAL_CONTRACT_FILE,
                gf.APPROACH_REGISTRY_FILE,
                gf.CURRENT_PLAN_FILE,
            ):
                self.assertTrue((root / filename).is_file(), filename)

    def test_migration_reports_backup_verification_failure_after_durable_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._legacy(
                root,
                path_campaign="A3",
                recovery_campaign="A3",
                ledger_campaign="A3",
            )
            with mock.patch.object(
                gf,
                "_verify_migration_backup",
                side_effect=ValueError("simulated backup mismatch"),
            ):
                result = _apply_migration(root)

            self.assertEqual(
                result["status"], "post_apply_backup_verification_failed"
            )
            self.assertFalse(result["applied"])
            self.assertTrue(result["authority_written"])
            self.assertTrue(result["recovery_required"])
            self.assertTrue(Path(result["backup_manifest"]).is_file())
            self.assertIn("transaction", result)

    def test_migration_reports_projection_reconcile_failure_after_durable_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._legacy(
                root,
                path_campaign="A3",
                recovery_campaign="A3",
                ledger_campaign="A3",
            )
            with mock.patch.object(
                gf,
                "reconcile_goal_focus",
                side_effect=ValueError("simulated projection failure"),
            ):
                result = _apply_migration(root)

            self.assertEqual(result["status"], "post_apply_reconcile_failed")
            self.assertFalse(result["applied"])
            self.assertTrue(result["authority_written"])
            self.assertTrue(result["recovery_required"])
            self.assertTrue(Path(result["backup_manifest"]).is_file())
            self.assertIn("validation", result)

    def test_interrupted_migration_transaction_recovers_to_one_valid_authority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._legacy(
                root,
                path_campaign="A3",
                recovery_campaign="A3",
                ledger_campaign="A3",
            )
            source_before = _snapshot_files(
                root, list(gf.LEGACY_MIGRATION_SOURCE_FILES)
            )
            real_commit = gf.commit_transaction

            def crash_after_apply(*args: object, **kwargs: object) -> dict:
                kwargs["crash_after"] = "after_apply"
                return real_commit(*args, **kwargs)

            with mock.patch.object(
                gf, "commit_transaction", side_effect=crash_after_apply
            ), self.assertRaises(st.InjectedCrash):
                _apply_migration(root)

            recovered = st.recover_transactions(root)
            self.assertEqual(len(recovered), 1)
            self.assertEqual(recovered[0]["status"], "recovered")
            gf.reconcile_goal_focus(root, apply=True)
            validation = gf.validate_goal_focus(root, require_enabled=True)
            self.assertEqual(validation["errors"], [], validation)
            decisions = gf.load_direction_decisions(root)
            self.assertEqual(len(decisions), 1)
            metadata = decisions[0]["migration_backup"]
            manifest = root / str(metadata["manifest_relative_path"])
            self.assertTrue(manifest.is_file())
            self.assertEqual(
                hashlib.sha256(manifest.read_bytes()).hexdigest(),
                metadata["manifest_sha256"],
            )
            for source in metadata["sources"]:
                source_name = str(source["source_path"])
                original = source_before[source_name]
                self.assertIs(source["present"], original is not None)
                if original is not None:
                    backup_path = root / str(source["backup_relative_path"])
                    self.assertEqual(backup_path.read_bytes(), original)


class GoalFocusCandidateTests(_AttestedGoalFocusTestCase):
    @staticmethod
    def _resource_attestation_case(case: str) -> dict | None:
        if case == "missing":
            return None
        attestation = _provider_resource_attestation()
        if case == "tampered":
            attestation["scope_unit"] = "attacker-selected.scope"
        elif case == "capture_verified_false":
            attestation["capture_verified"] = False
        elif case == "timed_out_true":
            attestation["timed_out"] = True
        elif case == "oversized_output_true":
            attestation["oversized_output"] = True
        elif case == "sensitive_output_blocked_true":
            attestation["sensitive_output_blocked"] = True
        else:  # pragma: no cover - test table is closed below
            raise AssertionError(f"unknown resource-attestation case: {case}")
        return attestation

    def test_enforce_stage_requires_verified_primary_resource_attestation(self) -> None:
        cases = (
            "missing",
            "tampered",
            "capture_verified_false",
            "timed_out_true",
            "oversized_output_true",
            "sensitive_output_blocked_true",
        )
        protected_names = [
            "budget.json",
            "iterations.jsonl",
            gf.CURRENT_PLAN_FILE,
            gf.GOAL_CONTRACT_FILE,
            gf.APPROACH_REGISTRY_FILE,
        ]
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _initialize(root)
                plan = _activate(root)
                before = _snapshot_files(root, protected_names)

                with self.assertRaises((ValueError, st.RevisionConflict)):
                    _stage_enforced_candidate(
                        root,
                        plan,
                        {"output": "must not become a pending result"},
                        host_resource_attestation=self._resource_attestation_case(
                            case
                        ),
                    )

                self.assertEqual(_snapshot_files(root, protected_names), before)
                self.assertIsNone(gf.load_pending_candidate(root))
                self.assertTrue((root / gf.ITERATION_DISPATCH_FILE).is_file())
                self.assertEqual(
                    (root / "iterations.jsonl").read_text(encoding="utf-8"),
                    "",
                )

    def test_resource_attestation_tampering_cannot_be_banked(self) -> None:
        cases = (
            "missing",
            "tampered",
            "capture_verified_false",
            "timed_out_true",
            "oversized_output_true",
            "sensitive_output_blocked_true",
        )
        protected_names = [
            "loop_state.json",
            "budget.json",
            "iterations.jsonl",
            gf.CURRENT_PLAN_FILE,
            gf.GOAL_CONTRACT_FILE,
            gf.APPROACH_REGISTRY_FILE,
        ]
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _initialize(root)
                plan = _activate(root)
                _stage_enforced_candidate(
                    root,
                    plan,
                    {"output": "candidate whose resource proof is later changed"},
                )
                candidate_path = root / gf.PENDING_CANDIDATE_FILE
                changed = json.loads(candidate_path.read_text(encoding="utf-8"))
                host_attestation = changed["host_execution_attestation"]
                if case == "missing":
                    host_attestation.pop("resource_attestation", None)
                else:
                    host_attestation["resource_attestation"] = (
                        self._resource_attestation_case(case)
                    )
                _write_json(candidate_path, changed)
                changed_bytes = candidate_path.read_bytes()
                before = _snapshot_files(root, protected_names)
                review = _bound_review({"candidate": changed})

                with self.assertRaises((ValueError, st.RevisionConflict)):
                    gf.finalize_candidate(root, accepted=True, review=review)

                self.assertEqual(_snapshot_files(root, protected_names), before)
                self.assertEqual(candidate_path.read_bytes(), changed_bytes)
                self.assertEqual(
                    (root / "iterations.jsonl").read_text(encoding="utf-8"),
                    "",
                )

    def test_compute_execution_rejects_malformed_or_invented_provenance(self) -> None:
        plan = {
            "compute_policy": {
                "allowed_services": ["hetzner"],
                "forbidden_services": ["kaggle"],
            }
        }
        valid = {
            "recording_status": "explicit",
            "usage": "hetzner",
            "services": [
                {
                    "service": "hetzner",
                    "status": "succeeded",
                    "job_ref": "server-42",
                    "started_at": "2026-07-29T10:00:00Z",
                    "finished_at": "2026-07-29T10:01:00Z",
                    "duration_seconds": 60,
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            accepted = gf.validate_compute_execution(
                Path(tmp), valid, _plan=plan
            )
            self.assertEqual(accepted["used_services"], ["hetzner"])
            self.assertEqual(accepted["recording_status"], "explicit")

            invalid = (
                {"services": []},
                {"recording_status": "bogus", "usage": "none", "services": []},
                {"recording_status": "explicit", "usage": "none", "services": [{}]},
                {
                    "recording_status": "explicit",
                    "usage": "hetzner",
                    "services": [{"service": "hetzner", "status": "bogus"}],
                },
                {
                    "recording_status": "explicit",
                    "usage": "hetzner",
                    "services": [
                        {
                            "service": "hetzner",
                            "status": "succeeded",
                            "duration_seconds": -3,
                        }
                    ],
                },
                {
                    "recording_status": "explicit",
                    "usage": "hetzner",
                    "services": [
                        {
                            "service": "hetzner",
                            "status": "succeeded",
                            "started_at": "2026-07-29T10:02:00Z",
                            "finished_at": "2026-07-29T10:01:00Z",
                        }
                    ],
                },
                {
                    "recording_status": "explicit",
                    "usage": "hetzner",
                    "services": [{"service": True, "status": "unknown"}],
                },
                {
                    "recording_status": "explicit",
                    "usage": "hetzner",
                    "services": [
                        {"service": "hetzner", "status": "unknown", "job_ref": 42}
                    ],
                },
                {
                    "recording_status": "explicit",
                    "usage": "hetzner",
                    "services": [
                        {
                            "service": "hetzner",
                            "status": "unknown",
                            "duration_seconds": "3",
                        }
                    ],
                },
                {
                    "recording_status": "unreported",
                    "usage": "unknown",
                    "services": [{"service": "hetzner", "status": "unknown"}],
                },
                {
                    "recording_status": "explicit",
                    "usage": "none",
                    "services": [{"service": "hetzner", "status": "unknown"}],
                },
            )
            for compute in invalid:
                with self.subTest(compute=compute), self.assertRaises(ValueError):
                    gf.validate_compute_execution(Path(tmp), compute, _plan=plan)

    def test_enforce_stage_captures_exact_evidence_and_replaces_worker_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize(root)
            plan = _activate(root)
            dispatch = gf.prepare_iteration_dispatch(
                root,
                executor_provider="codex",
                executor_family="openai",
                executor_attestation=_provider_attestation("codex", root),
                started_at="2026-07-29T00:00:00Z",
            )["dispatch"]
            payload = "complete proof artifact: π\n".encode("utf-8")
            _dispatch_evidence_path(root, dispatch, "proof.txt").write_bytes(payload)
            staged = gf.stage_iteration_candidate(
                root,
                {
                    "candidate_id": dispatch["candidate_id"],
                    "claim_ids": ["CLAIM-EXACT"],
                    "evidence_ids": ["proof.txt"],
                    "evidence_artifacts": [
                        {
                            "evidence_id": "invented.txt",
                            "content": "worker-controlled manifest",
                        }
                    ],
                    "execution": {
                        "executor_provider": "codex",
                        "compute": {
                            "recording_status": "explicit",
                            "usage": "none",
                            "services": [],
                        },
                    },
                    "goal_focus": {
                        "plan_revision": plan["plan_revision"],
                        "campaign_id": plan["campaign_id"],
                        "approach_id": plan["approach_id"],
                    },
                },
                plan["plan_revision"],
                expected_dispatch_id=dispatch["dispatch_id"],
                host_resource_attestation=_provider_resource_attestation(),
            )
            artifacts = staged["candidate"]["record"]["evidence_artifacts"]
            self.assertEqual(len(artifacts), 1)
            self.assertEqual(
                artifacts[0],
                {
                    "schema_version": gf.EVIDENCE_ARTIFACT_SCHEMA,
                    "evidence_id": "proof.txt",
                    "source_path": Path(
                        dispatch["evidence_root"], "proof.txt"
                    ).as_posix(),
                    "sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
                    "size_bytes": len(payload),
                    "content_encoding": "utf-8",
                    "content": payload.decode("utf-8"),
                },
            )
            self.assertNotIn("invented.txt", json.dumps(staged["candidate"]))

    @unittest.skipUnless(os.name == "posix", "no-follow evidence checks require POSIX")
    def test_enforce_stage_rejects_unsafe_or_unreviewable_evidence(self) -> None:
        cases = (
            "traversal",
            "path_like",
            "control_state",
            "windows_rooted",
            "windows_drive_relative",
            "windows_drive_rooted",
            "windows_unc",
            "hidden_env",
            "credentials_name",
            "key_suffix",
            "symlink_leaf",
            "symlink_candidate_dir",
            "hardlink_leaf",
            "wrong_candidate_dir",
            "directory",
            "invalid_utf8",
            "oversized",
            "secret_content",
        )
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _initialize(root)
                plan = _activate(root)
                dispatch = gf.prepare_iteration_dispatch(
                    root,
                    executor_provider="codex",
                    executor_family="openai",
                    executor_attestation=_provider_attestation("codex", root),
                    started_at="2026-07-29T00:00:00Z",
                )["dispatch"]
                evidence_root = root / dispatch["evidence_root"]
                if case == "traversal":
                    evidence_id = "../outside.txt"
                elif case == "path_like":
                    evidence_id = "nested/proof.txt"
                elif case == "control_state":
                    evidence_id = gf.CURRENT_PLAN_FILE
                elif case == "windows_rooted":
                    evidence_id = r"\escape.txt"
                elif case == "windows_drive_relative":
                    evidence_id = r"C:escape.txt"
                elif case == "windows_drive_rooted":
                    evidence_id = r"C:\escape.txt"
                elif case == "windows_unc":
                    evidence_id = r"\\server\share\escape.txt"
                elif case == "hidden_env":
                    evidence_id = ".env"
                elif case == "credentials_name":
                    evidence_id = "credentials.json"
                elif case == "key_suffix":
                    evidence_id = "signing.key"
                elif case == "symlink_leaf":
                    outside = root / "real-evidence.txt"
                    outside.write_text("outside", encoding="utf-8")
                    evidence_id = "evidence.txt"
                    (evidence_root / evidence_id).symlink_to(outside)
                elif case == "symlink_candidate_dir":
                    outside_dir = root / "real-evidence-dir"
                    outside_dir.mkdir()
                    (outside_dir / "proof.txt").write_text("outside", encoding="utf-8")
                    evidence_root.rmdir()
                    evidence_root.symlink_to(outside_dir, target_is_directory=True)
                    evidence_id = "proof.txt"
                elif case == "hardlink_leaf":
                    outside = root / "hardlinked-evidence.txt"
                    outside.write_text("outside", encoding="utf-8")
                    evidence_id = "proof.txt"
                    os.link(outside, evidence_root / evidence_id)
                elif case == "wrong_candidate_dir":
                    evidence_id = "proof.txt"
                    wrong_root = evidence_root.parent / "wrong-candidate"
                    wrong_root.mkdir()
                    (wrong_root / evidence_id).write_text(
                        "wrong candidate evidence", encoding="utf-8"
                    )
                elif case == "directory":
                    evidence_id = "evidence-directory"
                    (evidence_root / evidence_id).mkdir()
                elif case == "invalid_utf8":
                    evidence_id = "invalid.bin"
                    (evidence_root / evidence_id).write_bytes(b"\xff\xfe")
                elif case == "oversized":
                    evidence_id = "too-large.txt"
                    (evidence_root / evidence_id).write_bytes(
                        b"x" * (gf.MAX_EVIDENCE_ARTIFACT_BYTES + 1)
                    )
                else:
                    evidence_id = "secret-shaped.txt"
                    (evidence_root / evidence_id).write_text(
                        "credential sk-abcdefghijklmnop must not cross review",
                        encoding="utf-8",
                    )
                record = {
                    "candidate_id": dispatch["candidate_id"],
                    "claim_ids": ["CLAIM-UNSAFE"],
                    "evidence_ids": [evidence_id],
                    "execution": {
                        "executor_provider": "codex",
                        "compute": {
                            "recording_status": "explicit",
                            "usage": "none",
                            "services": [],
                        },
                    },
                    "goal_focus": {
                        "plan_revision": plan["plan_revision"],
                        "campaign_id": plan["campaign_id"],
                        "approach_id": plan["approach_id"],
                    },
                }
                with self.assertRaises((ValueError, OSError, st.RevisionConflict)):
                    gf.stage_iteration_candidate(
                        root,
                        record,
                        plan["plan_revision"],
                        expected_dispatch_id=dispatch["dispatch_id"],
                    )
                self.assertIsNone(gf.load_pending_candidate(root))
                self.assertTrue((root / gf.ITERATION_DISPATCH_FILE).exists())

    def test_enforce_stage_rejects_aggregate_evidence_overflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize(root)
            plan = _activate(root)
            dispatch = gf.prepare_iteration_dispatch(
                root,
                executor_provider="codex",
                executor_family="openai",
                executor_attestation=_provider_attestation("codex", root),
                started_at="2026-07-29T00:00:00Z",
            )["dispatch"]
            evidence_ids = [f"aggregate-{index}.txt" for index in range(4)]
            per_artifact = gf.MAX_EVIDENCE_TOTAL_BYTES // len(evidence_ids) + 1
            self.assertLessEqual(per_artifact, gf.MAX_EVIDENCE_ARTIFACT_BYTES)
            for evidence_id in evidence_ids:
                _dispatch_evidence_path(root, dispatch, evidence_id).write_bytes(
                    b"x" * per_artifact
                )

            with self.assertRaisesRegex(ValueError, "total bytes"):
                gf.stage_iteration_candidate(
                    root,
                    {
                        "candidate_id": dispatch["candidate_id"],
                        "claim_ids": ["CLAIM-AGGREGATE"],
                        "evidence_ids": evidence_ids,
                        "execution": {
                            "executor_provider": "codex",
                            "compute": {
                                "recording_status": "explicit",
                                "usage": "none",
                                "services": [],
                            },
                        },
                        "goal_focus": {
                            "plan_revision": plan["plan_revision"],
                            "campaign_id": plan["campaign_id"],
                            "approach_id": plan["approach_id"],
                        },
                    },
                    plan["plan_revision"],
                    expected_dispatch_id=dispatch["dispatch_id"],
                )

            self.assertIsNone(gf.load_pending_candidate(root))
            self.assertTrue((root / gf.ITERATION_DISPATCH_FILE).is_file())

    def test_enforce_stage_rejects_pii_before_candidate_persistence(self) -> None:
        pii_cases = {
            "email": "Contact email: alice.research" + chr(64) + "example.org",
            "phone": "Contact phone: +1 (415) 555-0123",
            "participant_id": "Participant ID: PART-2048",
            "patient_id": "Patient ID: MRN-8675309",
            "subject_id": "Subject ID: SUBJ-0042",
            "participant_name": "Participant name: Alice Example",
            "patient_name": "Patient name: Bob Example",
            "subject_name": "Subject name: Carol Example",
            "participant_address": "Participant address: 123 Main Street, Springfield",
            "patient_address": "Patient address: 456 Oak Avenue, Springfield",
            "subject_address": "Subject address: 789 Pine Road, Springfield",
            "participant_dob": "Participant DOB: 1990-01-02",
            "patient_dob": "Patient date of birth: 1985-03-04",
            "subject_dob": "Subject birth date: 1979-05-06",
        }
        for case, content in pii_cases.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _initialize(root)
                plan = _activate(root)
                dispatch = gf.prepare_iteration_dispatch(
                    root,
                    executor_provider="codex",
                    executor_family="openai",
                    executor_attestation=_provider_attestation("codex", root),
                    started_at="2026-07-29T00:00:00Z",
                )["dispatch"]
                evidence_id = f"{case}.txt"
                _dispatch_evidence_path(root, dispatch, evidence_id).write_text(
                    content,
                    encoding="utf-8",
                )
                record = {
                    "candidate_id": dispatch["candidate_id"],
                    "claim_ids": ["CLAIM-PII"],
                    "evidence_ids": [evidence_id],
                    "execution": {
                        "executor_provider": "codex",
                        "compute": {
                            "recording_status": "explicit",
                            "usage": "none",
                            "services": [],
                        },
                    },
                    "goal_focus": {
                        "plan_revision": plan["plan_revision"],
                        "campaign_id": plan["campaign_id"],
                        "approach_id": plan["approach_id"],
                    },
                }

                with self.assertRaises(ValueError):
                    gf.stage_iteration_candidate(
                        root,
                        record,
                        plan["plan_revision"],
                        expected_dispatch_id=dispatch["dispatch_id"],
                    )

                self.assertFalse((root / gf.PENDING_CANDIDATE_FILE).exists())
                self.assertIsNone(gf.load_pending_candidate(root))
                self.assertTrue((root / gf.ITERATION_DISPATCH_FILE).is_file())

    def test_tampered_embedded_evidence_metadata_cannot_be_banked(self) -> None:
        cases = (
            "content",
            "schema_version",
            "evidence_id",
            "source_path",
            "size_bytes",
            "sha256",
            "content_encoding",
            "missing_artifact",
            "unexpected_artifact",
            "duplicate_artifact",
        )
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _initialize(root)
                plan = _activate(root)
                staged = _stage_enforced_candidate(
                    root,
                    plan,
                    {
                        "claim_ids": ["CLAIM-TAMPER"],
                        "evidence_ids": ["proof.txt"],
                    },
                )
                candidate_path = root / gf.PENDING_CANDIDATE_FILE
                tampered = json.loads(candidate_path.read_text(encoding="utf-8"))
                artifacts = tampered["record"]["evidence_artifacts"]
                artifact = artifacts[0]
                if case == "content":
                    artifact["content"] += "forged"
                elif case == "schema_version":
                    artifact["schema_version"] = "attacker-schema.v9"
                elif case == "evidence_id":
                    artifact["evidence_id"] = "other-proof.txt"
                elif case == "source_path":
                    artifact["source_path"] = "other-proof.txt"
                elif case == "size_bytes":
                    artifact["size_bytes"] += 1
                elif case == "sha256":
                    artifact["sha256"] = "sha256:" + "0" * 64
                elif case == "content_encoding":
                    artifact["content_encoding"] = "base64"
                elif case == "missing_artifact":
                    artifacts.clear()
                elif case == "unexpected_artifact":
                    extra = copy.deepcopy(artifact)
                    extra["evidence_id"] = "unexpected-proof.txt"
                    extra["source_path"] = "unexpected-proof.txt"
                    artifacts.append(extra)
                else:
                    artifacts.append(copy.deepcopy(artifact))
                _write_json(candidate_path, tampered)
                before_budget = (root / "budget.json").read_bytes()
                before_ledger = (root / "iterations.jsonl").read_bytes()

                with self.assertRaisesRegex(
                    ValueError,
                    "evidence artifact|duplicate evidence|artifact set|host-snapshotted",
                ):
                    gf.finalize_candidate(
                        root,
                        accepted=True,
                        review=_bound_review({"candidate": tampered}),
                    )

                self.assertEqual((root / "budget.json").read_bytes(), before_budget)
                self.assertEqual((root / "iterations.jsonl").read_bytes(), before_ledger)

    def test_finalize_requires_valid_schema_and_inspected_evidence(self) -> None:
        for case in ("ad_hoc_pass", "uninspected_reference"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _initialize(root)
                plan = _activate(root)
                staged = _stage_enforced_candidate(root, plan, {"output": "candidate"})
                review = _bound_review(staged)
                if case == "ad_hoc_pass":
                    review.pop("schema_version")
                    pattern = "must validate as result_review"
                else:
                    review["provider_reviews"]["claude"]["inspected_paths"] = [
                        gf.PENDING_CANDIDATE_FILE
                    ]
                    pattern = "lacks inspected staged evidence"
                with self.assertRaisesRegex(ValueError, pattern):
                    gf.finalize_candidate(root, accepted=True, review=review)
                self.assertTrue((root / gf.PENDING_CANDIDATE_FILE).exists())
                self.assertEqual((root / "iterations.jsonl").read_text(encoding="utf-8"), "")

    def test_final_banking_revalidates_embedded_provider_reviews(self) -> None:
        cases = (
            "candidate_id",
            "candidate_fingerprint",
            "claim_evidence",
            "verdict",
            "provider_set",
            "same_family_provider",
            "missing_provider_reviews",
        )
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _initialize(root)
                plan = _activate(root)
                staged = _stage_enforced_candidate(
                    root,
                    plan,
                    {
                        "claim_ids": ["CLAIM-RAW-REVIEW"],
                        "evidence_ids": ["raw-review-proof.txt"],
                    },
                )
                review = _bound_review(staged)
                provider_review = review["provider_reviews"]["claude"]
                if case == "candidate_id":
                    provider_review["candidate_id"] = "attacker-candidate"
                elif case == "candidate_fingerprint":
                    provider_review["candidate_fingerprint"] = "sha256:" + "0" * 64
                elif case == "claim_evidence":
                    provider_review["claim_reviews"][0]["evidence_refs"] = [
                        "attacker-evidence.txt"
                    ]
                elif case == "verdict":
                    provider_review["verdict"] = "fail"
                    provider_review["safe_to_bank"] = False
                    provider_review["claim_reviews"][0]["status"] = "unsupported"
                elif case == "provider_set":
                    review["providers"].append("grok")
                elif case == "same_family_provider":
                    review["providers"] = ["codex"]
                    review["different_family_providers"] = ["codex"]
                    review["provider_reviews"] = {"codex": provider_review}
                else:
                    review["providers"] = []
                    review["different_family_providers"] = []
                    review["provider_reviews"] = {}
                before = _snapshot_files(
                    root,
                    [
                        "budget.json",
                        "iterations.jsonl",
                        gf.PENDING_CANDIDATE_FILE,
                        gf.GOAL_CONTRACT_FILE,
                        gf.APPROACH_REGISTRY_FILE,
                        gf.CURRENT_PLAN_FILE,
                    ],
                )

                with self.assertRaises(ValueError):
                    gf.finalize_candidate(root, accepted=True, review=review)

                self.assertEqual(
                    _snapshot_files(
                        root,
                        [
                            "budget.json",
                            "iterations.jsonl",
                            gf.PENDING_CANDIDATE_FILE,
                            gf.GOAL_CONTRACT_FILE,
                            gf.APPROACH_REGISTRY_FILE,
                            gf.CURRENT_PLAN_FILE,
                        ],
                    ),
                    before,
                )

    def test_host_attested_candidate_rejects_direct_result_review_v1(self) -> None:
        for reviewer_provider in ("claude", "codex"):
            with self.subTest(
                reviewer_provider=reviewer_provider
            ), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _initialize(root)
                plan = _activate(root)
                staged = _stage_enforced_candidate(
                    root,
                    plan,
                    {
                        "claim_ids": ["CLAIM-DIRECT-V1"],
                        "evidence_ids": ["direct-v1-proof.txt"],
                    },
                )
                before = _snapshot_files(
                    root,
                    ["budget.json", "iterations.jsonl", gf.PENDING_CANDIDATE_FILE],
                )

                with self.assertRaisesRegex(
                    ValueError, "result_review_summary|direct result_review"
                ):
                    gf.finalize_candidate(
                        root,
                        accepted=True,
                        review=_bound_provider_review(
                            staged, reviewer_provider=reviewer_provider
                        ),
                    )

                self.assertEqual(
                    _snapshot_files(
                        root,
                        [
                            "budget.json",
                            "iterations.jsonl",
                            gf.PENDING_CANDIDATE_FILE,
                        ],
                    ),
                    before,
                )

    @unittest.skipUnless(os.name == "posix", "descriptor no-follow checks require POSIX")
    def test_authority_swap_to_symlink_never_uses_path_read_bytes(self) -> None:
        for operation in ("prepare", "cancel", "finalize"):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _initialize(root)
                plan = _activate(root)
                outside = root / "attacker-selected.txt"
                outside_payload = b"attacker-selected bytes must never be read or changed\n"
                outside.write_bytes(outside_payload)
                review: dict | None = None
                if operation == "prepare":
                    target = root / gf.CURRENT_PLAN_FILE
                    protected_before = (root / gf.DIRECTION_DECISIONS_FILE).read_bytes()
                elif operation == "cancel":
                    prepared = gf.prepare_iteration_dispatch(
                        root,
                        executor_provider="codex",
                        executor_family="openai",
                        executor_attestation=_provider_attestation("codex", root),
                        started_at="2026-07-29T00:00:00Z",
                    )
                    target = root / gf.ITERATION_DISPATCH_FILE
                    protected_before = (
                        root / gf.DIRECTION_DECISIONS_FILE
                    ).read_bytes()
                else:
                    staged = _stage_enforced_candidate(
                        root, plan, {"output": "candidate"}
                    )
                    review = _bound_review(staged)
                    target = root / gf.PENDING_CANDIDATE_FILE
                    protected_before = (root / "budget.json").read_bytes()
                real_commit = gf.commit_transaction
                swapped = False

                def swap_then_commit(*args: object, **kwargs: object) -> dict:
                    nonlocal swapped
                    if not swapped:
                        target.unlink()
                        target.symlink_to(outside)
                        swapped = True
                    return real_commit(*args, **kwargs)

                with mock.patch.object(
                    gf, "commit_transaction", side_effect=swap_then_commit
                ), mock.patch.object(
                    Path,
                    "read_bytes",
                    side_effect=AssertionError("unsafe Path.read_bytes followed authority"),
                ):
                    with self.assertRaises(
                        (st.TransactionError, st.RevisionConflict, OSError, ValueError)
                    ):
                        if operation == "prepare":
                            gf.prepare_iteration_dispatch(
                                root,
                                executor_provider="codex",
                                executor_family="openai",
                                executor_attestation=_provider_attestation("codex", root),
                                started_at="2026-07-29T00:00:00Z",
                            )
                        elif operation == "cancel":
                            gf.cancel_iteration_dispatch(
                                root,
                                dispatch_id=prepared["dispatch"]["dispatch_id"],
                                reason="test swap",
                            )
                        else:
                            gf.finalize_candidate(
                                root,
                                accepted=True,
                                review=review or {},
                            )
                self.assertTrue(swapped)
                self.assertEqual(outside.read_bytes(), outside_payload)
                if operation == "prepare":
                    self.assertFalse((root / gf.ITERATION_DISPATCH_FILE).exists())
                    self.assertEqual(
                        (root / gf.DIRECTION_DECISIONS_FILE).read_bytes(),
                        protected_before,
                    )
                elif operation == "cancel":
                    self.assertEqual(
                        (root / gf.DIRECTION_DECISIONS_FILE).read_bytes(),
                        protected_before,
                    )
                else:
                    self.assertEqual((root / "budget.json").read_bytes(), protected_before)
                    self.assertEqual((root / "iterations.jsonl").read_text(encoding="utf-8"), "")

    def test_host_derives_terminal_state_from_reviewed_goal_obligations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize(root)
            plan = _activate(root)
            staged = _stage_enforced_candidate(
                root,
                plan,
                {
                    "decision": "stop",
                    "stop_reason": "proof_found",
                    "output": "A useful lemma, but not the theorem.",
                    "evidence_ids": ["lemma.json"],
                    "budget_delta": {},
                },
            )
            result = gf.finalize_candidate(
                root,
                accepted=True,
                review=_bound_review(staged),
            )
            self.assertEqual(result["record"]["decision"], "revise")
            self.assertEqual(
                result["record"]["stop_reason"],
                "terminal_claim_not_supported_by_goal_obligations",
            )
            self.assertEqual(json.loads((root / "loop_state.json").read_text(encoding="utf-8"))["status"], "running")

    def test_host_dispatch_is_pinned_and_consumed_atomically_by_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize(root)
            plan = _activate(root)
            prepared = gf.prepare_iteration_dispatch(
                root,
                executor_provider="codex",
                executor_family="openai",
                executor_attestation=_provider_attestation("codex", root),
                started_at="2026-07-29T00:00:00Z",
                driver_pid=123,
            )
            dispatch = prepared["dispatch"]
            record = {
                "candidate_id": dispatch["candidate_id"],
                "claim_ids": ["CLAIM-1"],
                "evidence_checked": {
                    "claim_ids": ["CLAIM-1"],
                    "evidence_ids": ["EVIDENCE-1"],
                },
                "goal_focus": {
                    "plan_revision": plan["plan_revision"],
                    "campaign_id": plan["campaign_id"],
                    "approach_id": plan["approach_id"],
                },
                "execution": {
                    "executor_provider": "claude",
                    "compute": {
                        "recording_status": "explicit",
                        "usage": "none",
                        "services": [],
                    },
                },
            }
            _dispatch_evidence_path(root, dispatch, "EVIDENCE-1").write_text(
                "host-dispatch evidence\n", encoding="utf-8"
            )
            with self.assertRaises(st.RevisionConflict):
                gf.stage_iteration_candidate(
                    root,
                    record,
                    plan["plan_revision"],
                    expected_dispatch_id="wrong",
                )
            staged = gf.stage_iteration_candidate(
                root,
                record,
                plan["plan_revision"],
                expected_dispatch_id=dispatch["dispatch_id"],
                host_resource_attestation=_provider_resource_attestation(),
            )["candidate"]
            self.assertFalse((root / gf.ITERATION_DISPATCH_FILE).exists())
            self.assertEqual(
                staged["host_execution_attestation"]["source"], "host_dispatch"
            )
            self.assertEqual(
                staged["record"]["execution"]["executor_provider"], "codex"
            )
            gf.validate_host_staged_candidate(root, staged)

    def test_missing_declared_evidence_cannot_be_staged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize(root)
            plan = _activate(root)
            prepared = gf.prepare_iteration_dispatch(
                root,
                executor_provider="codex",
                executor_family="openai",
                executor_attestation=_provider_attestation("codex", root),
                started_at="2026-07-29T00:00:00Z",
            )
            dispatch = prepared["dispatch"]
            record = {
                "candidate_id": dispatch["candidate_id"],
                "claim_ids": ["CLAIM-1"],
                "evidence_checked": {
                    "claim_ids": ["CLAIM-1"],
                    "evidence_ids": ["missing.txt"],
                },
                "goal_focus": {
                    "plan_revision": plan["plan_revision"],
                    "campaign_id": plan["campaign_id"],
                    "approach_id": plan["approach_id"],
                },
                "execution": {
                    "executor_provider": "codex",
                    "compute": {
                        "recording_status": "explicit",
                        "usage": "none",
                        "services": [],
                    },
                },
            }

            with self.assertRaisesRegex(ValueError, "does not exist"):
                gf.stage_iteration_candidate(
                    root,
                    record,
                    plan["plan_revision"],
                    expected_dispatch_id=dispatch["dispatch_id"],
                )

            self.assertTrue((root / gf.ITERATION_DISPATCH_FILE).is_file())
            self.assertFalse((root / gf.PENDING_CANDIDATE_FILE).exists())

    def test_dispatch_family_must_match_reviewed_primary_family(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize(root)
            _activate(root)
            with self.assertRaises(st.RevisionConflict):
                gf.prepare_iteration_dispatch(
                    root,
                    executor_provider="claude",
                    executor_family="anthropic",
                    executor_attestation=_provider_attestation("claude", root),
                    started_at="2026-07-29T00:00:00Z",
                )

    def test_dispatch_rejects_changed_sibling_provider_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dependency = (
                self.provider_fixture.paths["codex"].parent / "provider-runtime.dat"
            )
            dependency.write_bytes(b"trusted dependency version 1\n")
            if os.name == "posix":
                dependency.chmod(0o600)
            executable_bytes = self.provider_fixture.paths["codex"].read_bytes()

            _initialize(root)
            plan = _activate(root)
            reviewed_attestation = copy.deepcopy(
                plan["dispatch_provider_attestation"]
            )
            dependency.write_bytes(b"mutated dependency version 2\n")

            self.assertEqual(
                self.provider_fixture.paths["codex"].read_bytes(), executable_bytes
            )
            with self.assertRaises((ValueError, st.RevisionConflict)):
                gf.prepare_iteration_dispatch(
                    root,
                    executor_provider="codex",
                    executor_family="openai",
                    executor_attestation=reviewed_attestation,
                    started_at="2026-07-29T00:00:00Z",
                )

            self.assertFalse((root / gf.ITERATION_DISPATCH_FILE).exists())

    def test_missing_or_tampered_executable_attestations_fail_closed(self) -> None:
        for mutation in ("missing", "tampered"):
            with self.subTest(boundary="plan", mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _initialize(root)
                _activate(root)
                plan_path = root / gf.CURRENT_PLAN_FILE
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
                if mutation == "missing":
                    plan.pop("dispatch_provider_attestation", None)
                else:
                    plan["dispatch_provider_attestation"]["executable_sha256"] = (
                        "sha256:" + "0" * 64
                    )
                _write_json(plan_path, plan)
                tampered_plan = plan_path.read_bytes()

                with self.assertRaises((ValueError, st.RevisionConflict)):
                    gf.prepare_iteration_dispatch(
                        root,
                        executor_provider="codex",
                        executor_family="openai",
                        executor_attestation=_provider_attestation("codex", root),
                        started_at="2026-07-29T00:00:00Z",
                    )

                self.assertEqual(plan_path.read_bytes(), tampered_plan)
                self.assertFalse((root / gf.ITERATION_DISPATCH_FILE).exists())

            with self.subTest(boundary="dispatch", mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _initialize(root)
                plan = _activate(root)
                dispatch = gf.prepare_iteration_dispatch(
                    root,
                    executor_provider="codex",
                    executor_family="openai",
                    executor_attestation=_provider_attestation("codex", root),
                    started_at="2026-07-29T00:00:00Z",
                )["dispatch"]
                _dispatch_evidence_path(root, dispatch, "proof.txt").write_text(
                    "reviewable proof evidence\n", encoding="utf-8"
                )
                dispatch_path = root / gf.ITERATION_DISPATCH_FILE
                changed = json.loads(dispatch_path.read_text(encoding="utf-8"))
                if mutation == "missing":
                    changed.pop("executor_attestation", None)
                else:
                    changed["executor_attestation"]["file_identity"]["inode"] += 1
                _write_json(dispatch_path, changed)
                changed_bytes = dispatch_path.read_bytes()

                with self.assertRaises((ValueError, st.RevisionConflict)):
                    gf.stage_iteration_candidate(
                        root,
                        {
                            "candidate_id": dispatch["candidate_id"],
                            "claim_ids": ["CLAIM-ATTESTATION"],
                            "evidence_ids": ["proof.txt"],
                            "execution": {
                                "executor_provider": "codex",
                                "compute": {
                                    "recording_status": "explicit",
                                    "usage": "none",
                                    "services": [],
                                },
                            },
                            "goal_focus": {
                                "plan_revision": plan["plan_revision"],
                                "campaign_id": plan["campaign_id"],
                                "approach_id": plan["approach_id"],
                            },
                        },
                        plan["plan_revision"],
                        expected_dispatch_id=dispatch["dispatch_id"],
                    )

                self.assertEqual(dispatch_path.read_bytes(), changed_bytes)
                self.assertFalse((root / gf.PENDING_CANDIDATE_FILE).exists())

            with self.subTest(boundary="candidate", mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _initialize(root)
                plan = _activate(root)
                staged = _stage_enforced_candidate(root, plan, {"output": "result"})
                review = _bound_review(staged)
                candidate_path = root / gf.PENDING_CANDIDATE_FILE
                changed = json.loads(candidate_path.read_text(encoding="utf-8"))
                if mutation == "missing":
                    changed["host_execution_attestation"].pop(
                        "executor_attestation", None
                    )
                else:
                    changed["host_execution_attestation"]["executor_attestation"][
                        "executable_sha256"
                    ] = "sha256:" + "0" * 64
                _write_json(candidate_path, changed)
                protected = _snapshot_files(
                    root,
                    ["budget.json", "iterations.jsonl", gf.CURRENT_PLAN_FILE],
                )

                with self.assertRaises((ValueError, st.RevisionConflict)):
                    gf.finalize_candidate(root, accepted=True, review=review)

                self.assertEqual(
                    _snapshot_files(
                        root,
                        ["budget.json", "iterations.jsonl", gf.CURRENT_PLAN_FILE],
                    ),
                    protected,
                )
                self.assertTrue(candidate_path.is_file())

            with self.subTest(boundary="reviewer", mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _initialize(root)
                plan = _activate(root)
                staged = _stage_enforced_candidate(root, plan, {"output": "result"})
                review = _bound_review(staged)
                if mutation == "missing":
                    review.pop("provider_execution_attestations", None)
                else:
                    review["provider_execution_attestations"]["claude"][
                        "executable_sha256"
                    ] = "sha256:" + "0" * 64
                protected = _snapshot_files(
                    root,
                    ["budget.json", "iterations.jsonl", gf.CURRENT_PLAN_FILE],
                )

                with self.assertRaises((ValueError, st.RevisionConflict)):
                    gf.finalize_candidate(root, accepted=True, review=review)

                self.assertEqual(
                    _snapshot_files(
                        root,
                        ["budget.json", "iterations.jsonl", gf.CURRENT_PLAN_FILE],
                    ),
                    protected,
                )
                self.assertIsNotNone(gf.load_pending_candidate(root))

    def test_accepted_candidate_is_reviewed_then_banked_and_updates_obligation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize(root)
            plan = _activate(root)
            staged = _stage_enforced_candidate(
                root,
                plan,
                {
                    "output": "Verified terminal proof",
                    "campaign_delta": "closed",
                    "obligation_transitions": [
                        {"obligation_id": "GOAL-SC-1", "to": "satisfied"}
                    ],
                    "evidence_ids": ["proof.json"],
                    "budget_delta": {"tokens": 50, "usd": 1.25},
                },
            )
            self.assertEqual(staged["status"], "staged")
            result = gf.finalize_candidate(
                root,
                accepted=True,
                review=_bound_review(
                    staged,
                    obligation_reviews=[
                        {
                            "obligation_id": "GOAL-SC-1",
                            "target_status": "satisfied",
                            "verdict": "accept",
                            "evidence_refs": ["proof.json"],
                        }
                    ],
                ),
            )
            self.assertEqual(result["status"], "accepted")
            self.assertEqual(result["record"]["global_delta"], "satisfied")
            self.assertFalse((root / gf.PENDING_CANDIDATE_FILE).exists())
            contract = gf.load_goal_contract(root)
            self.assertEqual(contract["obligations"]["GOAL-SC-1"]["status"], "satisfied")
            self.assertEqual(
                contract["obligations"]["GOAL-SC-1"]["evidence_refs"],
                ["proof.json"],
            )
            self.assertEqual(json.loads((root / "budget.json").read_text(encoding="utf-8"))["spent_iterations"], 1)
            rows = json.loads((root / "iterations.jsonl").read_text(encoding="utf-8").strip())
            self.assertEqual(rows["bank_status"], "accepted")

    def test_host_finalized_goal_success_validates_without_legacy_proof_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "loop"
            staged, review = _accepted_terminal_fixture(root)

            result = gf.finalize_candidate(root, accepted=True, review=review)

            self.assertEqual(
                result["record"]["stop_reason"],
                gf.HOST_REVIEWED_GOAL_SUCCESS_REASON,
            )
            self.assertEqual(list((root / "proof_artifacts").iterdir()), [])
            goal_focus_validation = gf.validate_goal_focus(
                root, require_enabled=True
            )
            self.assertEqual(
                goal_focus_validation["errors"], [], goal_focus_validation
            )
            validation = rt.validate_loop_dir(root)
            self.assertEqual(validation["errors"], [], validation)
            archive = (
                root
                / ".goal_focus"
                / "candidates"
                / f"{staged['candidate']['candidate_id']}.json"
            )
            self.assertTrue(archive.is_file())

    def test_host_finalized_goal_success_rejects_archive_evidence_review_and_fingerprint_tampering(self) -> None:
        cases = (
            "archive_deleted",
            "archived_evidence_tampered",
            "live_evidence_deleted",
            "archive_review_wrong_type",
            "review_tampered",
            "fingerprint_tampered",
            "ledger_source_fingerprint_tampered",
            "result_finalize_decision_missing",
            "result_finalize_decision_duplicated",
        )
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "loop"
                staged, review = _accepted_terminal_fixture(root)
                gf.finalize_candidate(root, accepted=True, review=review)
                candidate_id = staged["candidate"]["candidate_id"]
                archive_path = (
                    root / ".goal_focus" / "candidates" / f"{candidate_id}.json"
                )
                self.assertEqual(rt.validate_loop_dir(root)["errors"], [])

                if case == "archive_deleted":
                    archive_path.unlink()
                elif case == "live_evidence_deleted":
                    (root / ".goal_focus" / "evidence" / candidate_id / "proof.json").unlink()
                elif case == "ledger_source_fingerprint_tampered":
                    row = json.loads(
                        (root / "iterations.jsonl").read_text(encoding="utf-8")
                    )
                    row["source_candidate_fingerprint"] = "sha256:" + "0" * 64
                    (root / "iterations.jsonl").write_text(
                        json.dumps(row, sort_keys=True) + "\n", encoding="utf-8"
                    )
                elif case.startswith("result_finalize_decision_"):
                    decisions_path = root / gf.DIRECTION_DECISIONS_FILE
                    decisions = [
                        json.loads(line)
                        for line in decisions_path.read_text(encoding="utf-8").splitlines()
                    ]
                    result_decisions = [
                        row
                        for row in decisions
                        if row.get("decision_type") == "result_finalize"
                    ]
                    self.assertEqual(len(result_decisions), 1)
                    if case.endswith("missing"):
                        decisions = [
                            row
                            for row in decisions
                            if row.get("decision_type") != "result_finalize"
                        ]
                    else:
                        decisions.append(copy.deepcopy(result_decisions[0]))
                    decisions_path.write_text(
                        "".join(
                            json.dumps(row, sort_keys=True) + "\n"
                            for row in decisions
                        ),
                        encoding="utf-8",
                    )
                else:
                    archived = json.loads(archive_path.read_text(encoding="utf-8"))
                    if case == "archived_evidence_tampered":
                        archived["record"]["evidence_artifacts"][0]["content"] += (
                            "forged bytes"
                        )
                    elif case == "archive_review_wrong_type":
                        archived["review"] = []
                    elif case == "review_tampered":
                        archived["review"]["status"] = "failed"
                    else:
                        archived["review"]["candidate_fingerprint"] = (
                            "sha256:" + "0" * 64
                        )
                    _write_json(archive_path, archived)

                validation = rt.validate_loop_dir(root)
                self.assertEqual(validation["status"], "failed", validation)
                self.assertTrue(
                    any(
                        "host-reviewed goal success:" in error
                        for error in validation["errors"]
                    ),
                    validation,
                )

    def test_host_only_goal_success_reason_rejects_handwritten_rejected_and_nonterminal_rows(self) -> None:
        cases = ("handwritten", "rejected", "nonterminal")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "loop"
                staged, review = _accepted_terminal_fixture(root)
                gf.finalize_candidate(root, accepted=True, review=review)
                row = json.loads((root / "iterations.jsonl").read_text(encoding="utf-8"))

                if case == "handwritten":
                    row["candidate_id"] = "handwritten-candidate"
                    row.pop("result_review", None)
                    row.pop("source_candidate_fingerprint", None)
                    archive = (
                        root
                        / ".goal_focus"
                        / "candidates"
                        / f"{staged['candidate']['candidate_id']}.json"
                    )
                    archive.unlink()
                    decisions_path = root / gf.DIRECTION_DECISIONS_FILE
                    decisions = [
                        json.loads(line)
                        for line in decisions_path.read_text(encoding="utf-8").splitlines()
                    ]
                    decisions_path.write_text(
                        "".join(
                            json.dumps(item, sort_keys=True) + "\n"
                            for item in decisions
                            if item.get("decision_type") != "result_finalize"
                        ),
                        encoding="utf-8",
                    )
                elif case == "rejected":
                    row["bank_status"] = "rejected"
                else:
                    row["decision"] = "revise"
                (root / "iterations.jsonl").write_text(
                    json.dumps(row, sort_keys=True) + "\n", encoding="utf-8"
                )

                validation = rt.validate_loop_dir(root)
                self.assertEqual(validation["status"], "failed", validation)
                self.assertTrue(
                    any(
                        "host-reviewed goal success:" in error
                        for error in validation["errors"]
                    ),
                    validation,
                )

    def test_crash_recovered_host_finalization_validates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "loop"
            staged, review = _accepted_terminal_fixture(root)
            real_commit = gf.commit_transaction

            def crash_after_apply(*args: object, **kwargs: object) -> dict:
                kwargs["crash_after"] = "after_apply"
                return real_commit(*args, **kwargs)

            with mock.patch.object(
                gf, "commit_transaction", side_effect=crash_after_apply
            ), self.assertRaises(st.InjectedCrash):
                gf.finalize_candidate(root, accepted=True, review=review)

            recovered = st.recover_transactions(root)
            self.assertEqual(len(recovered), 1)
            self.assertEqual(recovered[0]["status"], "recovered")
            gf.reconcile_goal_focus(root, apply=True)
            self.assertFalse((root / gf.PENDING_CANDIDATE_FILE).exists())
            archive = (
                root
                / ".goal_focus"
                / "candidates"
                / f"{staged['candidate']['candidate_id']}.json"
            )
            self.assertTrue(archive.is_file())
            validation = rt.validate_loop_dir(root)
            self.assertEqual(validation["errors"], [], validation)

    def test_terminal_history_does_not_require_current_provider_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "terminal"
            _staged, review = _accepted_terminal_fixture(root)
            gf.finalize_candidate(root, accepted=True, review=review)

            with mock.patch.object(
                pp,
                "revalidate_provider_executable_attestation",
                side_effect=pp.PanelIsolationError("provider was upgraded"),
            ) as live_revalidation:
                validation = rt.validate_loop_dir(root)
            self.assertEqual(validation["errors"], [], validation)
            live_revalidation.assert_not_called()

            live_root = Path(tmp) / "live-finalization"
            staged, live_review = _accepted_terminal_fixture(live_root)
            with mock.patch.object(
                pp,
                "revalidate_provider_executable_attestation",
                side_effect=pp.PanelIsolationError("provider was upgraded"),
            ), self.assertRaisesRegex(ValueError, "executable attestation"):
                gf.finalize_candidate(
                    live_root, accepted=True, review=live_review
                )
            self.assertTrue((live_root / gf.PENDING_CANDIDATE_FILE).exists())

    def test_obligation_transition_records_only_its_reviewed_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize(root)
            plan = _activate(root)
            staged = _stage_enforced_candidate(
                root,
                plan,
                {
                    "output": "Verified terminal proof with an unrelated artifact",
                    "campaign_delta": "closed",
                    "obligation_transitions": [
                        {"obligation_id": "GOAL-SC-1", "to": "satisfied"}
                    ],
                    "evidence_ids": ["proof.json", "unrelated.json"],
                    "budget_delta": {},
                },
            )
            gf.finalize_candidate(
                root,
                accepted=True,
                review=_bound_review(
                    staged,
                    obligation_reviews=[
                        {
                            "obligation_id": "GOAL-SC-1",
                            "target_status": "satisfied",
                            "verdict": "accept",
                            "evidence_refs": ["proof.json"],
                        }
                    ],
                ),
            )
            obligation = gf.load_goal_contract(root)["obligations"]["GOAL-SC-1"]
            self.assertEqual(obligation["evidence_refs"], ["proof.json"])

    def test_nested_progress_assessment_is_normalized_and_terminal_status_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize(root)
            plan = _activate(root)
            staged = _stage_enforced_candidate(
                root,
                plan,
                {
                    "decision": "stop",
                    "progress_assessment": {
                        "campaign_delta": "closed",
                        "global_delta": "satisfied",
                        "obligation_ids": ["GOAL-SC-1"],
                    },
                    "evidence_checked": {"evidence_ids": ["proof.json"]},
                    "budget_delta": {},
                },
            )
            result = gf.finalize_candidate(
                root,
                accepted=True,
                review=_bound_review(
                    staged,
                    verdict="pass",
                    obligation_reviews=[
                        {
                            "obligation_id": "GOAL-SC-1",
                            "target_status": "satisfied",
                            "verdict": "accept",
                            "evidence_refs": ["proof.json"],
                        }
                    ],
                ),
            )
            self.assertEqual(result["record"]["campaign_delta"], "closed")
            self.assertEqual(result["record"]["global_delta"], "satisfied")
            self.assertEqual(
                result["record"]["obligation_transitions"],
                [{"obligation_id": "GOAL-SC-1", "to": "satisfied"}],
            )
            self.assertEqual(json.loads((root / "loop_state.json").read_text(encoding="utf-8"))["status"], "stopped")

    def test_finalize_requires_exact_candidate_id_without_mutation(self) -> None:
        for review_candidate_id in (None, "different-candidate"):
            with self.subTest(review_candidate_id=review_candidate_id), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _initialize(root)
                plan = _activate(root)
                staged = _stage_enforced_candidate(
                    root, plan, {"output": "candidate"}
                )
                tracked = [
                    "loop_state.json",
                    "budget.json",
                    "iterations.jsonl",
                    "recovery.md",
                    gf.GOAL_CONTRACT_FILE,
                    gf.APPROACH_REGISTRY_FILE,
                    gf.CURRENT_PLAN_FILE,
                    gf.DIRECTION_DECISIONS_FILE,
                    gf.PENDING_CANDIDATE_FILE,
                ]
                before = _snapshot_files(root, tracked)
                review = _bound_review(staged)
                review.pop("candidate_id")
                if review_candidate_id is not None:
                    review["candidate_id"] = review_candidate_id

                with self.assertRaisesRegex(ValueError, "candidate_id"):
                    gf.finalize_candidate(root, accepted=True, review=review)

                self.assertEqual(_snapshot_files(root, tracked), before)
                self.assertEqual(
                    gf.load_pending_candidate(root)["candidate_id"],
                    staged["candidate"]["candidate_id"],
                )
                self.assertFalse((root / ".goal_focus" / "candidates").exists())
                self.assertFalse((root / st.TRANSACTION_DIRNAME).exists())

    def test_acceptance_requires_supported_claim_and_accepted_obligation_coverage(self) -> None:
        cases = [
            (
                "unsupported claim",
                [
                    {
                        "claim_id": "CLAIM-1",
                        "status": "unsupported",
                        "evidence_refs": ["proof.json"],
                    }
                ],
                [
                    {
                        "obligation_id": "GOAL-SC-1",
                        "target_status": "satisfied",
                        "verdict": "accept",
                        "evidence_refs": ["proof.json"],
                    }
                ],
                "must be supported",
            ),
            (
                "rejected obligation",
                [
                    {
                        "claim_id": "CLAIM-1",
                        "status": "supported",
                        "evidence_refs": ["proof.json"],
                    }
                ],
                [
                    {
                        "obligation_id": "GOAL-SC-1",
                        "target_status": "satisfied",
                        "verdict": "reject",
                        "evidence_refs": ["proof.json"],
                    }
                ],
                "must be accept",
            ),
        ]
        for label, claim_reviews, obligation_reviews, error_pattern in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _initialize(root)
                plan = _activate(root)
                staged = _stage_enforced_candidate(
                    root,
                    plan,
                    {
                        "claim_ids": ["CLAIM-1"],
                        "obligation_transitions": [
                            {"obligation_id": "GOAL-SC-1", "to": "satisfied"}
                        ],
                        "evidence_ids": ["proof.json"],
                    },
                )
                tracked = [
                    "loop_state.json",
                    "budget.json",
                    "iterations.jsonl",
                    gf.GOAL_CONTRACT_FILE,
                    gf.CURRENT_PLAN_FILE,
                    gf.PENDING_CANDIDATE_FILE,
                ]
                before = _snapshot_files(root, tracked)

                with self.assertRaisesRegex(ValueError, error_pattern):
                    gf.finalize_candidate(
                        root,
                        accepted=True,
                        review=_bound_review(
                            staged,
                            claim_reviews=claim_reviews,
                            obligation_reviews=obligation_reviews,
                        ),
                    )

                self.assertEqual(_snapshot_files(root, tracked), before)

    def test_acceptance_rejects_supported_claim_without_matching_staged_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize(root)
            plan = _activate(root)
            staged = _stage_enforced_candidate(
                root,
                plan,
                {
                    "claim_ids": ["CLAIM-1"],
                    "evidence_ids": ["proof.json"],
                    "output": "Candidate proof",
                },
            )
            before = _snapshot_files(
                root,
                ["budget.json", "iterations.jsonl", gf.PENDING_CANDIDATE_FILE],
            )

            with self.assertRaisesRegex(ValueError, "lacks inspected staged evidence"):
                gf.finalize_candidate(
                    root,
                    accepted=True,
                    review=_bound_review(
                        staged,
                        claim_reviews=[
                            {
                                "claim_id": "CLAIM-1",
                                "status": "supported",
                                "evidence_refs": ["different-proof.json"],
                            }
                        ],
                    ),
                )

            self.assertEqual(
                _snapshot_files(
                    root,
                    ["budget.json", "iterations.jsonl", gf.PENDING_CANDIDATE_FILE],
                ),
                before,
            )

    def test_concurrent_staging_creates_exactly_one_pending_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize(root, mode="monitor")
            plan = _activate(root)
            barrier = threading.Barrier(2)
            outcomes: list[str] = []
            outcome_lock = threading.Lock()

            def stage(candidate_id: str) -> None:
                barrier.wait()
                try:
                    gf.stage_iteration_candidate(
                        root,
                        {"candidate_id": candidate_id, "output": candidate_id},
                        plan["plan_revision"],
                    )
                    outcome = "staged"
                except (ValueError, st.RevisionConflict):
                    outcome = "rejected"
                with outcome_lock:
                    outcomes.append(outcome)

            threads = [
                threading.Thread(target=stage, args=(candidate_id,))
                for candidate_id in ("candidate-A", "candidate-B")
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(sorted(outcomes), ["rejected", "staged"])
            self.assertIn(
                gf.load_pending_candidate(root)["candidate_id"],
                {"candidate-A", "candidate-B"},
            )
            self.assertEqual((root / "iterations.jsonl").read_text(encoding="utf-8"), "")
            self.assertFalse((root / st.TRANSACTION_DIRNAME).exists())

    def test_review_error_keeps_candidate_pending_and_consumes_no_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize(root)
            plan = _activate(root)
            staged = _stage_enforced_candidate(
                root, plan, {"output": "x"}
            )
            with self.assertRaises(ValueError):
                gf.finalize_candidate(
                    root,
                    accepted=False,
                    review=_bound_review(staged, status="error"),
                )
            self.assertIsNotNone(gf.load_pending_candidate(root))
            self.assertEqual(json.loads((root / "budget.json").read_text(encoding="utf-8"))["spent_iterations"], 0)
            self.assertEqual((root / "iterations.jsonl").read_text(encoding="utf-8"), "")

    def test_rejected_candidate_consumes_budget_but_banks_no_claims(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize(root)
            plan = _activate(root)
            staged = _stage_enforced_candidate(
                root,
                plan,
                {
                    "output": "Unsupported claimed proof",
                    "decision": "stop",
                    "stop_reason": "proof_found",
                    "claim_ids": ["CLAIM-1"],
                    "claims": ["theorem proved"],
                    "goal_contribution": "construct",
                    "campaign_delta": "substantial",
                    "obligation_transitions": [
                        {"obligation_id": "GOAL-SC-1", "to": "satisfied"}
                    ],
                    "evidence_checked": {"claim_ids": ["CLAIM-1"], "evidence_ids": []},
                    "progress_assessment": {
                        "campaign_delta": "closed",
                        "global_delta": "satisfied",
                        "obligation_ids": ["GOAL-SC-1"],
                    },
                    "execution": {"executor_provider": "codex", "duration_seconds": 3},
                    "compute": {"reported": True, "runs": [{"service": "modal"}]},
                    "budget_delta": {"tokens": 25},
                },
            )
            result = gf.finalize_candidate(
                root,
                accepted=False,
                review=_bound_review(staged, status="failed", reason="gap"),
            )
            record = result["record"]
            self.assertEqual(record["bank_status"], "rejected")
            self.assertEqual(record["proposed_decision"], "stop")
            self.assertEqual(record["proposed_stop_reason"], "proof_found")
            self.assertEqual(
                record["proposed_progress_assessment"]["global_delta"], "satisfied"
            )
            self.assertEqual(record["decision"], "revise")
            self.assertEqual(record["stop_reason"], "result_rejected")
            self.assertEqual(record["claims"], [])
            self.assertEqual(record["claim_ids"], [])
            self.assertEqual(record["obligation_transitions"], [])
            self.assertEqual(record["goal_contribution"], "none")
            self.assertEqual(record["campaign_delta"], "none")
            self.assertEqual(record["global_delta"], "none")
            self.assertEqual(
                record["progress_assessment"],
                {"campaign_delta": "none", "global_delta": "none", "obligation_ids": []},
            )
            self.assertEqual(record["evidence_checked"]["claim_ids"], [])
            self.assertEqual(record["execution"]["executor_provider"], "codex")
            self.assertEqual(record["compute"]["runs"], [{"service": "modal"}])
            self.assertEqual(json.loads((root / "budget.json").read_text(encoding="utf-8"))["spent_iterations"], 1)
            self.assertEqual(gf.load_current_plan(root)["state"], "needs_replan")

    def test_rejected_final_budget_candidate_becomes_terminal_block_and_validates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_json(
                root / "loop_state.json",
                {
                    "schema_version": rt.SCHEMA_VERSION,
                    "run_id": "final-rejection",
                    "goal": "Prove the main theorem",
                    "success_criteria": "Produce a verified terminal theorem.",
                    "default_mode": "bounded-research",
                    "status": "running",
                    "last_iteration": 0,
                    "next_preferred_path": "",
                },
            )
            _write_json(
                root / "budget.json",
                {
                    "schema_version": rt.SCHEMA_VERSION,
                    "max_iterations": 1,
                    "spent_iterations": 0,
                    "max_depth": 3,
                    "max_hops": 20,
                    "max_child_workers": 2,
                    "spent_tokens": 0,
                    "spent_usd": 0.0,
                },
            )
            (root / "iterations.jsonl").write_text("", encoding="utf-8")
            (root / "recovery.md").write_text("# Recovery\n", encoding="utf-8")
            gf.initialize_goal_focus(
                root,
                goal="Prove the main theorem",
                success_criteria="Produce a verified terminal theorem.",
            )
            plan = _activate(root)
            staged = _stage_enforced_candidate(
                root,
                plan,
                {
                    "mode": "bounded-research",
                    "objective": "Attempt the final proof",
                    "decision": "stop",
                    "stop_reason": "proof_found",
                    "output": "Claimed proof with a fatal gap",
                    "claim_ids": ["CLAIM-FINAL"],
                    "claims": ["main theorem proved"],
                    "evidence_checked": {
                        "claim_ids": ["CLAIM-FINAL"],
                        "evidence_ids": [],
                    },
                    "progress_assessment": {
                        "campaign_delta": "closed",
                        "global_delta": "satisfied",
                        "obligation_ids": ["GOAL-SC-1"],
                    },
                    "budget_delta": {"iterations": 1, "tokens": 10, "usd": 0.0},
                },
            )
            result = gf.finalize_candidate(
                root,
                accepted=False,
                review=_bound_review(
                    staged, status="failed", reason="fatal proof gap"
                ),
            )

            record = result["record"]
            state = json.loads((root / "loop_state.json").read_text(encoding="utf-8"))
            budget = json.loads((root / "budget.json").read_text(encoding="utf-8"))
            self.assertEqual(record["bank_status"], "rejected")
            self.assertEqual(record["decision"], "blocked")
            self.assertEqual(record["stop_reason"], "result_rejected_at_iteration_budget_limit")
            self.assertEqual(record["rejection_disposition"], "terminal_budget_exhausted")
            self.assertEqual(record["claims"], [])
            self.assertEqual(record["claim_ids"], [])
            self.assertEqual(record["global_delta"], "none")
            self.assertEqual(state["status"], "blocked")
            self.assertEqual(budget["spent_iterations"], 1)
            self.assertEqual(gf.load_current_plan(root)["state"], "terminal")
            validation = rt.validate_loop_dir(root)
            self.assertEqual(validation["errors"], [], validation)


if __name__ == "__main__":
    unittest.main()
