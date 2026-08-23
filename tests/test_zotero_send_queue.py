"""Security contract tests for authenticated Zotero host delivery."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "canonical/runtime/skills/zotero/send_queue.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("aas_zotero_send_queue", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@unittest.skipIf(os.name == "nt", "POSIX descriptor queue")
def _attested_node_available() -> bool:
    """Mirror the fixed root-controlled Node runtime attestation (uid 0, nlink 1)."""
    try:
        info = os.lstat("/usr/bin/node")
    except OSError:
        return False
    return (
        stat.S_ISREG(info.st_mode)
        and int(info.st_nlink) == 1
        and int(info.st_uid) == 0
        and not stat.S_IMODE(info.st_mode) & 0o022
        and bool(stat.S_IMODE(info.st_mode) & 0o111)
    )


class ZoteroSendQueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.queue = _load_module()

    def test_default_authority_is_host_scoped_and_not_openclaw_delivery_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            authority = root / ".config/ai-agents-skills/file-delivery-queue.json"
            authority.parent.mkdir(parents=True, mode=0o700)
            replay = root / ".local/state/ai-agents-skills/file-delivery-replay"
            replay.mkdir(parents=True, mode=0o700)
            authority.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "hmac_key_hex": "11" * 32,
                        "allowed": {"whatsapp": ["target"]},
                        "max_job_age_seconds": 60,
                        "max_media_bytes": 1024,
                        "replay_ledger_dir": "aas-host-state:file-delivery-replay",
                        "replay_retention_seconds": 300,
                        "max_replay_entries": 100,
                    }
                ),
                encoding="utf-8",
            )
            authority.chmod(0o600)
            with mock.patch.dict(
                os.environ,
                {"HOME": str(root)},
                clear=False,
            ):
                os.environ.pop("AAS_FILE_DELIVERY_SECRETS_FILE", None)
                policy = self.queue.load_policy()

            self.assertEqual(policy.replay_ledger_dir, replay)

            second_home = root / "restored-home"
            second_authority = (
                second_home / ".config/ai-agents-skills/file-delivery-queue.json"
            )
            second_authority.parent.mkdir(parents=True, mode=0o700)
            second_authority.write_bytes(authority.read_bytes())
            second_authority.chmod(0o600)
            restored = self.queue.load_policy(str(second_authority))
            self.assertEqual(
                restored.replay_ledger_dir,
                second_home / ".local/state/ai-agents-skills/file-delivery-replay",
            )

    def test_authority_rejects_duplicate_top_level_and_channel_keys(self) -> None:
        valid_tail = (
            '"hmac_key_hex":"' + "11" * 32 + '",'
            '"allowed":{"whatsapp":["target"]},'
            '"max_job_age_seconds":60,"max_media_bytes":1024,'
            '"replay_ledger_dir":"aas-host-state:file-delivery-replay",'
            '"replay_retention_seconds":300,"max_replay_entries":100}'
        )
        cases = {
            "top-level": '{"version":1,"version":1,' + valid_tail,
            "channel": (
                '{"version":1,"hmac_key_hex":"'
                + "11" * 32
                + '","allowed":{"whatsapp":["target"],"whatsapp":["other"]},'
                + '"max_job_age_seconds":60,"max_media_bytes":1024,'
                + '"replay_ledger_dir":"aas-host-state:file-delivery-replay",'
                + '"replay_retention_seconds":300,"max_replay_entries":100}'
            ),
        }
        for label, payload in cases.items():
            with self.subTest(case=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                authority = root / ".config/ai-agents-skills/file-delivery-queue.json"
                authority.parent.mkdir(parents=True, mode=0o700)
                authority.write_text(payload, encoding="utf-8")
                authority.chmod(0o600)
                with self.assertRaisesRegex(
                    self.queue.QueueSecurityError,
                    "duplicate key",
                ):
                    self.queue.load_policy(str(authority))

    def test_missing_or_invalid_authority_path_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cases = (
                str(root / "missing-authority.json"),
                str(root / "invalid\x00authority.json"),
            )
            for authority in cases:
                with self.subTest(authority=repr(authority)), self.assertRaisesRegex(
                    self.queue.QueueSecurityError,
                    "authority could not be read securely",
                ):
                    self.queue.load_policy(authority)

    def test_protected_json_parser_failures_are_security_errors(self) -> None:
        for failure in (ValueError("integer limit"), RecursionError("nested JSON")):
            with self.subTest(failure=type(failure).__name__), mock.patch.object(
                self.queue.json,
                "loads",
                side_effect=failure,
            ), self.assertRaisesRegex(
                self.queue.QueueSecurityError,
                "not valid bounded UTF-8 JSON",
            ):
                self.queue._decode_protected_json(b"{}", label="test record")

    def test_lone_surrogates_fail_closed_at_every_json_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _workspace, authority, _media = self._layout(root)
            authority_record = json.loads(authority.read_text(encoding="utf-8"))
            authority_record["allowed"]["whatsapp"] = ["\ud800"]
            authority.write_text(json.dumps(authority_record), encoding="utf-8")
            authority.chmod(0o600)
            with self.assertRaises(self.queue.QueueSecurityError):
                self.queue.load_policy(str(authority))

            _workspace, valid_authority, valid_media = self._layout(root / "valid")
            with self.assertRaisesRegex(
                self.queue.QueueSecurityError,
                "caption is not valid UTF-8",
            ):
                self.queue.publish_job(
                    _workspace,
                    channel="whatsapp",
                    target="target'quoted",
                    media=valid_media,
                    caption="\ud800",
                    authority=str(valid_authority),
                )

            request = (
                b'{"channel":"whatsapp","target":"target","media":"/tmp/x",'
                b'"caption":"\\ud800"}'
            )
            stdin = mock.Mock()
            stdin.buffer = io.BytesIO(request)
            with mock.patch.object(self.queue.sys, "stdin", stdin), self.assertRaises(
                self.queue.QueueSecurityError
            ):
                self.queue._read_submit_request_stdin()

    def _layout(self, root: Path) -> tuple[Path, Path, Path]:
        workspace = root / "workspace"
        data = workspace / "data"
        data.mkdir(parents=True)
        data.chmod(0o700)
        exports = data / "exports"
        exports.mkdir(mode=0o700)
        authority = root / ".config/ai-agents-skills/file-delivery-queue.json"
        authority.parent.mkdir(parents=True, mode=0o700)
        replay_ledger = root / ".local/state/ai-agents-skills/file-delivery-replay"
        replay_ledger.mkdir(parents=True, mode=0o700)
        authority.write_text(
            json.dumps(
                {
                    "version": 1,
                    "hmac_key_hex": "42" * 32,
                    "allowed": {
                        "whatsapp": ["target'quoted", "second-target"],
                        "signal": ["signal-target"],
                    },
                    "max_job_age_seconds": 60,
                    "max_media_bytes": 4096,
                    "replay_ledger_dir": "aas-host-state:file-delivery-replay",
                    "replay_retention_seconds": 300,
                    "max_replay_entries": 100,
                }
            ),
            encoding="utf-8",
        )
        authority.chmod(0o600)
        media = exports / "payload.pdf"
        media.write_bytes(b"%PDF-1.4\noriginal-snapshot\n")
        media.chmod(0o600)
        return workspace, authority, media

    def _publish(self, root: Path, **kwargs):
        workspace, authority, media = self._layout(root)
        published = self.queue.publish_job(
            workspace,
            channel=kwargs.pop("channel", "whatsapp"),
            target=kwargs.pop("target", "target'quoted"),
            media=kwargs.pop("media", media),
            caption=kwargs.pop("caption", "caption;$(not-code)"),
            authority=str(authority),
            now=kwargs.pop("now", 1_700_000_000),
            nonce=kwargs.pop("nonce", "ab" * 32),
            **kwargs,
        )
        return workspace, authority, media, published

    def test_snapshot_mac_allowlist_and_authenticated_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace, authority, media, published = self._publish(root)
            job_path = workspace / "data/send-queue" / f"{published['job_id']}.json"
            job = json.loads(job_path.read_text(encoding="utf-8"))
            self.assertEqual(job["caption"], "caption;$(not-code)")
            self.assertEqual(job["target"], "target'quoted")
            self.assertRegex(job["mac"], r"^[0-9a-f]{64}$")
            self.assertEqual(job_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(
                (workspace / "data/send-queue").stat().st_mode & 0o777,
                0o700,
            )

            # The original path is no longer authoritative after publication.
            media.write_bytes(b"attacker-replaced-original\n")
            captured: list[bytes] = []

            def sender(_channel, _target, descriptor_path, _caption, pass_fds):
                self.assertEqual(len(pass_fds), 1)
                captured.append(Path(descriptor_path).read_bytes())
                return True

            result = self.queue.process_once(
                workspace,
                authority=str(authority),
                sender=sender,
                now=1_700_000_001,
            )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(captured, [b"%PDF-1.4\noriginal-snapshot\n"])
            authenticated = self.queue.read_result(
                workspace,
                job_id=str(published["job_id"]),
                nonce=str(published["nonce"]),
                media_sha256=str(published["media_sha256"]),
                authority=str(authority),
            )
            self.assertIsNotNone(authenticated)
            self.assertEqual(authenticated["status"], "ok")

    def test_capability_rejects_unlisted_channel_and_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace, authority, media = self._layout(root)
            for channel, target in (
                ("telegram", "target'quoted"),
                ("whatsapp", "not-allowlisted"),
            ):
                with self.subTest(channel=channel, target=target), self.assertRaises(
                    self.queue.QueueSecurityError
                ):
                    self.queue.publish_job(
                        workspace,
                        channel=channel,
                        target=target,
                        media=media,
                        authority=str(authority),
                    )
            self.assertEqual(
                [path.name for path in (workspace / "data").iterdir()],
                ["exports"],
            )

    def test_exact_workflow_export_roots_are_admitted_and_outside_files_are_rejected(self) -> None:
        roots = (
            "data/exports",
            "data/research/zotero/staging",
            "data/calibre/staging",
        )
        for index, relative in enumerate(roots):
            with self.subTest(root=relative), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                workspace, authority, _media = self._layout(root)
                approved = workspace / relative
                approved.mkdir(parents=True, exist_ok=True)
                approved.chmod(0o700)
                media = approved / "approved.pdf"
                media.write_bytes(b"approved")
                media.chmod(0o600)
                published = self.queue.publish_job(
                    workspace,
                    channel="whatsapp",
                    target="target'quoted",
                    media=media,
                    authority=str(authority),
                    now=1_700_000_000,
                    nonce=f"{index + 1:02x}" * 32,
                )
                self.assertRegex(str(published["media_sha256"]), r"^[0-9a-f]{64}$")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace, authority, _media = self._layout(root)
            outside = root / "private-host-file"
            outside.write_text("must-not-export", encoding="utf-8")
            with self.assertRaisesRegex(
                self.queue.QueueSecurityError,
                "outside the authorized export roots",
            ):
                self.queue.publish_job(
                    workspace,
                    channel="whatsapp",
                    target="target'quoted",
                    media=outside,
                    authority=str(authority),
                )

    def test_source_mutation_during_snapshot_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace, authority, media = self._layout(root)
            source_identity = (media.stat().st_dev, media.stat().st_ino)
            real_open = self.queue.os.open
            real_read = self.queue.os.read
            source_fds: set[int] = set()
            mutated = False

            def open_spy(*args, **kwargs):
                descriptor = real_open(*args, **kwargs)
                info = os.fstat(descriptor)
                if (info.st_dev, info.st_ino) == source_identity:
                    source_fds.add(descriptor)
                return descriptor

            def read_and_mutate(descriptor, size):
                nonlocal mutated
                chunk = real_read(descriptor, size)
                if descriptor in source_fds and chunk and not mutated:
                    mutated = True
                    media.write_bytes(b"changed-during-snapshot\n")
                    media.chmod(0o600)
                return chunk

            with (
                mock.patch.object(self.queue.os, "open", side_effect=open_spy),
                mock.patch.object(self.queue.os, "read", side_effect=read_and_mutate),
                self.assertRaisesRegex(
                    self.queue.QueueSecurityError,
                    "changed during snapshot",
                ),
            ):
                self.queue.publish_job(
                    workspace,
                    channel="whatsapp",
                    target="target'quoted",
                    media=media,
                    authority=str(authority),
                )

    def test_expired_or_mac_tampered_jobs_never_reach_sender(self) -> None:
        for case in ("expired", "tampered"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                workspace, authority, _media, published = self._publish(root)
                if case == "tampered":
                    job_path = workspace / "data/send-queue" / f"{published['job_id']}.json"
                    record = json.loads(job_path.read_text(encoding="utf-8"))
                    record["caption"] = "tampered"
                    job_path.write_text(json.dumps(record), encoding="utf-8")
                    job_path.chmod(0o600)
                    now = 1_700_000_001
                else:
                    now = 1_700_000_061
                sender = mock.Mock(return_value=True)
                result = self.queue.process_once(
                    workspace,
                    authority=str(authority),
                    sender=sender,
                    now=now,
                )
                self.assertEqual(result["status"], "rejected")
                sender.assert_not_called()

    def test_malformed_bounded_json_job_is_rejected_without_stopping_worker(self) -> None:
        cases = {
            "integer-digit-limit": '{"version":' + "1" * 5_000 + "}",
            "lone-surrogate": '{"caption":"\\ud800"}',
        }
        for label, malformed in cases.items():
            with self.subTest(case=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                workspace, authority, _media, published = self._publish(root)
                job_path = (
                    workspace
                    / "data/send-queue"
                    / f"{published['job_id']}.json"
                )
                job_path.write_text(malformed, encoding="utf-8")
                job_path.chmod(0o600)
                sender = mock.Mock(return_value=True)

                result = self.queue.process_once(
                    workspace,
                    authority=str(authority),
                    sender=sender,
                    now=1_700_000_001,
                )

                self.assertEqual(result["status"], "rejected")
                sender.assert_not_called()

    def test_media_digest_drift_never_reaches_sender(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace, authority, _media, published = self._publish(root)
            media_dir = workspace / "data/send-queue/media"
            snapshot = next(media_dir.iterdir())
            snapshot.write_bytes(b"same-path-different-media\n")
            snapshot.chmod(0o600)
            sender = mock.Mock(return_value=True)
            result = self.queue.process_once(
                workspace,
                authority=str(authority),
                sender=sender,
                now=1_700_000_001,
            )
            self.assertEqual(result["status"], "rejected")
            sender.assert_not_called()
            self.assertFalse((workspace / "data/send-queue" / f"{published['job_id']}.result").exists())

    def test_missing_media_or_claim_rejects_only_that_job(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace, authority, _media, published = self._publish(root)
            snapshot = next((workspace / "data/send-queue/media").iterdir())
            snapshot.unlink()
            sender = mock.Mock(return_value=True)

            result = self.queue.process_once(
                workspace,
                authority=str(authority),
                sender=sender,
                now=1_700_000_001,
            )

            self.assertEqual(result["status"], "rejected")
            sender.assert_not_called()
            self.assertIsNone(
                self.queue.read_result(
                    workspace,
                    job_id=str(published["job_id"]),
                    nonce=str(published["nonce"]),
                    media_sha256=str(published["media_sha256"]),
                    authority=str(authority),
                )
            )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace, authority, _media, _published = self._publish(root)
            sender = mock.Mock(return_value=True)
            with mock.patch.object(
                self.queue,
                "_read_named_json",
                side_effect=FileNotFoundError,
            ):
                result = self.queue.process_once(
                    workspace,
                    authority=str(authority),
                    sender=sender,
                    now=1_700_000_001,
                )
            self.assertEqual(result["status"], "rejected")
            sender.assert_not_called()

    def test_replay_marker_enforces_at_most_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace, authority, _media, published = self._publish(root)
            queue_dir = workspace / "data/send-queue"
            job_path = queue_dir / f"{published['job_id']}.json"
            saved_job = job_path.read_bytes()
            saved_media_path = next((queue_dir / "media").iterdir())
            saved_media_name = saved_media_path.name
            saved_media = saved_media_path.read_bytes()
            sender = mock.Mock(return_value=True)
            first = self.queue.process_once(
                workspace, authority=str(authority), sender=sender, now=1_700_000_001
            )
            self.assertEqual(first["status"], "ok")
            (queue_dir / f"{published['job_id']}.result").unlink()
            job_path.write_bytes(saved_job)
            job_path.chmod(0o600)
            replay_media = queue_dir / "media" / saved_media_name
            replay_media.write_bytes(saved_media)
            replay_media.chmod(0o600)
            second = self.queue.process_once(
                workspace, authority=str(authority), sender=sender, now=1_700_000_002
            )
            self.assertEqual(second["status"], "replay")
            self.assertEqual(sender.call_count, 1)
            replay_result = self.queue.read_result(
                workspace,
                job_id=str(published["job_id"]),
                nonce=str(published["nonce"]),
                media_sha256=str(published["media_sha256"]),
                authority=str(authority),
            )
            self.assertEqual(replay_result["error_code"], "replay")

    def test_replay_pruning_removes_only_proven_expired_markers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _workspace, authority, _media = self._layout(root)
            policy = self.queue.load_policy(str(authority))
            ledger_fd = self.queue._open_directory_nofollow(
                policy.replay_ledger_dir, private=True
            )
            old = "10" * 32 + ".used"
            fresh = "20" * 32 + ".used"
            malformed = "30" * 32 + ".used"
            unexpected = "unexpected-entry"
            try:
                self.queue._write_exclusive_json(
                    ledger_fd,
                    old,
                    {
                        "version": 1,
                        "job_id": "job-1699999000-1010101010101010",
                        "used_at": 1_699_999_000,
                    },
                )
                self.queue._write_exclusive_json(
                    ledger_fd,
                    fresh,
                    {
                        "version": 1,
                        "job_id": "job-1700000000-2020202020202020",
                        "used_at": 1_700_000_001,
                    },
                )
                self.queue._write_exclusive_json(
                    ledger_fd,
                    malformed,
                    {"version": 1, "job_id": "bad", "used_at": 0},
                )
                os.mkdir(unexpected, mode=0o700, dir_fd=ledger_fd)
                with self.queue._locked_replay_ledger(ledger_fd):
                    active = self.queue._prune_replay_ledger(
                        ledger_fd, policy, now=1_700_000_301
                    )
                names = set(os.listdir(ledger_fd))
            finally:
                os.close(ledger_fd)

            self.assertNotIn(old, names)
            self.assertIn(fresh, names)
            self.assertIn(malformed, names)
            self.assertIn(unexpected, names)
            self.assertEqual(active, 3)

    def test_agent_writable_queue_scans_refuse_excess_directory_fanout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace, authority, _media, _published = self._publish(root)
            sender = mock.Mock(return_value=True)
            with mock.patch.object(self.queue, "MAX_QUEUE_DIRECTORY_ENTRIES", 1):
                with self.assertRaisesRegex(
                    self.queue.QueueSecurityError,
                    "send queue exceeds the 1-entry scan limit",
                ):
                    self.queue.process_once(
                        workspace,
                        authority=str(authority),
                        sender=sender,
                        now=1_700_000_001,
                    )
            sender.assert_not_called()
            job_path = (
                workspace
                / "data/send-queue"
                / f"{_published['job_id']}.json"
            )
            self.assertTrue(job_path.exists())
            with (
                mock.patch.object(self.queue, "MAX_QUEUE_DIRECTORY_ENTRIES", 2),
                mock.patch.object(
                    self.queue.os,
                    "listdir",
                    side_effect=AssertionError("unbounded listing used"),
                ),
            ):
                exact_cap = self.queue.process_once(
                    workspace,
                    authority=str(authority),
                    sender=sender,
                    now=1_700_000_001,
                )
            self.assertEqual(exact_cap["status"], "ok")
            sender.assert_called_once()

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace, _authority, _media, published = self._publish(root)
            media_dir = workspace / "data/send-queue/media"
            (media_dir / "unrelated").write_bytes(b"junk")
            with mock.patch.object(self.queue, "MAX_QUEUE_DIRECTORY_ENTRIES", 1):
                with self.assertRaisesRegex(
                    self.queue.QueueSecurityError,
                    "send queue media exceeds the 1-entry scan limit",
                ):
                    self.queue.cleanup_job(workspace, str(published["job_id"]))

    def test_replay_entry_bound_fails_closed_without_pruning_fresh_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace, authority, _media, _published = self._publish(root)
            policy = self.queue.load_policy(str(authority))
            ledger_fd = self.queue._open_directory_nofollow(
                policy.replay_ledger_dir, private=True
            )
            try:
                for index in range(policy.max_replay_entries - 1):
                    self.queue._write_exclusive_json(
                        ledger_fd,
                        f"{index:064x}.used",
                        {
                            "version": 1,
                            "job_id": f"job-1700000000-{index:016x}",
                            "used_at": 1_700_000_000,
                        },
                    )
                os.mkdir("unexpected-entry", mode=0o700, dir_fd=ledger_fd)
            finally:
                os.close(ledger_fd)
            sender = mock.Mock(return_value=True)

            result = self.queue.process_once(
                workspace,
                authority=str(authority),
                sender=sender,
                now=1_700_000_001,
            )

            self.assertEqual(result["status"], "rejected")
            sender.assert_not_called()
            self.assertEqual(
                len(list(policy.replay_ledger_dir.glob("*.used"))),
                policy.max_replay_entries - 1,
            )
            self.assertTrue((policy.replay_ledger_dir / "unexpected-entry").is_dir())

    def test_replay_entry_bound_admits_the_last_available_slot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace, authority, _media, _published = self._publish(root)
            policy = self.queue.load_policy(str(authority))
            ledger_fd = self.queue._open_directory_nofollow(
                policy.replay_ledger_dir, private=True
            )
            try:
                for index in range(policy.max_replay_entries - 1):
                    self.queue._write_exclusive_json(
                        ledger_fd,
                        f"{index:064x}.used",
                        {
                            "version": 1,
                            "job_id": f"job-1700000000-{index:016x}",
                            "used_at": 1_700_000_000,
                        },
                    )
            finally:
                os.close(ledger_fd)
            sender = mock.Mock(return_value=True)

            result = self.queue.process_once(
                workspace,
                authority=str(authority),
                sender=sender,
                now=1_700_000_001,
            )

            self.assertEqual(result["status"], "ok")
            sender.assert_called_once()
            self.assertEqual(
                len(list(policy.replay_ledger_dir.glob("*.used"))),
                policy.max_replay_entries,
            )

    def test_atomic_claim_contention_does_not_send(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace, authority, _media, _published = self._publish(root)
            sender = mock.Mock(return_value=True)
            with mock.patch.object(self.queue.os, "rename", side_effect=FileNotFoundError):
                result = self.queue.process_once(
                    workspace,
                    authority=str(authority),
                    sender=sender,
                    now=1_700_000_001,
                )
            self.assertEqual(result["status"], "contended")
            sender.assert_not_called()

    def test_forged_or_symlinked_result_is_rejected(self) -> None:
        mutations = {
            "forged": {"mac": "00" * 32},
            "short-mac": {"mac": "0" * 63},
            "long-mac": {"mac": "0" * 65},
            "uppercase-mac": {"mac": "A" * 64},
            "nonhex-mac": {"mac": "g" * 64},
            "nonascii-mac": {"mac": "é"},
            "non-string-status": {"status": []},
            "surrogate-error-code": {"error_code": "\ud800"},
        }
        for case in (*mutations, "symlink"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                workspace, authority, _media, published = self._publish(root)
                queue_dir = workspace / "data/send-queue"
                result_path = queue_dir / f"{published['job_id']}.result"
                if case != "symlink":
                    record = {
                        "version": 1,
                        "job_id": published["job_id"],
                        "nonce": published["nonce"],
                        "media_sha256": published["media_sha256"],
                        "status": "ok",
                        "error_code": "",
                        "completed_at": 1_700_000_001,
                        "mac": "00" * 32,
                    }
                    record.update(mutations[case])
                    result_path.write_text(
                        json.dumps(record),
                        encoding="utf-8",
                    )
                    result_path.chmod(0o600)
                else:
                    outside = root / "outside.result"
                    outside.write_text("{}", encoding="utf-8")
                    result_path.symlink_to(outside)
                with self.assertRaises(self.queue.QueueSecurityError):
                    self.queue.read_result(
                        workspace,
                        job_id=str(published["job_id"]),
                        nonce=str(published["nonce"]),
                        media_sha256=str(published["media_sha256"]),
                        authority=str(authority),
                    )

                with self.assertRaisesRegex(
                    self.queue.QueueSecurityError,
                    "result expectation is invalid",
                ):
                    self.queue.read_result(
                        workspace,
                        job_id=str(published["job_id"]),
                        nonce=str(published["nonce"]),
                        media_sha256="g" * 64,
                        authority=str(authority),
                    )

    def test_worker_never_accepts_symlinked_queue_or_media(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace, authority, media = self._layout(root)
            outside = root / "outside"
            outside.mkdir()
            (workspace / "data/send-queue").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(self.queue.QueueSecurityError):
                self.queue.publish_job(
                    workspace,
                    channel="whatsapp",
                    target="target'quoted",
                    media=media,
                    authority=str(authority),
                )
            self.assertEqual(list(outside.iterdir()), [])

    @unittest.skipUnless(
        _attested_node_available(),
        "the fixed root-controlled /usr/bin/node delivery runtime is unavailable",
    )
    def test_host_sender_ignores_path_and_executes_a_bound_launcher_descriptor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "openclaw"
            original.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            original.chmod(0o755)
            descriptor = os.open(original, os.O_RDONLY)
            node_descriptor = os.open("/usr/bin/node", os.O_RDONLY)
            replacement = root / "replacement"
            replacement.write_text("#!/bin/sh\nexit 97\n", encoding="utf-8")
            replacement.chmod(0o755)
            original.unlink()
            replacement.rename(original)
            captured: dict[str, object] = {}
            process = mock.Mock(pid=4242, returncode=0)
            process.communicate.return_value = (None, None)

            def fake_popen(command, **kwargs):
                captured["command"] = command
                captured["env"] = kwargs["env"]
                captured["pass_fds"] = kwargs["pass_fds"]
                captured["stdin"] = kwargs["stdin"]
                captured["stdout"] = kwargs["stdout"]
                captured["stderr"] = kwargs["stderr"]
                captured["start_new_session"] = kwargs["start_new_session"]
                return process

            hostile = root / "fake-bin"
            hostile.mkdir()
            with (
                mock.patch.object(
                    self.queue,
                    "_open_trusted_delivery_cli",
                    return_value=descriptor,
                ),
                mock.patch.object(
                    self.queue,
                    "_open_trusted_node_runtime",
                    return_value=node_descriptor,
                ),
                mock.patch.object(self.queue.subprocess, "Popen", side_effect=fake_popen),
                mock.patch.dict(
                    os.environ,
                    {
                        "PATH": str(hostile),
                        "HOME": str(root),
                        "XDG_CONFIG_HOME": str(root / "must-not-pass"),
                        "AAS_FILE_DELIVERY_SECRETS_FILE": str(root / "must-not-pass"),
                    },
                ),
            ):
                sent = self.queue._safe_sender(
                    "telegram", "recipient-canary-f41c", "/proc/self/fd/99", "", (99,)
                )

            self.assertTrue(sent)
            self.assertTrue(str(captured["command"][0]).startswith("/proc/self/fd/"))
            self.assertIn(descriptor, captured["pass_fds"])
            self.assertIn(node_descriptor, captured["pass_fds"])
            request = json.loads(process.communicate.call_args.kwargs["input"])
            self.assertEqual(
                request,
                {
                    "channel": "telegram",
                    "target": "recipient-canary-f41c",
                    "media": "/proc/self/fd/99",
                    "caption": "",
                },
            )
            command_text = "\0".join(str(value) for value in captured["command"])
            self.assertNotIn("recipient-canary-f41c", command_text)
            self.assertNotIn("/proc/self/fd/99", command_text)
            self.assertEqual(captured["env"]["PATH"], "/usr/local/bin:/usr/bin:/bin")
            self.assertEqual(captured["env"]["NODE_DISABLE_COMPILE_CACHE"], "1")
            self.assertNotIn("XDG_CONFIG_HOME", captured["env"])
            self.assertNotIn("AAS_FILE_DELIVERY_SECRETS_FILE", captured["env"])
            self.assertIs(captured["stdin"], self.queue.subprocess.PIPE)
            self.assertTrue(captured["start_new_session"])
            self.assertEqual(process.communicate.call_args.kwargs["timeout"], 120)

    @unittest.skipIf(os.name == "nt", "POSIX process-group containment")
    def test_delivery_communication_failures_kill_group_and_reap_leader(self) -> None:
        failures = (
            self.queue.subprocess.TimeoutExpired(["node"], 0.01),
            OSError("delivery pipe failed"),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                process = mock.Mock(pid=4242, returncode=None)
                process.stdin = None
                process.communicate.side_effect = failure
                process.wait.return_value = 0
                with (
                    mock.patch.object(
                        self.queue.subprocess,
                        "Popen",
                        return_value=process,
                    ) as popen,
                    mock.patch.object(self.queue.os, "killpg") as killpg,
                ):
                    sent = self.queue._run_delivery_process(
                        ["/proc/self/fd/10"],
                        request=b"{}",
                        child_env={"PATH": "/usr/bin:/bin"},
                        pass_fds=(10,),
                        timeout=0.01,
                    )

                self.assertFalse(sent)
                self.assertTrue(popen.call_args.kwargs["start_new_session"])
                killpg.assert_called_once_with(4242, self.queue.signal.SIGKILL)
                process.wait.assert_called_once_with(timeout=5)

    @unittest.skipIf(os.name == "nt", "POSIX process-group containment")
    def test_delivery_nonzero_exit_does_not_signal_a_reaped_process_group(self) -> None:
        process = mock.Mock(pid=4242, returncode=7)
        process.communicate.return_value = (None, None)
        with (
            mock.patch.object(
                self.queue.subprocess,
                "Popen",
                return_value=process,
            ),
            mock.patch.object(self.queue.os, "killpg") as killpg,
        ):
            sent = self.queue._run_delivery_process(
                ["/proc/self/fd/10"],
                request=b"{}",
                child_env={"PATH": "/usr/bin:/bin"},
                pass_fds=(10,),
            )

        self.assertFalse(sent)
        killpg.assert_not_called()

    @unittest.skipUnless(
        _attested_node_available(),
        "the fixed root-controlled /usr/bin/node delivery runtime is unavailable",
    )
    def test_host_sender_keeps_delivery_metadata_out_of_live_child_cmdline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            entry = root / "openclaw.mjs"
            media = root / "media.bin"
            target = "recipient-canary-9e3b"
            caption = "caption-canary-87a1"
            media.write_bytes(b"bound-media")
            media_descriptor = os.open(media, os.O_RDONLY)
            media_path = self.queue._fd_path(media_descriptor)
            entry.write_text(
                "import { readFileSync } from 'node:fs';\n"
                "const raw = readFileSync('/proc/self/cmdline', 'utf8');\n"
                f"for (const value of {json.dumps([target, caption, media_path])}) {{\n"
                "  if (raw.includes(value)) throw new Error('delivery metadata reached cmdline');\n"
                "  if (!process.argv.includes(value)) throw new Error('adapter lost delivery metadata');\n"
                "}\n"
                f"if (readFileSync({json.dumps(media_path)}, 'utf8') !== 'bound-media') "
                "throw new Error('media descriptor was not inherited');\n",
                encoding="utf-8",
            )
            entry_descriptor = os.open(entry, os.O_RDONLY)
            try:
                with mock.patch.object(
                    self.queue,
                    "_open_trusted_delivery_cli",
                    return_value=entry_descriptor,
                ):
                    sent = self.queue._safe_sender(
                        "telegram",
                        target,
                        media_path,
                        caption,
                        (media_descriptor,),
                    )
                self.assertTrue(sent)
            finally:
                os.close(media_descriptor)

    def test_host_sender_does_not_search_hostile_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fake = Path(temporary) / "openclaw"
            fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake.chmod(0o755)
            with mock.patch.dict(os.environ, {"PATH": str(fake.parent)}):
                with self.assertRaisesRegex(
                    self.queue.QueueSecurityError,
                    "fixed root-controlled",
                ):
                    self.queue._open_trusted_delivery_cli()

    def test_manifest_installs_protocol_module(self) -> None:
        manifest = json.loads((ROOT / "manifest/runtime.yaml").read_text(encoding="utf-8"))
        files = manifest["skills"]["zotero"]["files"]
        targets = {entry["target"] for entry in files}
        self.assertIn("workspace/skills/zotero/send_queue.py", targets)


if __name__ == "__main__":
    unittest.main()
