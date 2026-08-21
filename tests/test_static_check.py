from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tools.static_check import bash_syntax_path, powershell_parse_script, powershell_single_quoted


class StaticCheckTests(unittest.TestCase):
    def test_powershell_single_quoted_escapes_embedded_quotes(self) -> None:
        self.assertEqual(powershell_single_quoted("a'b"), "'a''b'")

    def test_powershell_parse_script_embeds_absolute_path_without_args(self) -> None:
        path = Path("canonical/runtime/runners/run_skill.ps1")
        script = powershell_parse_script(path)

        self.assertNotIn("$args[0]", script)
        self.assertIn("$path=", script)
        self.assertIn("[System.Management.Automation.Language.Parser]::ParseFile($path", script)
        self.assertIn(str(path.resolve()).replace("'", "''"), script)

    def test_windows_python_runner_selects_one_command_when_path_has_duplicates(self) -> None:
        text = Path("canonical/runtime/runners/run_python.ps1").read_text(encoding="utf-8")

        self.assertIn("$commands = @(Get-Command", text)
        self.assertIn("return [string]($commands[0].Source)", text)
        self.assertIn("$python = [string]($commands[0].Source)", text)
        self.assertNotIn("$python = $command.Source", text)

    def test_windows_secret_launchers_ignore_forged_environment_folder_roots(self) -> None:
        paths = (
            Path("canonical/runtime/runners/run_python.ps1"),
            Path(
                "canonical/runtime/skills/hetzner-research-compute/"
                "run_hetzner_research_compute.ps1"
            ),
            Path(
                "canonical/runtime/skills/hetzner-research-compute/"
                "run_hetzner_reaper.ps1"
            ),
        )
        for path in paths:
            with self.subTest(path=str(path)):
                text = path.read_text(encoding="utf-8")
                self.assertIn("[System.Environment]::SystemDirectory", text)
                self.assertIn("GetFolderPath", text)
                self.assertIn("SpecialFolderOption]::DoNotVerify", text)
                self.assertNotRegex(
                    text,
                    r"\$env:(?:USERPROFILE|ProgramFiles|ProgramW6432|SystemRoot|WINDIR)",
                )
                self.assertIn("[System.IO.FileShare]::Read", text)

    def test_managed_runtime_runners_disable_python_bytecode_writes(self) -> None:
        expected = '$env:PYTHONDONTWRITEBYTECODE = "1"'
        for relative_path in (
            "canonical/runtime/runners/run_skill.ps1",
            "canonical/runtime/runners/run_python.ps1",
        ):
            with self.subTest(path=relative_path):
                text = Path(relative_path).read_text(encoding="utf-8")
                self.assertIn(expected, text)

        posix = Path("canonical/runtime/runners/run_skill.sh").read_text(encoding="utf-8")
        self.assertIn("export PYTHONDONTWRITEBYTECODE=1", posix)

    def test_managed_skill_secret_loader_has_exact_cross_platform_allowlist(self) -> None:
        expected = {
            "AXLE_API_KEY",
            "LEANEXPLORE_API_KEY",
            "OCR_SPACE_API_KEY",
            "OCR_SPACE_KEY",
            "OCRSPACE_API_KEY",
            "OCRSPACE_KEY",
            "OPENCLAW_S2_API_KEY",
            "SEMANTIC_SCHOLAR_API_KEY",
            "UNPAYWALL_EMAIL",
            "ZENODO_TOKEN",
        }
        posix = Path("canonical/runtime/runners/run_skill.sh").read_text(encoding="utf-8")
        projection_blocks = []
        lines = posix.splitlines()
        for index, line in enumerate(lines):
            if "select_flat_projection AAS_SKILL_SECRETS_FILE env" not in line:
                continue
            block = line
            while block.rstrip().endswith("\\"):
                index += 1
                block += "\n" + lines[index]
            projection_blocks.append(block.split(" env", 1)[1])
        projected = set(
            re.findall(r"\b[A-Z][A-Z0-9_]*\b", "\n".join(projection_blocks))
        )
        self.assertEqual(projected, expected)
        self.assertIn(
            'for key in ${projection_allow_keys[@]+"${projection_allow_keys[@]}"}; '
            'do loader_args+=(--allow-key "$key"); done',
            posix,
        )
        self.assertIn(
            'for key in ${projection_export_keys[@]+"${projection_export_keys[@]}"}; '
            'do loader_args+=(--export-key "$key"); done',
            posix,
        )

        run_skill = Path("canonical/runtime/runners/run_skill.ps1").read_text(
            encoding="utf-8"
        )
        contract_text = run_skill.split("$flatContracts = @{", 1)[1].split(
            "$normalizedLower", 1
        )[0]
        skill_contracts = re.findall(
            r'Pointer = "AAS_SKILL_SECRETS_FILE"; Format = "env"; Keys = @\((.*?)\)\s*}',
            contract_text,
            re.DOTALL,
        )
        windows_projected = set(
            re.findall(r'"([A-Z][A-Z0-9_]*)"', "\n".join(skill_contracts))
        )
        self.assertEqual(windows_projected, expected)
        self.assertIn(
            '-AllowedKeys ([string[]]$flatProjection["Keys"])', run_skill
        )
        self.assertIn(
            '-ExportKeys ([string[]]$flatProjection["Keys"])', run_skill
        )

        run_python = Path("canonical/runtime/runners/run_python.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "AAS_SKILL_SECRETS_FILE must be projected by run_skill.ps1 before Python launch.",
            run_python,
        )
        self.assertNotIn(
            'Import-AasSecretEnvFile -PointerEnv "AAS_SKILL_SECRETS_FILE"',
            run_python,
        )

    def test_secret_loader_files_are_manifested_with_platform_hardening(self) -> None:
        data = json.loads(Path("manifest/runtime.yaml").read_text(encoding="utf-8"))
        runners = {entry["target"]: entry for entry in data["runners"]}
        self.assertEqual(
            set(runners["load_secret_env.py"]["platforms"]),
            {"linux", "macos", "wsl"},
        )
        self.assertEqual(runners["load_secret_env.ps1"]["platforms"], ["windows"])
        helper = Path("canonical/runtime/runners/load_secret_env.ps1").read_text(
            encoding="utf-8"
        )
        for token in (
            "ReparsePoint",
            "CreateFileW",
            "GetSecurityInfo",
            "GetFileInformationByHandle",
            "GetFileInformationByHandleEx",
            "ChangeTime",
            "ReadSnapshot",
            "GetDirectoryDescriptor",
            "NumberOfLinks",
            "WindowsIdentity",
            "ReadFile",
            "RawSecurityDescriptor",
            "EnvironmentVariableTarget]::Process",
        ):
            self.assertIn(token, helper)
        self.assertNotIn("[System.IO.File]::ReadAllBytes", helper)
        self.assertNotIn("Get-Acl -LiteralPath $absolute", helper)

    def test_windows_broker_state_uses_handle_bound_owner_dacl_guards(self) -> None:
        manifest = json.loads(Path("manifest/runtime.yaml").read_text(encoding="utf-8"))
        runtime_targets = {
            entry["target"]
            for skill in manifest["skills"].values()
            for entry in skill.get("files", [])
        }
        self.assertIn("workspace/research_compute/windows_acl.py", runtime_targets)

        guard = Path(
            "canonical/runtime/workspace/research_compute/windows_acl.py"
        ).read_text(encoding="utf-8")
        for token in (
            "CreateFileW",
            "GetSecurityInfo",
            "GetFileInformationByHandle",
            "FILE_SHARE_READ",
            "OPEN_REPARSE",
            "current_sid_string",
            "owner_value != current_sid",
            "owner/SYSTEM/Administrators",
        ):
            self.assertIn(token, guard)

        state = Path(
            "canonical/runtime/workspace/research_compute/state.py"
        ).read_text(encoding="utf-8")
        budget = Path(
            "canonical/runtime/workspace/research_compute/budget_ledger.py"
        ).read_text(encoding="utf-8")
        self.assertIn("with private_path_guard(path, directory=False)", state)
        self.assertIn("with private_path_guard(path, directory=False)", budget)
        self.assertIn("with private_path_guard(lock_path, directory=False)", budget)

    def test_windows_force_loop_uses_native_acl_loader_for_provider_secrets(self) -> None:
        expected = {
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "CLAUDE_API_KEY",
            "CLAUDE_CODE_OAUTH_TOKEN",
            "COPILOT_GITHUB_TOKEN",
            "COPILOT_PROVIDER_API_KEY",
            "COPILOT_PROVIDER_BEARER_TOKEN",
            "DEEPSEEK_API_KEY",
            "GEMINI_API_KEY",
            "GH_TOKEN",
            "GITHUB_TOKEN",
            "GOOGLE_API_KEY",
            "GROK_API_KEY",
            "KIMI_API_KEY",
            "MOONSHOT_API_KEY",
            "OPENAI_API_KEY",
            "OPENCODE_API_KEY",
            "XAI_API_KEY",
        }
        text = Path(
            "canonical/runtime/skills/autonomous-research-loop-runtime/force-loop/run_force_loop.ps1"
        ).read_text(encoding="utf-8")
        # The call is wrapped over several lines with backtick continuations and
        # takes the allowlist by variable, so read the assignment it names.
        call = text.split('-PointerEnv "AAS_PROVIDER_SECRETS_FILE"', 1)[1]
        call = call.split("\n\n", 1)[0]
        self.assertIn("-AllowedKeys $ProviderAllowed", call)
        block = text.split("$ProviderAllowed = @(", 1)[1].split(")", 1)[0]
        self.assertEqual(set(re.findall(r'"([A-Z][A-Z0-9_]*)"', block)), expected)
        self.assertIn("Remove-Item Env:AAS_PROVIDER_SECRETS_FILE", text)

    @unittest.skipUnless(os.name == "nt", "native PowerShell secret loader test")
    def test_windows_secret_loader_projects_private_file_into_process_only(self) -> None:
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        self.assertIsNotNone(powershell)
        loader = Path("canonical/runtime/runners/load_secret_env.ps1").resolve()
        with tempfile.TemporaryDirectory() as tmp:
            secret = Path(tmp) / "skill.env"
            secret.write_text("AXLE_API_KEY=restored-windows-value\n", encoding="utf-8")

            def quoted(path: Path) -> str:
                return str(path).replace("'", "''")

            command = f"""
. '{quoted(loader)}'
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$parentAcl = [System.Security.AccessControl.DirectorySecurity]::new()
$parentAcl.SetOwner($identity.User)
$parentRule = [System.Security.AccessControl.FileSystemAccessRule]::new(
    $identity.User,
    [System.Security.AccessControl.FileSystemRights]::FullControl,
    [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
        [System.Security.AccessControl.InheritanceFlags]::ObjectInherit,
    [System.Security.AccessControl.PropagationFlags]::None,
    [System.Security.AccessControl.AccessControlType]::Allow
)
[void]$parentAcl.AddAccessRule($parentRule)
Set-Acl -LiteralPath '{quoted(secret.parent)}' -AclObject $parentAcl
$acl = [System.Security.AccessControl.FileSecurity]::new()
$acl.SetOwner($identity.User)
$rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
    $identity.User,
    [System.Security.AccessControl.FileSystemRights]::Read,
    [System.Security.AccessControl.AccessControlType]::Allow
)
[void]$acl.AddAccessRule($rule)
Set-Acl -LiteralPath '{quoted(secret)}' -AclObject $acl
$env:AAS_SKILL_SECRETS_FILE = '{quoted(secret)}'
$env:ZENODO_TOKEN = 'stale-ambient-zenodo'
Import-AasSecretEnvFile `
    -PointerEnv 'AAS_SKILL_SECRETS_FILE' `
    -AllowedKeys @('AXLE_API_KEY', 'ZENODO_TOKEN') `
    -ExportKeys @('AXLE_API_KEY')
[Console]::Out.Write(
    "$($env:AXLE_API_KEY)|$($env:ZENODO_TOKEN)|$($env:AAS_SKILL_SECRETS_FILE)"
)
"""
            completed = subprocess.run(
                [str(powershell), "-NoProfile", "-NonInteractive", "-Command", command],
                check=False,
                text=True,
                capture_output=True,
                # pwsh cold start plus two Set-Acl round trips runs well past
                # 30s on a loaded Windows runner, and the timeout fails the
                # job rather than skipping. A long ceiling costs nothing when
                # the loader behaves and still bounds a genuine hang.
                timeout=180,
            )
            if completed.returncode != 0:
                ancestor_gate_messages = (
                    "Secret env path grants unsafe access outside the trusted ancestor boundary",
                    "Secret env path has an untrusted owner in its ancestor chain",
                )
                gate = next(
                    (message for message in ancestor_gate_messages if message in completed.stderr),
                    None,
                )
                if gate is not None:
                    self.skipTest(
                        "the strict Windows private-path DACL guard rejects this "
                        f"environment's temp ancestor chain by design: {gate}"
                    )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, "restored-windows-value||")

    def test_installed_runtime_copy_uses_portable_open_flags(self) -> None:
        text = Path("installer/ai_agents_skills/runtime_smoke.py").read_text(encoding="utf-8")

        self.assertIn('getattr(os, "O_CLOEXEC", 0)', text)
        self.assertIn('getattr(os, "O_BINARY", 0)', text)
        self.assertNotIn("os.O_RDONLY | os.O_CLOEXEC", text)

    def test_every_read_descriptor_on_a_windows_path_opts_into_binary_mode(self) -> None:
        # Windows opens descriptors in the CRT text mode, which collapses CRLF and
        # stops at Ctrl-Z. A read that hashes or size-checks its bytes then reports
        # a phantom mismatch on any file with Windows line endings. Pinning one
        # module is not enough -- the whole installer package runs on Windows CI.
        roots = [
            Path("installer/ai_agents_skills"),
            Path("canonical/runtime/skills/autonomous-research-loop-runtime"),
        ]
        offenders = []
        for root in roots:
            for source in sorted(root.rglob("*.py")):
                tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    if not (
                        isinstance(node.func, ast.Attribute)
                        and node.func.attr == "open"
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "os"
                    ):
                        continue
                    if len(node.args) < 2:
                        continue
                    flags = ast.unparse(node.args[1])
                    if "O_DIRECTORY" in flags:
                        continue  # a directory descriptor has no translation mode
                    if "O_RDONLY" in flags and "O_BINARY" not in flags:
                        offenders.append(f"{source}:{node.lineno}")
        self.assertEqual(offenders, [])

    def test_every_deferred_sibling_import_names_something_that_exists(self) -> None:
        # Function-local `from .sibling import name` lines are only executed on the
        # branch that needs them, so a renamed export stays invisible until a user
        # takes that branch -- and the branches that defer their imports are the
        # host-only ones the suite cannot run.
        package = Path("installer/ai_agents_skills")
        exported: dict[str, set[str]] = {}
        for source in sorted(package.glob("*.py")):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            names = set()
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names.add(node.name)
                elif isinstance(node, ast.Assign):
                    names.update(t.id for t in node.targets if isinstance(t, ast.Name))
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    names.add(node.target.id)
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    names.update(a.asname or a.name.split(".")[0] for a in node.names)
            exported[source.stem] = names

        offenders = []
        for source in sorted(package.glob("*.py")):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or node.level != 1 or node.module is None:
                    continue
                if node.module not in exported:
                    continue
                for alias in node.names:
                    if alias.name != "*" and alias.name not in exported[node.module]:
                        offenders.append(f"{source}:{node.lineno}: {node.module}.{alias.name}")
        self.assertEqual(offenders, [])

    def test_wsl_bash_syntax_path_uses_wslpath_with_forward_slashes(self) -> None:
        converted = Mock(returncode=0, stdout="/mnt/c/repo/script.sh\n")
        script_path = Path("C:/repo/script.sh")
        with (
            patch("tools.static_check.os.name", "nt"),
            patch("tools.static_check.shutil.which", return_value="C:\\Windows\\system32\\wsl.exe"),
            patch("tools.static_check.subprocess.run", return_value=converted) as run,
        ):
            result = bash_syntax_path(
                script_path,
                "C:\\Windows\\system32\\bash.EXE",
            )

        self.assertEqual(result, "/mnt/c/repo/script.sh")
        self.assertNotIn("\\", run.call_args.args[0][-1])

    def test_runtime_skill_sources_do_not_document_codex_runtime_runner(self) -> None:
        forbidden = (
            "bash ~/.codex/runtime/run_skill.sh",
            "~/.codex/runtime/workspace",
            "$HOME/.codex/runtime/workspace",
            "%USERPROFILE%\\.codex\\runtime",
        )
        root = Path("canonical/runtime/skills")
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in {".md", ".py", ".sh", ".txt"}:
                continue
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                with self.subTest(path=str(path), token=token):
                    self.assertNotIn(token, text)

    def test_canonical_skill_runtime_guidance_avoids_codex_runtime_paths(self) -> None:
        forbidden = (
            "bash ~/.codex/runtime/run_skill.sh",
            "bash ~/.local/share/ai-agents-skills/runtime/run_skill.sh",
            "~/.codex/runtime/workspace",
            "$HOME/.codex/runtime",
            "$env:USERPROFILE\\.codex\\runtime",
            "%USERPROFILE%\\.codex\\runtime",
            "Codex-only installs the runtime",
            "Codex runtime runner",
            "vendored Codex runtime",
        )
        for path in Path("canonical/skills").glob("*/SKILL.md"):
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                with self.subTest(path=str(path), token=token):
                    self.assertNotIn(token, text)

    def test_codex_auto_install_is_self_contained(self) -> None:
        from installer.ai_agents_skills.capabilities import effective_install_mode_with_evidence
        from installer.ai_agents_skills.render import canonical_skill_path

        source = canonical_skill_path("remote-bridge")
        mode, reason, evidence = effective_install_mode_with_evidence("codex", "auto", source)

        self.assertEqual(mode, "copy")
        self.assertIn("self-contained", reason)
        self.assertEqual(evidence["agent_policy"]["default_mode"], "copy")


if __name__ == "__main__":
    unittest.main()
