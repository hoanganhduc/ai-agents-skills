"""Unit tests for ARL host-owned panel_parent (hybrid multi-agent model)."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest
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

import panel_parent as pp  # noqa: E402
import provider_resources as pr  # noqa: E402


class PanelProviderCredentialScopeTests(unittest.TestCase):
    def test_each_attested_provider_receives_only_its_explicit_secret_keys(self) -> None:
        expected = {
            "claude": {
                "ANTHROPIC_API_KEY",
                "ANTHROPIC_AUTH_TOKEN",
                "CLAUDE_API_KEY",
                "CLAUDE_CODE_OAUTH_TOKEN",
            },
            "codex": {"OPENAI_API_KEY"},
            "codewhale": {"DEEPSEEK_API_KEY"},
            "deepseek": {"DEEPSEEK_API_KEY"},
            "grok": {"GROK_API_KEY", "XAI_API_KEY"},
            "antigravity": {"GEMINI_API_KEY", "GOOGLE_API_KEY"},
            "copilot": {"COPILOT_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"},
            "kimi": {"KIMI_API_KEY", "MOONSHOT_API_KEY"},
            "opencode": {"OPENCODE_API_KEY"},
        }
        self.assertEqual(
            {provider: set(keys) for provider, keys in pp.PANEL_PROVIDER_AUTH_ENV.items()},
            expected,
        )
        all_keys = set().union(*expected.values())
        source = {key: f"restored-{key.lower()}" for key in all_keys}
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            for provider, allowed in expected.items():
                with self.subTest(provider=provider), mock.patch.dict(
                    os.environ, source, clear=True
                ):
                    child = pp._panel_child_environment(
                        provider,
                        work,
                        {"provider": provider},
                    )
                self.assertEqual(set(child) & all_keys, allowed)


_TEST_PROVIDER_FAMILIES = {
    "codex": "openai",
    "claude": "anthropic",
    "codewhale": "deepseek",
    "grok": "xai",
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

    def __enter__(self) -> "_ProviderAttestationFixture":
        return self

    def __exit__(self, *_: object) -> None:
        self.cleanup()


def _provider_binary(name: str) -> str | None:
    return os.environ.get(f"AAS_AUTOLOOP_ATTESTED_BIN_{name.upper()}")


_TRUSTED_CONTAINMENT: bool | None = None


def _trusted_containment_works() -> bool:
    """Report whether the production containment command actually spawns.

    Trusted-local containment fails closed unless a root-owned bubblewrap
    binary sits at a fixed system path, and a present binary is not enough:
    hosts that confine unprivileged user namespaces (Ubuntu 24.04 does so
    through AppArmor) pass the trust check and then fail at spawn time. Probe
    the real wrapper once and cache the verdict for the whole module.
    """

    global _TRUSTED_CONTAINMENT
    if _TRUSTED_CONTAINMENT is None:
        _TRUSTED_CONTAINMENT = False
        if sys.platform.startswith("linux"):
            try:
                probe = pr.trusted_local_containment_command(
                    ["/bin/true"], cwd=Path.cwd().resolve()
                )
                completed = subprocess.run(
                    probe,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
            except (
                pr.ProviderResourceError,
                OSError,
                subprocess.SubprocessError,
            ):
                return _TRUSTED_CONTAINMENT
            _TRUSTED_CONTAINMENT = completed.returncode == 0
    return _TRUSTED_CONTAINMENT


_TRUSTED_RESOURCE_ENFORCEMENT: bool | None = None


def _trusted_resource_enforcement_works() -> bool:
    """Report whether this host can also enforce trusted-local resource limits.

    Containment is only half of the transport precondition. Every trusted-local
    child additionally runs inside a systemd user scope, so the host needs a
    live user manager and its session bus; without them the runtime refuses to
    start the child at all. A host that cannot enforce limits is not a runtime
    defect, so probe the real backend once and cache the verdict.
    """

    global _TRUSTED_RESOURCE_ENFORCEMENT
    if _TRUSTED_RESOURCE_ENFORCEMENT is None:
        _TRUSTED_RESOURCE_ENFORCEMENT = False
        if _trusted_containment_works():
            try:
                pr.preflight_resource_backend(30, role="panel")
            except (
                pr.ProviderResourceError,
                OSError,
                subprocess.SubprocessError,
            ):
                return _TRUSTED_RESOURCE_ENFORCEMENT
            _TRUSTED_RESOURCE_ENFORCEMENT = True
    return _TRUSTED_RESOURCE_ENFORCEMENT


def strategy_advice(
    approach_id: str = "A3",
    *,
    decision: str = "explore",
    rank: int = 1,
) -> str:
    estimates = {factor: {"lower": 1, "upper": 3} for factor in pp.ESTIMATE_FACTORS}
    return json.dumps(
        {
            "schema_version": "strategy_advice.v1",
            "decision": decision,
            "recommended_approach_id": approach_id,
            "candidates": [
                {
                    "approach_id": approach_id,
                    "rank": rank,
                    "estimates": estimates,
                    "evidence_refs": ["evidence/A3.json"],
                    "missing_evidence": ["independent reproduction"],
                    "falsifier": "the bridge lemma fails",
                    "strongest_objection": "the scope lift is not proved",
                    "next_action": "verify the bridge lemma",
                }
            ],
            "inspected_evidence": ["evidence/A3.json"],
            "uninspected_evidence": ["notes/unchecked.md"],
            "reasoning_summary": "A3 has the highest information value.",
        }
    )


def result_review(
    *,
    candidate_id: str = "cand-001",
    verdict: str = "pass",
) -> str:
    passed = verdict == "pass"
    candidate_fingerprint = "sha256:" + hashlib.sha256(
        candidate_id.encode("utf-8")
    ).hexdigest()
    return json.dumps(
        {
            "schema_version": "result_review.v1",
            "candidate_id": candidate_id,
            "candidate_fingerprint": candidate_fingerprint,
            "verdict": verdict,
            "safe_to_bank": passed,
            "inspected_paths": ["data/candidate.json"],
            "uninspected_paths": [],
            "claim_reviews": [
                {
                    "claim_id": "claim-1",
                    "status": "supported" if passed else "unsupported",
                    "evidence_refs": ["data/check.json"],
                    "reason": "the check reproduces the claim" if passed else "check fails",
                }
            ],
            "obligation_reviews": [
                {
                    "obligation_id": "O1",
                    "target_status": "satisfied",
                    "verdict": "accept" if passed else "reject",
                    "evidence_refs": ["data/check.json"],
                    "reason": "transition is supported" if passed else "not supported",
                }
            ],
            "machine_checks": [
                {
                    "status": "passed" if passed else "failed",
                    "artifact_ref": "data/check.json",
                    "summary": "independent replay",
                }
            ],
            "invalidation_conditions": ["the checker is unsound"],
            "summary": "accepted" if passed else "rejected",
        }
    )


class PanelParentUnitTests(unittest.TestCase):
    def test_archived_provider_attestation_is_static_but_strict(self) -> None:
        with _ProviderAttestationFixture() as fixture, mock.patch.dict(
            os.environ, fixture.environment, clear=False
        ):
            attestation = pp.attest_provider_executable("claude", required=True)
        assert attestation is not None

        validated = pp.validate_archived_provider_executable_attestation(
            attestation
        )
        self.assertEqual(validated, attestation)

        for field, value in (
            ("family", "openai"),
            ("executable_sha256", "sha256:invalid"),
            ("dependency_file_count", 250_001),
            ("dependency_policy", "unbounded"),
        ):
            with self.subTest(field=field):
                changed = dict(attestation)
                changed[field] = value
                with self.assertRaises(pp.PanelIsolationError):
                    pp.validate_archived_provider_executable_attestation(changed)

    def test_provider_transport_is_explicit_and_defaults_to_strict_isolated(self) -> None:
        self.assertEqual(pp.provider_transport_mode({}), "strict-isolated")
        self.assertEqual(
            pp.provider_transport_mode(
                {"AAS_AUTOLOOP_PROVIDER_TRANSPORT": "trusted_local"}
            ),
            "trusted-local",
        )
        self.assertEqual(
            pp.provider_transport_mode(
                {"AAS_AUTOLOOP_PROVIDER_TRANSPORT": "unexpected"}
            ),
            "strict-isolated",
        )

    def test_trusted_local_resource_limits_are_mandatory_and_bounded(self) -> None:
        limits = pp.provider_resource_limits(900, environ={})
        self.assertEqual(limits["wall_time_seconds"], 900)
        self.assertGreater(limits["memory_max_bytes"], 0)
        self.assertGreater(limits["address_space_bytes"], limits["memory_max_bytes"])
        self.assertGreater(limits["cpu_time_seconds"], 0)
        self.assertGreater(limits["tasks_max"], 0)
        self.assertGreater(limits["open_files_max"], 0)
        self.assertGreater(limits["file_size_max_bytes"], limits["output_max_bytes"])
        self.assertEqual(limits["core_size_max_bytes"], 0)
        with self.assertRaises(pp.ProviderResourceError):
            pp.provider_resource_limits(
                900,
                environ={"AAS_AUTOLOOP_RESOURCE_MEMORY_MIB": "not-an-integer"},
            )

    @unittest.skipUnless(
        _trusted_containment_works(),
        "trusted-local containment requires a working bubblewrap",
    )
    def test_trusted_local_resource_and_control_plane_command_shape(self) -> None:
        cwd = Path.cwd().resolve()
        containment = pr.trusted_local_containment_command(
            ["/bin/true"], cwd=cwd
        )
        masks = pr.provider_control_plane_mask_args()
        self.assertIn(
            ["--tmpfs", f"/run/user/{os.getuid()}"],
            [masks[index : index + 2] for index in range(len(masks) - 1)],
        )
        tmux_dir = Path(f"/tmp/tmux-{os.getuid()}")
        if tmux_dir.exists():
            self.assertIn(
                ["--tmpfs", str(tmux_dir)],
                [masks[index : index + 2] for index in range(len(masks) - 1)],
            )
        for directory in (
            Path("/run/dbus"),
            Path("/run/containerd"),
            Path("/run/libvirt"),
            Path("/run/lxd"),
            Path("/run/podman"),
        ):
            if directory.exists():
                self.assertIn(
                    ["--tmpfs", str(directory)],
                    [masks[index : index + 2] for index in range(len(masks) - 1)],
                )
        for socket in (
            Path("/run/docker.sock"),
            Path("/run/lxd-installer.socket"),
            Path("/run/snapd-snap.socket"),
            Path("/run/snapd.socket"),
        ):
            if socket.exists():
                self.assertIn(
                    ["--ro-bind", "/dev/null", str(socket)],
                    [masks[index : index + 3] for index in range(len(masks) - 2)],
                )
        self.assertTrue(
            any(
                containment[index : index + len(masks)] == masks
                for index in range(len(containment) - len(masks) + 1)
            )
        )
        self.assertIn("--unshare-cgroup", containment)
        self.assertIn(
            ["--tmpfs", "/sys/fs/cgroup"],
            [
                containment[index : index + 2]
                for index in range(len(containment) - 1)
            ],
        )

        probe = pr.trusted_local_containment_command(
            [
                sys.executable,
                "-c",
                (
                    "from pathlib import Path; "
                    "c=Path('/proc/self/cgroup').read_text(); "
                    "assert c.strip() == '0::/'; "
                    "assert not Path('/sys/fs/cgroup/memory.max').exists()"
                ),
            ],
            cwd=cwd,
        )
        completed = subprocess.run(
            probe, capture_output=True, text=True, timeout=10, check=False
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

        command, limits, _scope = pr.resource_limited_command(
            containment, 30, role="panel", environ={}
        )
        self.assertIn(f"--property=TasksMax={limits['tasks_max']}", command)
        self.assertIn("--property=KillMode=control-group", command)
        self.assertIn("--property=OOMPolicy=kill", command)
        self.assertIn("--nofile=1024", command)
        self.assertFalse(any(item.startswith("--nproc=") for item in command))
        self.assertIn("XDG_RUNTIME_DIR", command)
        self.assertIn("DBUS_SESSION_BUS_ADDRESS", command)
        gate_index = command.index(pr._RESOURCE_GATE_SCRIPT)
        self.assertGreater(gate_index, command.index("--core=0"))
        self.assertEqual(command[gate_index + 2], "--")
        self.assertEqual(command[gate_index + 3 :], containment)

    @unittest.skipUnless(
        _trusted_containment_works(),
        "trusted-local tmux masking requires a working bubblewrap",
    )
    def test_trusted_local_containment_hides_live_tmux_control_socket(self) -> None:
        tmux = Path("/usr/bin/tmux")
        socket = Path(f"/tmp/tmux-{os.getuid()}/default")
        if not tmux.is_file() or not socket.is_socket():
            self.skipTest("no live default tmux control socket")
        probe = [
            str(tmux),
            "-S",
            str(socket),
            "list-sessions",
            "-F",
            "reachable",
        ]
        outside = subprocess.run(
            probe, capture_output=True, text=True, timeout=5, check=False
        )
        if outside.returncode != 0:
            self.skipTest("default tmux server is not reachable")
        contained = pr.trusted_local_containment_command(
            probe, cwd=Path.cwd().resolve()
        )
        inside = subprocess.run(
            contained, capture_output=True, text=True, timeout=5, check=False
        )
        self.assertNotEqual(inside.returncode, 0)
        self.assertNotIn("reachable", inside.stdout)

    @unittest.skipUnless(
        _trusted_containment_works(),
        "trusted-local temporary cwd containment requires a working bubblewrap",
    )
    def test_trusted_local_containment_preserves_cwd_beneath_tmp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp).resolve()
            contained = pr.trusted_local_containment_command(
                ["/bin/sh", "-c", "test \"$PWD\" = \"$1\"", "sh", str(cwd)],
                cwd=cwd,
            )
            completed = subprocess.run(
                contained, capture_output=True, text=True, timeout=5, check=False
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_resource_scope_cleanup_fails_closed_on_inspection_error(self) -> None:
        failed = mock.Mock(returncode=1, stdout="", stderr="bus unavailable")
        with mock.patch.object(
            pr, "_trusted_host_binary", return_value="/usr/bin/systemctl"
        ), mock.patch.object(pr.subprocess, "run", return_value=failed):
            error = pr.cleanup_resource_scope(
                "aas-arl-panel-1234-deadbeefdead.scope"
            )
        self.assertEqual(error, "provider resource scope inspection failed")

    def test_resource_scope_cleanup_accepts_only_absent_or_observed_dead_scope(self) -> None:
        absent = mock.Mock(
            returncode=0,
            stdout="LoadState=not-found\nActiveState=inactive\n",
            stderr="",
        )
        active = mock.Mock(
            returncode=0,
            stdout="LoadState=loaded\nActiveState=active\n",
            stderr="",
        )
        killed = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(
            pr, "_trusted_host_binary", return_value="/usr/bin/systemctl"
        ), mock.patch.object(pr.subprocess, "run", return_value=absent):
            self.assertIsNone(
                pr.cleanup_resource_scope(
                    "aas-arl-primary-1234-feedfacefeed.scope"
                )
            )
        with mock.patch.object(
            pr, "_trusted_host_binary", return_value="/usr/bin/systemctl"
        ), mock.patch.object(
            pr.subprocess, "run", side_effect=[active, killed, absent]
        ) as run:
            self.assertIsNone(
                pr.cleanup_resource_scope(
                    "aas-arl-primary-1234-feedfacefeed.scope"
                )
            )
        self.assertEqual(run.call_count, 3)

    @unittest.skipUnless(
        sys.platform.startswith("linux"),
        "resource enforcement preflight requires Linux",
    )
    def test_resource_backend_preflight_propagates_scope_cleanup_failure(self) -> None:
        scope = "aas-arl-primary-1234-feedfacefeed.scope"
        limits = {"tasks_max": 64}
        completed = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(
            pr,
            "trusted_local_containment_command",
            return_value=["/bin/true"],
        ), mock.patch.object(
            pr,
            "resource_limited_command",
            return_value=(["/bin/true"], limits, scope),
        ), mock.patch.object(
            pr, "resource_control_environment", return_value={}
        ), mock.patch.object(
            pr.subprocess, "run", return_value=completed
        ), mock.patch.object(
            pr,
            "cleanup_resource_scope",
            return_value="provider resource scope cleanup failed",
        ):
            with self.assertRaisesRegex(
                pr.ProviderResourceError, "cleanup failed"
            ):
                pr.preflight_resource_backend(
                    30,
                    role="primary",
                    environ={"HOME": str(Path.home())},
                )

    @unittest.skipUnless(
        sys.platform.startswith("linux"),
        "resource enforcement preflight requires Linux",
    )
    def test_resource_backend_preflight_exercises_containment_and_limits(self) -> None:
        scope = "aas-arl-primary-1234-feedfacefeed.scope"
        limits = {"tasks_max": 64}
        completed = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(
            pr,
            "trusted_local_containment_command",
            return_value=["bounded-bwrap", "--", "/bin/true"],
        ) as containment, mock.patch.object(
            pr,
            "resource_limited_command",
            return_value=(["bounded-systemd-run"], limits, scope),
        ) as resource_command, mock.patch.object(
            pr, "resource_control_environment", return_value={}
        ), mock.patch.object(
            pr.subprocess, "run", return_value=completed
        ), mock.patch.object(
            pr, "cleanup_resource_scope", return_value=None
        ):
            observed = pr.preflight_resource_backend(
                30,
                role="primary",
                environ={"HOME": str(Path.home())},
            )

        containment.assert_called_once()
        probe_args, probe_kwargs = containment.call_args
        self.assertEqual(probe_kwargs, {"cwd": Path("/")})
        self.assertGreaterEqual(len(probe_args[0]), 5)
        self.assertEqual(probe_args[0][1:4], ["-I", "-S", "-c"])
        resource_command.assert_called_once_with(
            ["bounded-bwrap", "--", "/bin/true"],
            30,
            role="primary",
            environ={"HOME": str(Path.home())},
        )
        self.assertEqual(observed, limits)

    @unittest.skipUnless(
        sys.platform.startswith("linux"), "process cleanup requires Linux"
    )
    def test_panel_scope_cleanup_failure_forces_nonzero_result(self) -> None:
        with mock.patch.object(
            pr,
            "cleanup_resource_scope",
            return_value="bounded cleanup failure",
        ):
            rc, _stdout, stderr = pp._default_runner(
                ["/bin/true"],
                dict(os.environ),
                str(Path.cwd()),
                10,
                scope_unit="aas-arl-panel-1234-deadbeefdead.scope",
            )
        self.assertEqual(rc, 126)
        self.assertIn("bounded cleanup failure", stderr)
        self.assertIn("resource-cleanup-failed", stderr)

    def test_panel_cleanup_failure_dominates_simultaneous_output_overflow(self) -> None:
        bounded = mock.Mock(
            return_code=126,
            stdout=b"bounded prefix",
            stderr=b"",
            timed_out=False,
            oversized=True,
            capture_error=None,
            cleanup_error="synthetic scope survived cleanup",
        )
        with mock.patch.object(
            pp, "run_bounded_resource_process", return_value=bounded
        ):
            rc, stdout, stderr = pp._default_runner(
                ["/bin/true"],
                dict(os.environ),
                str(Path.cwd()),
                10,
                scope_unit="aas-arl-panel-1234-deadbeefdead.scope",
            )
        self.assertEqual(rc, 126)
        self.assertEqual(stdout, "")
        self.assertTrue(stderr.startswith("[resource-cleanup-failed]"))

    def test_only_verified_providers_are_prompt_only(self) -> None:
        self.assertEqual(
            pp.PROMPT_ONLY_PROVIDERS,
            frozenset({"claude", "codex", "codewhale", "deepseek", "grok"}),
        )

    def test_grok_private_prompt_transport_uses_dev_stdin(self) -> None:
        prompt = "bounded Grok review prompt"
        command = [
            "/usr/bin/grok",
            "-p",
            prompt,
            "--permission-mode",
            "plan",
        ]

        secured, stdin_prompt = pp._panel_private_prompt_transport(
            "grok", command, prompt
        )

        self.assertEqual(stdin_prompt, prompt)
        self.assertEqual(
            secured,
            [
                "/usr/bin/grok",
                "--prompt-file",
                "/dev/stdin",
                "--permission-mode",
                "plan",
            ],
        )
        self.assertNotIn(prompt, secured)

    def test_panel_commands_use_native_read_only_modes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            pp, "which", side_effect=lambda name: f"/usr/bin/{name}"
        ), mock.patch.dict(os.environ, {"CODEWHALE_PROVIDER": "anthropic"}, clear=False):
            root = Path(tmp)
            work = root / "work"
            work.mkdir()
            claude, _ = pp.build_cmd("claude", "review", root, work)
            codex, _ = pp.build_cmd("codex", "review", root, work)
            codewhale, codewhale_env = pp.build_cmd("codewhale", "review", root, work)
            antigravity, _ = pp.build_cmd("antigravity", "review", root, work)
            copilot, _ = pp.build_cmd("copilot", "review", root, work)

        self.assertIn("plan", claude)
        self.assertIn("--no-session-persistence", claude)
        self.assertIn("--safe-mode", claude)
        self.assertIn("--tools", claude)
        self.assertEqual(claude[claude.index("--tools") + 1], "")
        self.assertIn("read-only", codex)
        self.assertIn("--ignore-rules", codex)
        self.assertIn("shell_tool", codex)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", codex)
        self.assertIn("read-only", codewhale)
        self.assertEqual(
            codewhale[codewhale.index("--provider") + 1], "deepseek"
        )
        self.assertNotIn("CODEWHALE_PROVIDER", codewhale_env)
        self.assertNotIn("--auto", codewhale)
        self.assertIn("plan", antigravity)
        self.assertIn("--sandbox", antigravity)
        self.assertIn("--plan", copilot)
        self.assertIn("--disable-builtin-mcps", copilot)

    def test_panel_isolation_fails_closed_without_bwrap_for_unverified_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            pp, "which", return_value=None
        ):
            root = Path(tmp)
            with self.assertRaises(pp.PanelIsolationError):
                pp._read_only_panel_command(
                    ["/bin/true"], root, provider="opencode"
                )
            native, hard = pp._read_only_panel_command(
                ["/bin/true"], root, provider="claude"
            )
        self.assertEqual(native, ["/bin/true"])
        self.assertFalse(hard)

    @unittest.skipUnless(os.name == "posix", "requires POSIX symlink semantics")
    def test_panel_artifact_write_replaces_symlink_without_touching_target(self) -> None:
        def runner(cmd, env, cwd, timeout_s):  # noqa: ANN001
            return 0, "PANEL_SMOKE_OK", ""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            iter_dir = root / "iterations" / "iter001"
            raw = iter_dir / "raw"
            raw.mkdir(parents=True)
            victim = root / "victim.txt"
            victim.write_text("do not overwrite", encoding="utf-8")
            output = raw / "claude_smoke_stdout.txt"
            output.symlink_to(victim)

            summary = pp.dispatch_phase(
                iter_dir=iter_dir,
                phase="smoke",
                prompt="reply PANEL_SMOKE_OK",
                providers=["claude"],
                timeout_s=5,
                root=root,
                runner=runner,
                panel_cfg={"timeout_mode": "fixed", "timeouts": {"smoke": 5}},
            )

            self.assertTrue(summary["panel_content_pass"])
            self.assertEqual(victim.read_text(encoding="utf-8"), "do not overwrite")
            self.assertFalse(output.is_symlink())
            self.assertEqual(output.read_text(encoding="utf-8"), "PANEL_SMOKE_OK")

    @unittest.skipUnless(os.name == "posix", "requires POSIX symlink semantics")
    def test_panel_artifact_directory_symlink_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            iter_dir = root / "iterations" / "iter001"
            iter_dir.mkdir(parents=True)
            outside = root / "outside"
            outside.mkdir()
            (iter_dir / "raw").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(pp.PanelArtifactError):
                pp.dispatch_phase(
                    iter_dir=iter_dir,
                    phase="smoke",
                    prompt="reply PANEL_SMOKE_OK",
                    providers=["claude"],
                    timeout_s=5,
                    root=root,
                    runner=lambda *_args: (0, "PANEL_SMOKE_OK", ""),
                )
            self.assertEqual(list(outside.iterdir()), [])

    @unittest.skipUnless(os.name == "posix", "requires POSIX symlink semantics")
    def test_strategy_brief_rejects_symlinked_prompt_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            run_dir = base / "loop"
            run_dir.mkdir()
            outside = base / "outside-secret.txt"
            outside.write_text("PROMPT_SECRET_SENTINEL", encoding="utf-8")
            (run_dir / "goal_contract.json").symlink_to(outside)
            with self.assertRaises(pp.PanelArtifactError):
                pp.build_strategy_review_brief(run_dir)

    @unittest.skipUnless(os.name == "posix", "requires POSIX symlink semantics")
    def test_review_brief_rejects_symlinked_evidence_and_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            run_dir = base / "loop"
            iter_dir = run_dir / "iterations" / "iter001"
            iter_dir.mkdir(parents=True)
            outside = base / "outside"
            outside.mkdir()
            (outside / "00_evidence.md").write_text(
                "PROMPT_SECRET_SENTINEL", encoding="utf-8"
            )
            (iter_dir / "00_evidence.md").symlink_to(
                outside / "00_evidence.md"
            )
            with self.assertRaises(pp.PanelArtifactError):
                pp.build_review_brief(run_dir, iter_dir)
            (iter_dir / "00_evidence.md").unlink()
            (outside / "iteration_candidate.json").write_text(
                json.dumps({"candidate_id": "outside"}), encoding="utf-8"
            )
            (iter_dir / "data").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(pp.PanelArtifactError):
                pp.build_review_brief(run_dir, iter_dir)

    def test_real_panel_preflight_denies_before_sandbox_or_provider_side_effects(self) -> None:
        with _ProviderAttestationFixture() as fixture, tempfile.TemporaryDirectory(
            dir=str(Path.home())
        ) as tmp, mock.patch.dict(
            os.environ,
            {
                **fixture.environment,
                "AAS_AUTOLOOP_EXTERNAL_PANEL_EGRESS": "allow",
                "AAS_AUTOLOOP_PROVIDER_TRANSPORT": "strict-isolated",
            },
            clear=False,
        ):
            base = Path(tmp)
            root = base / "project"
            raw_dir = base / "raw"
            root.mkdir(mode=0o700)
            raw_dir.mkdir(mode=0o700)
            protected = root / "project-secret"
            protected_bytes = b"PRIVATE_PANEL_PROJECT_SENTINEL\n"
            protected.write_bytes(protected_bytes)
            marker = root / "provider-ran"

            with mock.patch.object(
                pp, "build_cmd"
            ) as build, mock.patch.object(
                pp, "attest_provider_executable"
            ) as attest, mock.patch.object(
                pp, "_new_panel_credential_vault"
            ) as credentials, mock.patch.object(
                pp, "_read_only_panel_command"
            ) as sandbox, mock.patch.object(
                pp, "_default_runner", return_value=(0, "PANEL_SMOKE_OK", "")
            ) as runner, mock.patch.object(pp.subprocess, "Popen") as popen:
                result = pp.run_one(
                    "claude",
                    "bounded non-sensitive review prompt",
                    root,
                    raw_dir,
                    "smoke",
                    5,
                )

            self.assertEqual(result["exit_code"], 126)
            self.assertEqual(result["error_class"], "isolation_unavailable")
            self.assertFalse(result["usable"])
            build.assert_not_called()
            attest.assert_not_called()
            credentials.assert_not_called()
            sandbox.assert_not_called()
            runner.assert_not_called()
            popen.assert_not_called()
            self.assertEqual(protected.read_bytes(), protected_bytes)
            self.assertFalse(marker.exists())

    @unittest.skipUnless(
        _trusted_resource_enforcement_works(),
        "trusted-local transport requires a working bubblewrap and a systemd user scope",
    )
    def test_trusted_local_real_panel_uses_stdin_and_mandatory_limits(self) -> None:
        prompt = "bounded non-sensitive review prompt"
        observed: dict[str, object] = {}

        def bounded_runner(cmd, env, cwd, timeout_s, **kwargs):  # noqa: ANN001
            observed.update(
                {
                    "cmd": cmd,
                    "env": env,
                    "cwd": cwd,
                    "timeout_s": timeout_s,
                    **kwargs,
                }
            )
            return 0, "PANEL_SMOKE_OK", ""

        with _ProviderAttestationFixture() as fixture, tempfile.TemporaryDirectory(
            dir=str(Path.home())
        ) as tmp, mock.patch.dict(
            os.environ,
            {
                **fixture.environment,
                "AAS_AUTOLOOP_PROVIDER_TRANSPORT": "trusted-local",
                "AAS_AUTOLOOP_EXTERNAL_PANEL_EGRESS": "allow",
            },
            clear=False,
        ), mock.patch.object(
            pp, "_default_runner", side_effect=bounded_runner
        ), mock.patch.object(
            pp, "cleanup_resource_scope", return_value=None
        ):
            base = Path(tmp)
            root = base / "project"
            raw_dir = base / "raw"
            root.mkdir(mode=0o700)
            raw_dir.mkdir(mode=0o700)
            result = pp.run_one(
                "claude", prompt, root, raw_dir, "smoke", 120
            )

        self.assertTrue(observed, result)
        command = observed["cmd"]
        self.assertIsInstance(command, list)
        self.assertNotIn(prompt, command)
        self.assertEqual(observed["stdin_text"], prompt)
        self.assertIn("--property=MemoryMax=3221225472", command)
        self.assertIn("--property=MemorySwapMax=0", command)
        self.assertIn("--property=TasksMax=64", command)
        self.assertIn("--property=CPUQuota=100%", command)
        self.assertIn("--expand-environment=no", command)
        self.assertIn("XDG_RUNTIME_DIR", command)
        self.assertIn("DBUS_SESSION_BUS_ADDRESS", command)
        self.assertFalse(any(str(item).startswith("--nproc=") for item in command))
        self.assertIn("--nofile=1024", command)
        self.assertIn("--core=0", command)
        masks = pr.provider_control_plane_mask_args()
        self.assertTrue(
            any(
                command[index : index + len(masks)] == masks
                for index in range(len(command) - len(masks) + 1)
            ),
            command,
        )
        self.assertTrue(result["usable"], result)
        self.assertEqual(result["provider_transport"], "trusted-local")
        self.assertEqual(result["prompt_transport"], "stdin")
        self.assertEqual(result["isolation_mode"], "trusted_local_resource_limited")
        self.assertEqual(result["resource_limits"]["tasks_max"], 64)

    @unittest.skipUnless(
        _trusted_resource_enforcement_works(),
        "trusted-local transport requires a working bubblewrap and a systemd user scope",
    )
    def test_trusted_local_grok_panel_uses_private_stdin_transport(self) -> None:
        prompt = "bounded Grok review prompt"
        observed: dict[str, object] = {}

        def bounded_runner(cmd, env, cwd, timeout_s, **kwargs):  # noqa: ANN001
            observed.update({"cmd": cmd, **kwargs})
            return 0, "PANEL_SMOKE_OK", ""

        with _ProviderAttestationFixture() as fixture, tempfile.TemporaryDirectory(
            dir=str(Path.home())
        ) as tmp, mock.patch.dict(
            os.environ,
            {
                **fixture.environment,
                "AAS_AUTOLOOP_PROVIDER_TRANSPORT": "trusted-local",
                "AAS_AUTOLOOP_EXTERNAL_PANEL_EGRESS": "allow",
            },
            clear=False,
        ), mock.patch.object(
            pp, "_default_runner", side_effect=bounded_runner
        ), mock.patch.object(
            pp, "cleanup_resource_scope", return_value=None
        ):
            base = Path(tmp)
            root = base / "project"
            raw_dir = base / "raw"
            root.mkdir(mode=0o700)
            raw_dir.mkdir(mode=0o700)
            result = pp.run_one("grok", prompt, root, raw_dir, "smoke", 120)

        command = observed["cmd"]
        self.assertNotIn(prompt, command)
        self.assertIn("--prompt-file", command)
        prompt_file_index = command.index("--prompt-file")
        self.assertEqual(command[prompt_file_index + 1], "/dev/stdin")
        self.assertEqual(observed["stdin_text"], prompt)
        self.assertTrue(result["usable"], result)
        self.assertEqual(result["provider_family"], "xai")
        self.assertEqual(result["prompt_transport"], "stdin")
        self.assertEqual(result["provider_transport"], "trusted-local")

    def test_trusted_local_invalid_limits_deny_before_provider_spawn(self) -> None:
        with _ProviderAttestationFixture() as fixture, tempfile.TemporaryDirectory(
            dir=str(Path.home())
        ) as tmp, mock.patch.dict(
            os.environ,
            {
                **fixture.environment,
                "AAS_AUTOLOOP_PROVIDER_TRANSPORT": "trusted-local",
                "AAS_AUTOLOOP_RESOURCE_MEMORY_MIB": "invalid",
            },
            clear=False,
        ), mock.patch.object(pp, "_default_runner") as runner:
            base = Path(tmp)
            root = base / "project"
            raw_dir = base / "raw"
            root.mkdir(mode=0o700)
            raw_dir.mkdir(mode=0o700)
            result = pp.run_one(
                "claude", "bounded prompt", root, raw_dir, "smoke", 120
            )
        runner.assert_not_called()
        self.assertEqual(result["exit_code"], 126)
        self.assertEqual(result["error_class"], "isolation_unavailable")

    @unittest.skipUnless(
        _trusted_resource_enforcement_works(),
        "trusted-local transport requires a working bubblewrap and a systemd user scope",
    )
    def test_trusted_local_codewhale_records_required_argv_transport(self) -> None:
        prompt = "bounded CodeWhale review prompt"
        observed: dict[str, object] = {}

        def bounded_runner(cmd, env, cwd, timeout_s, **kwargs):  # noqa: ANN001
            observed.update({"cmd": cmd, **kwargs})
            return 0, "PANEL_SMOKE_OK", ""

        with _ProviderAttestationFixture() as fixture, tempfile.TemporaryDirectory(
            dir=str(Path.home())
        ) as tmp, mock.patch.dict(
            os.environ,
            {
                **fixture.environment,
                "AAS_AUTOLOOP_PROVIDER_TRANSPORT": "trusted-local",
            },
            clear=False,
        ), mock.patch.object(
            pp, "_default_runner", side_effect=bounded_runner
        ), mock.patch.object(
            pp, "cleanup_resource_scope", return_value=None
        ):
            base = Path(tmp)
            root = base / "project"
            raw_dir = base / "raw"
            root.mkdir(mode=0o700)
            raw_dir.mkdir(mode=0o700)
            result = pp.run_one(
                "codewhale", prompt, root, raw_dir, "smoke", 120
            )

        self.assertIn(prompt, observed["cmd"])
        self.assertLess(
            observed["cmd"].index("--model"), observed["cmd"].index("exec")
        )
        exec_index = observed["cmd"].index("exec")
        self.assertEqual(
            observed["cmd"][exec_index + 1 : exec_index + 3],
            ["--reasoning-effort", "off"],
        )
        self.assertEqual(observed["cmd"][exec_index + 3], prompt)
        self.assertIsNone(observed["stdin_text"])
        self.assertTrue(result["usable"], result)
        self.assertEqual(result["provider_family"], "deepseek")
        self.assertEqual(result["prompt_transport"], "argv")
        self.assertEqual(result["provider_transport"], "trusted-local")

    @unittest.skipUnless(
        sys.platform.startswith("linux"),
        "trusted-local transport requires Linux",
    )
    def test_trusted_local_codewhale_argv_prompt_bound_denies_before_runner(self) -> None:
        # The verified one-shot CodeWhale interface has no stdin prompt mode.
        # Exercise a byte bound (not merely a character bound) below Linux's
        # per-argument exec ceiling, and prove refusal happens before spawn.
        prompt = "é" * 50_001
        self.assertGreater(len(prompt.encode("utf-8")), 100_000)
        with _ProviderAttestationFixture() as fixture, tempfile.TemporaryDirectory(
            dir=str(Path.home())
        ) as tmp, mock.patch.dict(
            os.environ,
            {
                **fixture.environment,
                "AAS_AUTOLOOP_PROVIDER_TRANSPORT": "trusted-local",
            },
            clear=False,
        ), mock.patch.object(
            pp,
            "_default_runner",
            return_value=(0, "PANEL_SMOKE_OK", ""),
        ) as runner:
            base = Path(tmp)
            root = base / "project"
            raw_dir = base / "raw"
            root.mkdir(mode=0o700)
            raw_dir.mkdir(mode=0o700)
            result = pp.run_one(
                "codewhale", prompt, root, raw_dir, "smoke", 120
            )

        runner.assert_not_called()
        self.assertEqual(result["exit_code"], 126, result)
        self.assertEqual(result["error_class"], "isolation_unavailable", result)
        self.assertFalse(result["usable"])

    @unittest.skipUnless(
        sys.platform.startswith("linux"), "process-group limits require Linux"
    )
    def test_panel_capture_rejects_oversized_output(self) -> None:
        rc, stdout, stderr = pp._default_runner(
            [sys.executable, "-c", "print('x' * 4096)"],
            dict(os.environ),
            str(Path.cwd()),
            10,
            output_limit_bytes=128,
        )
        self.assertEqual(rc, 126)
        self.assertEqual(stdout, "")
        self.assertIn("oversized", stderr)

    @unittest.skipUnless(
        sys.platform.startswith("linux"), "bounded stdin transport requires Linux"
    )
    def test_panel_capture_rejects_exit_before_complete_stdin(self) -> None:
        rc, _stdout, stderr = pp._default_runner(
            ["/bin/sh", "-c", "printf 'response without reading prompt\\n'"],
            dict(os.environ),
            str(Path.cwd()),
            10,
            stdin_text="x" * 500_000,
            output_limit_bytes=1_000_000,
        )
        self.assertEqual(rc, 126)
        self.assertIn("complete prompt", stderr)

    def test_panel_roster_cap_and_duplicates_fail_before_executor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            pp.concurrent.futures, "ThreadPoolExecutor"
        ) as executor:
            iter_dir = Path(tmp) / "iter001"
            with self.assertRaises(pp.PanelIsolationError):
                pp.dispatch_phase(
                    iter_dir=iter_dir,
                    phase="smoke",
                    prompt="bounded prompt",
                    providers=["claude"] * 10_000,
                    timeout_s=5,
                    root=Path(tmp),
                    runner=lambda *_args: (0, "PANEL_SMOKE_OK", ""),
                )
            executor.assert_not_called()
            self.assertFalse(iter_dir.exists())

    def test_real_panel_resource_preflight_fails_before_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ,
            {"AAS_AUTOLOOP_EXTERNAL_PANEL_EGRESS": "allow"},
            clear=False,
        ), mock.patch.object(
            pp,
            "preflight_resource_backend",
            side_effect=pp.ProviderResourceError("synthetic backend failure"),
        ) as preflight, mock.patch.object(
            pp.concurrent.futures, "ThreadPoolExecutor"
        ) as executor:
            iter_dir = Path(tmp) / "iter001"
            with self.assertRaisesRegex(
                pp.PanelIsolationError, "resource backend is unavailable"
            ):
                pp.dispatch_phase(
                    iter_dir=iter_dir,
                    phase="smoke",
                    prompt="bounded non-sensitive prompt",
                    providers=["claude"],
                    timeout_s=30,
                    root=Path(tmp),
                )
            preflight.assert_called_once_with(30, role="panel")
            executor.assert_not_called()
            self.assertFalse(iter_dir.exists())

    def test_panel_preflight_cleanup_failure_is_fatal_and_not_retryable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ,
            {"AAS_AUTOLOOP_EXTERNAL_PANEL_EGRESS": "allow"},
            clear=False,
        ), mock.patch.object(
            pp,
            "preflight_resource_backend",
            side_effect=pr.ProviderResourceCleanupError(
                "synthetic scope survived cleanup"
            ),
        ), mock.patch.object(
            pp.concurrent.futures, "ThreadPoolExecutor"
        ) as executor:
            iter_dir = Path(tmp) / "iter001"
            summary = pp.dispatch_phase(
                iter_dir=iter_dir,
                phase="smoke",
                prompt="bounded non-sensitive prompt",
                providers=["claude"],
                timeout_s=30,
                root=Path(tmp),
            )
            self.assertTrue(summary["fatal_resource_cleanup_failure"])
            self.assertFalse(summary["resource_cleanup_verified"])
            self.assertTrue(summary["dispatch_skipped"])
            executor.assert_not_called()
            self.assertFalse(iter_dir.exists())

    def test_panel_commands_strip_endpoint_identity_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ,
            {
                "OPENAI_BASE_URL": "https://router.invalid",
                "DEEPSEEK_BASE_URL": "https://router.invalid",
                "CODEWHALE_BASE_URL": "https://router.invalid",
                "GOOGLE_GEMINI_BASE_URL": "https://router.invalid",
            },
            clear=False,
        ):
            root = Path(tmp)
            codex, codex_env = pp.build_cmd("codex", "review", root, root)
            deepseek, deepseek_env = pp.build_cmd("deepseek", "review", root, root)
        self.assertIn('model_provider="openai"', codex)
        self.assertNotIn("OPENAI_BASE_URL", codex_env)
        self.assertIn("--provider", deepseek)
        self.assertEqual(deepseek_env["DEEPSEEK_BASE_URL"], "https://api.deepseek.com")
        self.assertNotIn("CODEWHALE_BASE_URL", deepseek_env)
        self.assertNotIn("GOOGLE_GEMINI_BASE_URL", codex_env)

    def test_attested_models_are_exactly_pinned_in_panel_argv(self) -> None:
        model_contracts = {
            "claude": ("--model", "claude-test-model", "AAS_CLAUDE_LATEST_MODEL"),
            "codex": ("--model", "codex-test-model", "AAS_CODEX_LATEST_MODEL"),
            "grok": ("-m", "grok-test-model", "AAS_GROK_LATEST_MODEL"),
        }
        with _ProviderAttestationFixture() as fixture, tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, fixture.environment, clear=False
        ):
            root = Path(tmp)
            for provider, (flag, model, configured_name) in model_contracts.items():
                with self.subTest(provider=provider):
                    cmd, _env = pp.build_cmd(provider, "review", root, root)
                    self.assertEqual(cmd.count(flag), 1)
                    self.assertEqual(cmd[cmd.index(flag) + 1], model)
                    with mock.patch.dict(
                        os.environ,
                        {configured_name: f"conflicting-{model}"},
                        clear=False,
                    ), self.assertRaises(pp.PanelIsolationError):
                        pp.build_cmd(provider, "review", root, root)

    def test_external_pii_prompt_requires_exact_payload_digest_before_runner(self) -> None:
        prompt = "Patient ID: MRN-8675309"
        approval_name = "AAS_AUTOLOOP_EXTERNAL_PII_APPROVAL_SHA256"
        exact_digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        denied_approvals = (None, "sha256:" + exact_digest, "0" * 64)
        for approval in denied_approvals:
            with self.subTest(approval=approval):
                approval_env = (
                    {} if approval is None else {approval_name: approval}
                )
                with self.assertRaises(pp.PanelIsolationError) as refused:
                    pp.assert_panel_prompt_safe(prompt, environ=approval_env)
                self.assertFalse("MRN-8675309" in str(refused.exception))

        pp.assert_panel_prompt_safe(
            prompt, environ={approval_name: exact_digest}
        )
        with self.assertRaises(pp.PanelIsolationError) as changed:
            pp.assert_panel_prompt_safe(
                prompt + "!", environ={approval_name: exact_digest}
            )
        self.assertFalse("MRN-8675309" in str(changed.exception))

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ,
            {
                "AAS_AUTOLOOP_EXTERNAL_PANEL_EGRESS": "allow",
                approval_name: exact_digest,
            },
            clear=False,
        ):
            root = Path(tmp)
            raw_dir = root / "fail-closed"
            raw_dir.mkdir(mode=0o700)
            with mock.patch.object(
                pp,
                "_default_runner",
                return_value=(0, "PANEL_SMOKE_OK", ""),
            ) as runner:
                result = pp.run_one(
                    "claude", prompt, root, raw_dir, "smoke", 5
                )
            runner.assert_not_called()
            self.assertFalse(result["usable"])
            self.assertEqual(result["error_class"], "isolation_unavailable")
            self.assertFalse("MRN-8675309" in json.dumps(result))
            self.assertTrue(
                all(
                    "MRN-8675309" not in path.read_text(
                        encoding="utf-8", errors="replace"
                    )
                    for path in raw_dir.iterdir()
                    if path.is_file()
                )
            )

    def test_real_dispatch_admission_precedes_prompt_artifact_creation(self) -> None:
        cases = (
            (
                "missing-egress-consent",
                "bounded non-sensitive review prompt",
                {"AAS_AUTOLOOP_EXTERNAL_PANEL_EGRESS": "deny"},
            ),
            (
                "credential",
                "secret=syntheticExternalPanelCredential123",
                {"AAS_AUTOLOOP_EXTERNAL_PANEL_EGRESS": "allow"},
            ),
            (
                "pii",
                "Patient ID: SYNTHETIC-RECORD-42",
                {
                    "AAS_AUTOLOOP_EXTERNAL_PANEL_EGRESS": "allow",
                    "AAS_AUTOLOOP_EXTERNAL_PII_APPROVAL_SHA256": "",
                },
            ),
        )
        for label, prompt, env in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
                os.environ, env, clear=True
            ):
                root = Path(tmp)
                iter_dir = root / "iterations" / "iter001"
                with self.assertRaises(pp.PanelIsolationError):
                    pp.dispatch_phase(
                        iter_dir=iter_dir,
                        phase="smoke",
                        prompt=prompt,
                        providers=["claude"],
                        timeout_s=5,
                        root=root,
                    )
                self.assertEqual(list(iter_dir.rglob("prompt.md")), [])
                for artifact in iter_dir.rglob("*") if iter_dir.exists() else ():
                    if artifact.is_file():
                        self.assertNotIn(
                            prompt,
                            artifact.read_text(encoding="utf-8", errors="replace"),
                        )

    def test_dispatcher_exception_sensitive_text_is_category_only(self) -> None:
        secret_value = "syntheticDispatcherCredential123"
        pii_value = "SYNTHETIC-RECORD-42"

        def runner(cmd, env, cwd, timeout_s):  # noqa: ANN001
            raise RuntimeError(
                f"secret={secret_value}; Patient ID: {pii_value}"
            )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            iter_dir = root / "iterations" / "iter001"
            summary = pp.dispatch_phase(
                iter_dir=iter_dir,
                phase="smoke",
                prompt="safe injected-runner prompt",
                providers=["claude"],
                timeout_s=5,
                root=root,
                runner=runner,
                panel_cfg={"timeout_mode": "fixed", "timeouts": {"smoke": 5}},
            )

            serialized = json.dumps(summary)
            self.assertNotIn(secret_value, serialized)
            self.assertNotIn(pii_value, serialized)
            meta = summary["results"]["claude"]
            self.assertTrue(meta["sensitive_output_blocked"])
            self.assertIn(
                "content:credential-assignment",
                meta["sensitive_output_categories"],
            )
            self.assertIn("pii:person-record", meta["sensitive_output_categories"])
            for artifact in iter_dir.rglob("*"):
                if artifact.is_file():
                    body = artifact.read_text(encoding="utf-8", errors="replace")
                    self.assertNotIn(secret_value, body, str(artifact))
                    self.assertNotIn(pii_value, body, str(artifact))

    def test_sensitive_schema_valid_panel_stdout_is_refused_without_persistence(self) -> None:
        cases = {
            "secret": "secret=abcdefghijklmnop",
            "pii": "Patient ID: MRN-8675309",
        }
        for case, sensitive_value in cases.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                iter_dir = root / "iter001"
                response = json.loads(strategy_advice())
                response["reasoning_summary"] = sensitive_value

                def runner(cmd, env, cwd, timeout_s):  # noqa: ANN001
                    return 0, json.dumps(response), ""

                summary = pp.dispatch_phase(
                    iter_dir=iter_dir,
                    phase="strategy_review",
                    prompt="compare safe inputs",
                    providers=["claude"],
                    timeout_s=5,
                    root=root,
                    runner=runner,
                    panel_cfg={
                        "primary_provider": "codex",
                        "timeout_mode": "fixed",
                        "timeouts": {"strategy_review": 5},
                    },
                )

                self.assertFalse(summary["panel_content_pass"])
                self.assertEqual(summary["usable_providers"], [])
                self.assertFalse(sensitive_value in json.dumps(summary))
                for artifact in iter_dir.rglob("*"):
                    if artifact.is_file():
                        self.assertFalse(
                            sensitive_value
                            in artifact.read_text(
                                encoding="utf-8", errors="replace"
                            ),
                            str(artifact),
                        )

    def test_usable_stdout_accepts_short_smoke(self) -> None:
        self.assertTrue(pp.usable_stdout("PANEL_SMOKE_OK\n"))
        self.assertTrue(pp.usable_stdout("• PANEL_SMOKE_OK\n"))
        self.assertFalse(pp.usable_stdout(""))
        self.assertFalse(pp.usable_stdout("tokens used\n29\n"))

    def test_classify_error(self) -> None:
        self.assertEqual(pp.classify_error("timeout exceeded", 124), "timeout")
        self.assertEqual(
            pp.classify_error("Read-only file system (os error 30)", 1),
            "read_only_filesystem",
        )
        self.assertEqual(pp.classify_error("rate limit exceeded", 1), "quota_or_credit")
        self.assertEqual(
            pp.classify_error(
                "API error (status 402 Payment Required): "
                "Grok Build usage balance exhausted",
                1,
            ),
            "quota_or_credit",
        )

    def test_provider_family_collapses_aliases_only(self) -> None:
        self.assertEqual(pp.provider_family("deepseek"), pp.provider_family("codewhale"))
        self.assertNotEqual(pp.provider_family("codex"), pp.provider_family("claude"))
        self.assertEqual(pp.provider_family("kimi"), "unverified")
        self.assertEqual(pp.provider_family("ANTIGRAVITY_CLI"), "google")

    def test_parse_strategy_advice_accepts_json_or_single_fence(self) -> None:
        raw = strategy_advice()
        parsed = pp.parse_panel_response("strategy_review", raw)
        self.assertTrue(parsed["valid"])
        fenced = pp.parse_panel_response("strategy_review", f"```json\n{raw}\n```")
        self.assertTrue(fenced["valid"])
        with_prose = pp.parse_panel_response(
            "strategy_review", f"Here is my answer:\n```json\n{raw}\n```"
        )
        self.assertFalse(with_prose["valid"])

    def test_unstructured_long_result_review_is_not_semantic_pass(self) -> None:
        def runner(cmd, env, cwd, timeout_s):  # noqa: ANN001
            return 0, "This is a long review with many confident words. " * 20, ""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary = pp.dispatch_phase(
                iter_dir=root / "iter001",
                phase="result_review",
                prompt="review",
                providers=["claude"],
                timeout_s=5,
                root=root,
                runner=runner,
                panel_cfg={
                    "primary_provider": "codex",
                    "timeout_mode": "fixed",
                    "timeouts": {"result_review": 5},
                },
            )
            self.assertFalse(summary["panel_content_pass"])
            self.assertFalse(summary["different_family_logic_available"])
            meta = summary["results"]["claude"]
            self.assertTrue(meta["transport_usable"])
            self.assertFalse(meta["structured_valid"])
            self.assertEqual(meta["status"], "invalid_response")

    def test_same_family_alias_cannot_satisfy_independent_review(self) -> None:
        def runner(cmd, env, cwd, timeout_s):  # noqa: ANN001
            return 0, result_review(), ""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary = pp.dispatch_phase(
                iter_dir=root / "iter001",
                phase="result_review",
                prompt="review",
                providers=["codewhale"],
                timeout_s=5,
                root=root,
                runner=runner,
                panel_cfg={
                    "primary_provider": "deepseek",
                    "timeout_mode": "fixed",
                    "timeouts": {"result_review": 5},
                },
            )
            self.assertTrue(summary["panel_content_pass"])
            self.assertFalse(summary["different_family_logic_available"])
            self.assertFalse(summary["independent_review_pass"])

    def test_active_driver_family_overrides_stale_panel_primary(self) -> None:
        def runner(cmd, env, cwd, timeout_s):  # noqa: ANN001
            return 0, result_review(), ""

        with tempfile.TemporaryDirectory() as tmp, _ProviderAttestationFixture() as fixture, mock.patch.dict(
            os.environ,
            {
                **fixture.environment,
                "AAS_AUTOLOOP_PRIMARY_PROVIDER": "claude",
            },
            clear=False,
        ), mock.patch.object(pp, "which", side_effect=_provider_binary):
            root = Path(tmp)
            summary = pp.dispatch_phase(
                iter_dir=root / "iter001",
                phase="result_review",
                prompt="review",
                providers=["codex"],
                timeout_s=5,
                root=root,
                runner=runner,
                panel_cfg={
                    "primary_provider": "codex",
                    "timeout_mode": "fixed",
                    "timeouts": {"result_review": 5},
                },
            )
            self.assertEqual(summary["primary_provider"], "claude")
            self.assertEqual(summary["primary_family"], "anthropic")
            self.assertTrue(summary["independent_review_pass"])

    def test_result_review_banking_invariants_are_validated(self) -> None:
        data = json.loads(result_review())
        data["safe_to_bank"] = True
        data["claim_reviews"][0]["status"] = "disputed"
        errors = pp.validate_result_review(data)
        self.assertTrue(any("must be supported" in error for error in errors))
        data = json.loads(result_review(verdict="fail"))
        data["safe_to_bank"] = True
        errors = pp.validate_result_review(data)
        self.assertTrue(any("exactly when verdict is pass" in error for error in errors))

        data = json.loads(result_review())
        data["inspected_paths"] = []
        data["claim_reviews"] = []
        errors = pp.validate_result_review(data)
        self.assertTrue(any("inspected_paths must not be empty" in error for error in errors))
        self.assertTrue(any("claim_reviews must not be empty" in error for error in errors))

        data = json.loads(result_review())
        data["claim_reviews"].append(dict(data["claim_reviews"][0]))
        data["obligation_reviews"].append(dict(data["obligation_reviews"][0]))
        errors = pp.validate_result_review(data)
        self.assertIn("claim_reviews claim_id values must be unique", errors)
        self.assertIn("obligation_reviews obligation_id values must be unique", errors)

    def test_conflicting_candidate_ids_prevent_independent_review_pass(self) -> None:
        responses = {
            "claude": pp.parse_panel_response(
                "result_review", result_review(candidate_id="candidate-A")
            ),
            "grok": pp.parse_panel_response(
                "result_review", result_review(candidate_id="candidate-B")
            ),
        }
        synthesis = pp.synthesize_structured_panel(
            "result_review",
            responses,
            primary_provider="codex",
            primary_family=pp.provider_family("codex"),
            provider_families={
                provider: pp.provider_family(provider) for provider in responses
            },
        )
        self.assertEqual(synthesis["candidate_ids"], ["candidate-A", "candidate-B"])
        self.assertEqual(synthesis["conservative_verdict"], "partial")
        self.assertTrue(synthesis["dissent"])
        self.assertFalse(synthesis["independent_review_pass"])

    def test_result_review_dissent_prevents_independent_pass(self) -> None:
        responses = {
            "claude": pp.parse_panel_response("result_review", result_review()),
            "grok": pp.parse_panel_response(
                "result_review", result_review(verdict="fail")
            ),
        }
        synthesis = pp.synthesize_structured_panel(
            "result_review",
            responses,
            primary_provider="codex",
            primary_family=pp.provider_family("codex"),
            provider_families={
                provider: pp.provider_family(provider) for provider in responses
            },
        )
        self.assertTrue(synthesis["different_family_logic_available"])
        self.assertEqual(synthesis["conservative_verdict"], "fail")
        self.assertTrue(synthesis["dissent"])
        self.assertFalse(synthesis["independent_review_pass"])

    def test_strategy_synthesis_exposes_dissent_and_rankings(self) -> None:
        responses = {
            "claude": pp.parse_panel_response(
                "strategy_review", strategy_advice("A3")
            ),
            "grok": pp.parse_panel_response(
                "strategy_review", strategy_advice("A1")
            ),
        }
        synthesis = pp.synthesize_structured_panel(
            "strategy_review",
            responses,
            primary_provider="codex",
            primary_family=pp.provider_family("codex"),
            provider_families={
                provider: pp.provider_family(provider) for provider in responses
            },
        )
        self.assertTrue(synthesis["panel_content_pass"])
        self.assertTrue(synthesis["different_family_logic_available"])
        self.assertTrue(synthesis["dissent"])
        self.assertEqual(synthesis["recommendation_counts"], {"A1": 1, "A3": 1})
        self.assertEqual(synthesis["candidate_rankings"]["A3"]["mean_rank"], 1.0)

    def test_strategy_dispatch_requires_structured_advice(self) -> None:
        outputs = {
            "claude": strategy_advice("A3"),
            "codex": "A3 is probably the best next direction." * 20,
        }

        def runner(cmd, env, cwd, timeout_s):  # noqa: ANN001
            provider = "claude" if "--no-session-persistence" in cmd else "codex"
            return 0, outputs[provider], ""

        with tempfile.TemporaryDirectory() as tmp, _ProviderAttestationFixture() as fixture, mock.patch.dict(
            os.environ, fixture.environment, clear=False
        ), mock.patch.object(pp, "which", side_effect=_provider_binary):
            root = Path(tmp)
            iter_dir = root / "iter001"
            summary = pp.dispatch_phase(
                iter_dir=iter_dir,
                phase="strategy_review",
                prompt="compare",
                providers=["claude", "codex"],
                timeout_s=5,
                root=root,
                runner=runner,
                panel_cfg={
                    "primary_provider": "codex",
                    "timeout_mode": "fixed",
                    "timeouts": {"strategy_review": 5},
                },
            )
            self.assertEqual(summary["usable_providers"], ["claude"])
            self.assertTrue(summary["independent_review_pass"])
            self.assertTrue(
                (iter_dir / "panel" / "00_strategy_review" / "claude.md").is_file()
            )

    def test_dispatch_decision_is_bound_to_in_memory_stdout(self) -> None:
        genuine = strategy_advice("A3")
        forged = strategy_advice("FORGED")

        def runner(cmd, env, cwd, timeout_s):  # noqa: ANN001
            return 0, genuine, ""

        original_write = pp._secure_write_text

        def write_then_replace(path, body, **kwargs):  # noqa: ANN001
            original_write(path, body, **kwargs)
            if Path(path).name == "claude_strategy_review_stdout.txt":
                Path(path).write_text(forged, encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            pp, "_secure_write_text", side_effect=write_then_replace
        ):
            root = Path(tmp)
            iter_dir = root / "iter001"
            summary = pp.dispatch_phase(
                iter_dir=iter_dir,
                phase="strategy_review",
                prompt="compare",
                providers=["claude"],
                timeout_s=5,
                root=root,
                runner=runner,
                panel_cfg={
                    "primary_provider": "codex",
                    "timeout_mode": "fixed",
                    "timeouts": {"strategy_review": 5},
                },
            )
        synthesis = summary["structured_synthesis"]
        self.assertEqual(synthesis["recommendation_counts"], {"A3": 1})
        self.assertNotIn("FORGED", json.dumps(summary))
        self.assertEqual(
            summary["results"]["claude"]["stdout_sha256"],
            hashlib.sha256(genuine.encode("utf-8")).hexdigest(),
        )
        self.assertNotIn("_stdout_body", summary["results"]["claude"])

    def test_strategy_brief_always_includes_exact_current_plan_without_presumption(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "loop_state.json").write_text(
                json.dumps(
                    {
                        "goal": "Prove G",
                        "success_criteria": "Certified proof",
                        "next_preferred_path": "SECRET_INCUMBENT_PATH",
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "current_plan.json").write_text(
                json.dumps(
                    {
                        "approach_id": "SECRET_INCUMBENT_PATH",
                        "next_action": "continue incumbent",
                    }
                ),
                encoding="utf-8",
            )
            default = pp.build_strategy_review_brief(run_dir)
            compatibility_false = pp.build_strategy_review_brief(
                run_dir, incumbent_visible=False
            )
            visible = pp.build_strategy_review_brief(run_dir, incumbent_visible=True)
            legacy = pp.build_target_brief(run_dir)
            self.assertIn("SECRET_INCUMBENT_PATH", default)
            self.assertIn("SECRET_INCUMBENT_PATH", compatibility_false)
            self.assertIn("SECRET_INCUMBENT_PATH", visible)
            self.assertIn("SECRET_INCUMBENT_PATH", legacy)
            self.assertIn("no presumption or tie-break advantage", default)
            self.assertNotIn("Prefer the committed next path", default)
            self.assertIn("one-shot, embedded-content-only review", default)
            self.assertIn(
                "Do not call tools or access files, the workspace, the network, or external information.",
                default,
            )

    def test_strategy_brief_preserves_nested_plan_and_all_binding_hashes_or_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            goal_contract = {
                "schema_version": "goal_contract.v2",
                "goal_revision": 3,
                "goal": "Prove the nested target.",
                "obligations": {
                    "terminal": {
                        "status": "open",
                        "evidence": ["evidence/terminal.json"],
                    }
                },
            }
            approach_registry = {
                "schema_version": "approach_registry.v2",
                "registry_revision": 5,
                "campaigns": {
                    "campaign-a": {
                        "approaches": {
                            "approach-a": {
                                "next_action": "Run the exact nested plan.",
                                "estimates": {
                                    "goal_resolution": {"lower": 2, "upper": 4}
                                },
                            }
                        }
                    }
                },
            }
            current_plan = {
                "schema_version": "current_plan.v2",
                "plan_revision": 7,
                "campaign_id": "campaign-a",
                "approach_id": "approach-a",
                "selection": {
                    "selected_candidate": {
                        "estimates": {
                            "goal_resolution": {"lower": 2, "upper": 4},
                            "dependency_risk": {"lower": 1, "upper": 3},
                        },
                        "evidence_for": [
                            {
                                "path": "evidence/terminal.json",
                                "checks": ["schema", "replay", "scope"],
                            }
                        ],
                    },
                    "dissent": {
                        "providers": ["claude", "grok"],
                        "objections": [{"kind": "bridge", "open": True}],
                    },
                },
                "compute_policy": {
                    "allowed_services": ["modal"],
                    "limits": {"modal": {"max_runs": 2, "max_usd": 1.5}},
                },
            }
            documents = {
                "goal_contract.json": goal_contract,
                "approach_registry.json": approach_registry,
                "current_plan.json": current_plan,
            }
            source_hashes: dict[str, str] = {}
            for name, value in documents.items():
                payload = (json.dumps(value, indent=1, ensure_ascii=False) + "\n").encode(
                    "utf-8"
                )
                (run_dir / name).write_bytes(payload)
                source_hashes[name] = hashlib.sha256(payload).hexdigest()

            brief = pp.build_strategy_review_brief(run_dir)

            self.assertIn(
                json.dumps(current_plan, indent=2, sort_keys=True),
                brief,
            )
            for name, field in (
                ("goal_contract.json", "goal_contract_source_sha256"),
                ("approach_registry.json", "approach_registry_source_sha256"),
                ("current_plan.json", "current_plan_source_sha256"),
            ):
                self.assertIn(
                    f'"{field}": "{source_hashes[name]}"',
                    brief,
                )
            for value, field in (
                (goal_contract, "goal_contract_fingerprint"),
                (approach_registry, "approach_registry_fingerprint"),
                (current_plan, "current_plan_fingerprint"),
            ):
                canonical = json.dumps(
                    value,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
                expected = "sha256:" + hashlib.sha256(canonical).hexdigest()
                self.assertIn(f'"{field}": "{expected}"', brief)
            self.assertNotIn("[truncated]", brief)

            with self.assertRaisesRegex(pp.PanelArtifactError, "exceeds 512 characters"):
                pp.build_strategy_review_brief(run_dir, max_chars=512)

    def test_review_brief_includes_candidate_and_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            iter_dir = run_dir / "iterations" / "iter001"
            data_dir = iter_dir / "data"
            data_dir.mkdir(parents=True)
            (data_dir / "iteration_candidate.json").write_text(
                json.dumps({"candidate_id": "cand-visible"}), encoding="utf-8"
            )
            brief = pp.build_review_brief(run_dir, iter_dir)
            self.assertIn("result_review.v1", brief)
            self.assertIn("cand-visible", brief)
            self.assertIn("one-shot, embedded-content-only review", brief)
            self.assertIn(
                "Do not call tools or access files, the workspace, the network, or external information.",
                brief,
            )

    def test_review_brief_prefers_authoritative_run_root_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            iter_dir = run_dir / "iterations" / "iter001"
            data_dir = iter_dir / "data"
            data_dir.mkdir(parents=True)
            (run_dir / "iteration_candidate.json").write_text(
                json.dumps({"candidate_id": "cand-authoritative"}), encoding="utf-8"
            )
            (data_dir / "iteration_candidate.json").write_text(
                json.dumps({"candidate_id": "cand-stale-local"}), encoding="utf-8"
            )
            brief = pp.build_review_brief(run_dir, iter_dir)
            self.assertIn("authoritative run-root pending state", brief)
            self.assertIn("cand-authoritative", brief)
            self.assertNotIn("cand-stale-local", brief)

    def test_dispatch_phase_with_fake_runner(self) -> None:
        def runner(cmd, env, cwd, timeout_s):  # noqa: ANN001
            # cmd ends with prompt for codex/claude; last arg or -p next
            return 0, "PANEL_SMOKE_OK\n", ""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            iter_dir = root / "iterations" / "iter001"
            summary = pp.dispatch_phase(
                iter_dir=iter_dir,
                phase="target_advice",
                prompt="Reply PANEL_SMOKE_OK",
                providers=[
                    "codex",
                    "claude",
                    "grok",
                    "opencode",
                    "antigravity",
                    "copilot",
                    "kimi",
                    "deepseek",
                ],
                timeout_s=5,
                root=root,
                runner=runner,
            )
            self.assertTrue(summary["panel_content_pass"])
            self.assertEqual(len(summary["usable_providers"]), 8)
            self.assertTrue((iter_dir / "panel" / "01_target_advice" / "claude.md").is_file())
            self.assertTrue((iter_dir / "data" / "panel_dispatch_target_advice.json").is_file())

    def test_dispatch_phase_caps_attempts_at_three(self) -> None:
        calls = 0

        def runner(cmd, env, cwd, timeout_s):  # noqa: ANN001
            nonlocal calls
            calls += 1
            return 0, "PANEL_SMOKE_OK\n", ""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            iter_dir = root / "iterations" / "iter001"
            summaries = [
                pp.dispatch_phase(
                    iter_dir=iter_dir,
                    phase="target_advice",
                    prompt="Reply PANEL_SMOKE_OK",
                    providers=["codex"],
                    timeout_s=5,
                    root=root,
                    runner=runner,
                    panel_cfg={
                        "max_attempts": 3,
                        "timeout_mode": "fixed",
                        "timeouts": {"target_advice": 5},
                        "timeout_calc": {"min_s": 1, "max_s": 5},
                    },
                )
                for _ in range(4)
            ]
            self.assertEqual(calls, 3)
            self.assertEqual(
                [summary["attempt_number"] for summary in summaries],
                [1, 2, 3, 3],
            )
            self.assertTrue(summaries[-1]["attempt_cap_reached"])
            self.assertTrue(summaries[-1]["panel_content_pass"])

    def test_resolve_panel_mode_auto(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            self.assertFalse(pp.resolve_panel_mode("off", run_dir))
            self.assertTrue(pp.resolve_panel_mode("on", run_dir))
            self.assertFalse(pp.resolve_panel_mode("auto", run_dir))
            (run_dir / "panel.json").write_text(
                json.dumps({"enabled": True, "providers": ["claude"]}),
                encoding="utf-8",
            )
            self.assertTrue(pp.resolve_panel_mode("auto", run_dir))
            cfg = pp.load_panel_config(run_dir)
            self.assertEqual(cfg["providers"], ["claude"])

    def test_exclude_until_credit_filters_providers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "panel.json").write_text(
                json.dumps(
                    {
                        "enabled": True,
                        "providers": [
                            "codex",
                            "claude",
                            "grok",
                            "opencode",
                            "antigravity",
                            "copilot",
                            "kimi",
                            "deepseek",
                        ],
                        "exclude_until_credit": ["codex", "kimi"],
                    }
                ),
                encoding="utf-8",
            )
            cfg = pp.load_panel_config(run_dir)
            self.assertEqual(
                cfg["providers"],
                ["claude", "grok", "opencode", "antigravity", "copilot", "deepseek"],
            )
            self.assertEqual(set(cfg["exclude_until_credit"]), {"codex", "kimi"})
            # Alias exclude_providers also works
            (run_dir / "panel.json").write_text(
                json.dumps(
                    {
                        "enabled": True,
                        "providers": ["claude", "codex"],
                        "exclude_providers": ["codex"],
                    }
                ),
                encoding="utf-8",
            )
            cfg2 = pp.load_panel_config(run_dir)
            self.assertEqual(cfg2["providers"], ["claude"])

    def test_host_synthesis_and_prompt_addon(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            iter_dir = Path(tmp) / "iter001"
            summary = {
                "usable_providers": ["claude"],
                "panel_content_pass": True,
                "different_family_logic_available": True,
                "results": {
                    "claude": {
                        "status": "ok",
                        "error_class": None,
                        "exit_code": 0,
                    }
                },
            }
            path = pp.write_host_synthesis(
                iter_dir, "target_advice", summary, next_path="SINGLE PATH: M3"
            )
            text = path.read_text(encoding="utf-8")
            self.assertIn("SINGLE PATH: M3", text)
            self.assertIn("claude", text)
            addon = pp.panel_prompt_addon(Path(tmp), iter_dir)
            self.assertIn("Do NOT", addon)
            self.assertIn("nest multi-agent", addon)

    def test_one_provider_fails_still_pass(self) -> None:
        def runner(cmd, env, cwd, timeout_s):  # noqa: ANN001
            if "--no-session-persistence" in cmd:
                return 1, "", "connection failed"
            return 0, result_review(), ""

        with tempfile.TemporaryDirectory() as tmp, _ProviderAttestationFixture() as fixture, mock.patch.dict(
            os.environ, fixture.environment, clear=False
        ), mock.patch.object(pp, "which", side_effect=_provider_binary):
            root = Path(tmp)
            summary = pp.dispatch_phase(
                iter_dir=root / "i",
                phase="result_review",
                prompt="review",
                providers=["claude", "codex"],
                timeout_s=5,
                root=root,
                runner=runner,
                panel_cfg={
                    "primary_provider": "claude",
                    "timeout_mode": "fixed",
                    "timeouts": {"result_review": 5},
                },
            )
            self.assertTrue(summary["panel_content_pass"])
            self.assertTrue(summary["independent_review_pass"])
            self.assertIn("codex", summary["usable_providers"])
            self.assertNotIn("claude", summary["usable_providers"])

    def test_timeout_fixed_mode_same_for_all(self) -> None:
        budgets = pp.compute_provider_timeouts(
            "result_review",
            "short",
            ["claude", "kimi", "codex"],
            {
                "timeout_mode": "fixed",
                "timeouts": {"result_review": 400},
                "timeout_calc": {"min_s": 1, "max_s": 2400},
            },
            explicit_timeout_s=400,
        )
        vals = {b["timeout_s"] for b in budgets.values()}
        self.assertEqual(vals, {400})
        self.assertTrue(all(b["timeout_mode"] == "fixed" for b in budgets.values()))

    def test_timeout_adaptive_size_and_provider_mult(self) -> None:
        small = pp.compute_provider_timeouts(
            "result_review",
            "x" * 100,
            ["codex", "kimi"],
            {"timeout_mode": "adaptive", "timeouts": {"result_review": 900}},
        )
        large = pp.compute_provider_timeouts(
            "result_review",
            "x" * 20000,
            ["codex", "kimi"],
            {"timeout_mode": "adaptive", "timeouts": {"result_review": 900}},
        )
        self.assertGreater(large["codex"]["timeout_s"], small["codex"]["timeout_s"])
        self.assertGreaterEqual(large["kimi"]["timeout_s"], large["codex"]["timeout_s"])
        self.assertLessEqual(large["kimi"]["timeout_s"], 2400)

    def test_timeout_clamp(self) -> None:
        budgets = pp.compute_provider_timeouts(
            "result_review",
            "x" * 500000,
            ["kimi"],
            {
                "timeout_mode": "adaptive",
                "timeouts": {"result_review": 900},
                "timeout_calc": {"max_s": 1000, "min_s": 120},
            },
        )
        self.assertEqual(budgets["kimi"]["timeout_s"], 1000)

    def test_timeout_history_pad(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            data = run_dir / "iterations" / "iter001" / "data"
            data.mkdir(parents=True)
            (data / "panel_dispatch_result_review.json").write_text(
                json.dumps(
                    {
                        "phase": "result_review",
                        "results": {
                            "kimi": {
                                "usable": True,
                                "elapsed_s": 1100,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            budgets = pp.compute_provider_timeouts(
                "result_review",
                "short",
                ["kimi"],
                {"timeout_mode": "adaptive", "timeouts": {"result_review": 900}},
                run_dir=run_dir,
            )
            # hist 1100 * 1.25 = 1375, times mult 1.5 → well above 900
            self.assertGreaterEqual(budgets["kimi"]["timeout_s"], 1300)
            self.assertIn("timeout_inputs", budgets["kimi"])

    def test_dispatch_records_timeout_inputs(self) -> None:
        def runner(cmd, env, cwd, timeout_s):  # noqa: ANN001
            return 0, "enough usable content for panel advice here\n", ""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary = pp.dispatch_phase(
                iter_dir=root / "i",
                phase="result_review",
                prompt="review me",
                providers=["codex"],
                timeout_s=180,
                root=root,
                runner=runner,
                panel_cfg={
                    "timeout_mode": "fixed",
                    "timeouts": {"result_review": 180},
                    "timeout_calc": {"min_s": 1, "max_s": 2400},
                },
            )
            meta = summary["results"]["codex"]
            self.assertEqual(meta["timeout_s"], 180)
            self.assertEqual(meta["timeout_mode"], "fixed")
            self.assertIn("timeout_inputs", meta)

    def test_target_brief_order_goal_before_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "recovery.md").write_text(
                "# recovery\nlong recovery body\n", encoding="utf-8"
            )
            (run_dir / "loop_state.json").write_text(
                json.dumps(
                    {
                        "goal": "G",
                        "success_criteria": "S",
                        "next_preferred_path": "PATH-A",
                        "last_iteration": 1,
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "goal_priority.json").write_text(
                json.dumps({"enabled": True, "primary_campaign": "main"}),
                encoding="utf-8",
            )
            (run_dir / "iterations.jsonl").write_text("", encoding="utf-8")
            brief = pp.build_target_brief(run_dir)
            goal_i = brief.find("Goal-EV") if "Goal-EV" in brief else brief.find("goal_priority")
            path_i = brief.find("next_preferred_path")
            rec_i = brief.find("recovery.md")
            self.assertGreaterEqual(goal_i, 0)
            self.assertGreater(path_i, goal_i)
            self.assertGreater(rec_i, path_i)
            self.assertIn("PATH-A", brief)


class PanelPiiPhonePatternTests(unittest.TestCase):
    """The phone detector must not fire on bare research digit runs."""

    def test_research_numbers_do_not_fire_phone(self) -> None:
        for payload in (
            "elapsed 1.99658203125 seconds on 96 seeds",
            "address_space_bytes 68719476736 and 4294967296",
            "state count 99658203125 within cap",
            "timestamp 2026-07-30T05:16:06Z window 14/14",
        ):
            with self.subTest(payload=payload):
                self.assertEqual(pp.panel_payload_pii_findings(payload), [])

    def test_formatted_phone_numbers_still_fire(self) -> None:
        for payload in (
            "call +1 (202) 555-0187 now",
            "hotline (090) 123-4567",
            "dial 090-123-4567 today",
            "intl +84 90 123 4567",
        ):
            with self.subTest(payload=payload):
                self.assertEqual(
                    pp.panel_payload_pii_findings(payload), ["pii:phone"]
                )


class ResultReviewStatusNormalizationTests(unittest.TestCase):
    """Unambiguous claim-status synonyms normalize; others stay invalid."""

    def test_synonyms_normalize_and_are_reported(self) -> None:
        data = {
            "claim_reviews": [
                {"claim_id": "c1", "status": "accepted", "reason": "ok"},
                {"claim_id": "c2", "status": "not_run", "reason": "skipped"},
                {"claim_id": "c3", "status": "supported", "reason": "ok"},
            ]
        }
        notes = pp.normalize_result_review_statuses(data)
        self.assertEqual(
            [review["status"] for review in data["claim_reviews"]],
            ["supported", "not_checked", "supported"],
        )
        self.assertEqual(len(notes), 2)
        self.assertIn("accepted -> supported", notes[0])

    def test_ambiguous_status_stays_and_fails_validation(self) -> None:
        data = {
            "claim_reviews": [
                {"claim_id": "c1", "status": "maybe", "reason": "unsure"}
            ]
        }
        self.assertEqual(pp.normalize_result_review_statuses(data), [])
        self.assertEqual(data["claim_reviews"][0]["status"], "maybe")


class DescriptorFreeDependencyAttestationTests(unittest.TestCase):
    """Windows attests the dependency closure by name, not by descriptor.

    ``os.open`` refuses a directory on Windows and there is no ``dir_fd``, so
    that platform walks the closure with ``lstat``. The walk is exercised
    directly here: ``os.name`` cannot be patched, because ``pathlib`` dispatches
    on it and cannot build a ``WindowsPath`` on a POSIX host.
    """

    def _closure(self, base: Path, *, newline: bytes = b"\r\n") -> Path:
        root = base / "node_modules" / "provider"
        (root / "lib" / "inner").mkdir(parents=True)
        (root / "cli.js").write_bytes(b"alpha" + newline + b"\x1aomega")
        (root / "lib" / "b.js").write_bytes(b"beta")
        (root / "lib" / "inner" / "c.js").write_bytes(b"gamma")
        if os.name == "posix":
            for current, _dirs, names in os.walk(root):
                os.chmod(current, 0o700)
                for name in names:
                    os.chmod(Path(current) / name, 0o600)
        return root

    def _walk(self, root: Path, **bounds: int) -> dict:
        return pp._dependency_tree_attestation_by_lstat(
            Path(os.path.abspath(root)),
            max_files=bounds.get("max_files", 250_000),
            max_bytes=bounds.get("max_bytes", 2_000_000_000),
        )

    @unittest.skipUnless(os.name == "posix", "requires POSIX directory descriptors")
    def test_the_lstat_walk_reproduces_the_descriptor_walk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._closure(Path(tmp))
            self.assertEqual(
                pp._dependency_tree_attestation(root), self._walk(root)
            )

    def test_an_absent_descriptor_selects_the_lstat_walk(self) -> None:
        real = pp._open_real_directory_descriptor

        def without_descriptor(path, *, create, purpose):  # noqa: ANN001
            absolute, descriptor = real(path, create=create, purpose=purpose)
            if descriptor is not None:
                os.close(descriptor)
            return absolute, None

        with tempfile.TemporaryDirectory() as tmp:
            root = self._closure(Path(tmp))
            expected = self._walk(root)
            with mock.patch.object(
                pp, "_open_real_directory_descriptor", without_descriptor
            ):
                self.assertEqual(pp._dependency_tree_attestation(root), expected)
            self.assertEqual(expected["dependency_file_count"], 3)
            self.assertEqual(expected["dependency_policy"], "hash_revalidated")

    def test_the_digest_covers_exact_file_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            crlf = self._closure(base / "crlf")
            newline = self._closure(base / "newline", newline=b"\n")
            self.assertNotEqual(
                self._walk(crlf)["dependency_sha256"],
                self._walk(newline)["dependency_sha256"],
            )

    def test_the_file_and_byte_bounds_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._closure(Path(tmp))
            for bounds in ({"max_files": 2}, {"max_bytes": 4}):
                with self.subTest(bounds=bounds):
                    with self.assertRaisesRegex(
                        pp.PanelIsolationError, "exceeds the attestation bound"
                    ):
                        self._walk(root, **bounds)

    @unittest.skipUnless(os.name == "posix", "requires POSIX symlink semantics")
    def test_a_symlinked_entry_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._closure(Path(tmp))
            (root / "lib" / "alias.js").symlink_to(root / "cli.js")
            with self.assertRaisesRegex(
                pp.PanelIsolationError, "symlink or reparse point"
            ):
                self._walk(root)

    @unittest.skipUnless(os.name == "posix", "requires POSIX symlink semantics")
    def test_a_symlinked_subdirectory_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = self._closure(base)
            outside = base / "outside"
            outside.mkdir(mode=0o700)
            (root / "vendor").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(
                pp.PanelIsolationError, "symlink or reparse point"
            ):
                self._walk(root)

    @unittest.skipUnless(os.name == "posix", "requires a POSIX named pipe")
    def test_a_non_regular_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._closure(Path(tmp))
            os.mkfifo(root / "pipe", 0o600)
            with self.assertRaisesRegex(
                pp.PanelIsolationError, "not a regular file/directory"
            ):
                self._walk(root)

    @unittest.skipUnless(os.name == "posix", "requires POSIX hard-link counts")
    def test_a_hard_link_out_of_the_closure_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = self._closure(base)
            os.link(root / "cli.js", root / "lib" / "cli-link.js")
            self.assertEqual(self._walk(root)["dependency_file_count"], 4)
            os.link(root / "cli.js", base / "escaped.js")
            with self.assertRaisesRegex(
                pp.PanelIsolationError, "hard link outside the attested closure"
            ):
                self._walk(root)

    @unittest.skipUnless(os.name == "posix", "requires POSIX ownership bits")
    def test_a_group_writable_entry_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._closure(Path(tmp))
            (root / "lib" / "b.js").chmod(0o660)
            with self.assertRaisesRegex(
                pp.PanelIsolationError, "not host-controlled"
            ):
                self._walk(root)

    def test_a_windows_reparse_point_is_rejected_as_link_like(self) -> None:
        class _Junction:
            st_mode = stat.S_IFDIR | 0o700
            st_file_attributes = (
                stat.FILE_ATTRIBUTE_DIRECTORY | stat.FILE_ATTRIBUTE_REPARSE_POINT
            )

        class _Regular:
            st_mode = stat.S_IFREG | 0o600
            st_file_attributes = stat.FILE_ATTRIBUTE_ARCHIVE

        self.assertTrue(pp._is_link_like(_Junction()))
        self.assertFalse(pp._is_link_like(_Regular()))


if __name__ == "__main__":
    unittest.main()
