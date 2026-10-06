"""Offline delivery identity and uncertain-acceptance regressions."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import test_remote_bridge as fixtures


class IntentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        helper = fixtures.RemoteBridgeStructuredNotify()
        self.mod = helper._mod()
        self.event = helper._event(self.mod)
        self.path = self.root / 'event.json'
        self.path.write_text(json.dumps(self.event), encoding="utf-8")
        self.args = helper._send_args(self.path)
        self.cfg = self.mod.BridgeConfig(raw={}, secrets_path=None,
            zulip={'site': 'https://example.invalid', 'email': 'test' + chr(64) + 'example.invalid', 'api_key': 'synthetic'})
        self.env = mock.patch.dict(os.environ, {'AAS_REMOTE_BRIDGE_STATE': str(self.root / 'state'), 'AAS_REMOTE_BRIDGE_SYNC': '0'})
        self.env.start(); self.addCleanup(self.env.stop)
        self.config = mock.patch.object(self.mod, 'build_config', return_value=self.cfg)
        self.config.start(); self.addCleanup(self.config.stop)
        self.http = mock.patch.object(self.mod, 'http_json', side_effect=AssertionError('unconfigured transport'))
        self.http.start(); self.addCleanup(self.http.stop)

    def send(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return self.mod.cmd_send(self.args)

    def test_same_event_can_reach_two_authorized_topics(self):
        with mock.patch.object(self.mod, 'notify_channels', return_value={'zulip': {'ok': True}}) as send:
            self.args.job = 'topic-one'; self.assertEqual(self.send(), 0)
            self.args.job = 'topic-two'; self.assertEqual(self.send(), 0)
        self.assertEqual(send.call_count, 2)

    def test_transport_exception_is_not_blindly_retried(self):
        with mock.patch.object(self.mod, 'notify_channels', side_effect=TimeoutError('synthetic disconnect')) as send:
            self.assertEqual(self.send(), 1)
            self.assertEqual(self.send(), 1)
        self.assertEqual(send.call_count, 1)

    def test_plaintext_uses_the_same_durable_identity_path(self):
        self.args.event_json = None; self.args.text = 'Synthetic milestone'; self.args.job = 'paper'
        with mock.patch.object(self.mod, 'notify_channels', return_value={'zulip': {'ok': True}}) as send:
            self.assertEqual(self.send(), 0); self.assertEqual(self.send(), 0)
        self.assertEqual(send.call_count, 1)

    def test_same_identity_with_changed_content_is_refused(self):
        with mock.patch.object(self.mod, 'notify_channels', return_value={'zulip': {'ok': True}}) as send:
            self.assertEqual(self.send(), 0)
            self.event['sections']['current'] = 'Different state needs a new transition identity.'
            self.path.write_text(json.dumps(self.event), encoding="utf-8")
            self.assertEqual(self.send(), 1)
        self.assertEqual(send.call_count, 1)

    def test_distinct_milestones_do_not_share_iteration_updated_title(self):
        notify = self.mod.load_notify_v2_module()
        titles = [notify._title_text({**self.event, 'event': name}) for name in
                  ['synthesis_started', 'synthesis_completed', 'loop_completed']]
        self.assertEqual(len(set(titles)), 3)

    def test_route_alias_does_not_create_a_second_destination(self):
        with mock.patch.object(self.mod, 'notify_channels', return_value={'zulip': {'ok': True}}) as send:
            self.args.channel = 'auto'; self.assertEqual(self.send(), 0)
            self.args.channel = 'zulip'; self.assertEqual(self.send(), 0)
        self.assertEqual(send.call_count, 1)

    def test_adding_a_fallback_does_not_resend_acknowledged_primary(self):
        with mock.patch.object(self.mod, 'notify_channels', return_value={'zulip': {'ok': True}}) as send:
            self.assertEqual(self.send(), 0)
            self.cfg.telegram = {'bot_token': 'synthetic', 'allowed_chat_ids': ['123']}
            self.assertEqual(self.send(), 0)
        self.assertEqual(send.call_count, 1)

    def test_missing_or_empty_registry_never_reopens_a_delivered_event(self):
        for corruption in [None, '{}']:
            with self.subTest(corruption=corruption), tempfile.TemporaryDirectory() as tmp, \
                 mock.patch.dict(os.environ, {'AAS_REMOTE_BRIDGE_STATE': tmp}), \
                 mock.patch.object(self.mod, 'notify_channels', return_value={'zulip': {'ok': True}}) as send:
                self.assertEqual(self.send(), 0)
                path = self.mod._notify_delivery_path(self.mod.Mailbox())
                if corruption is None:
                    path.unlink()
                else:
                    path.write_text(corruption, encoding="utf-8")
                self.assertNotEqual(self.send(), 0)
                self.assertEqual(send.call_count, 1)

    def test_unspecified_failure_outcome_is_unknown_not_retryable(self):
        with mock.patch.object(self.mod, 'notify_channels', return_value={'zulip': {'ok': False}}) as send:
            self.assertEqual(self.send(), 1)
            self.assertEqual(self.send(), 1)
        self.assertEqual(send.call_count, 1)

    def test_only_three_definitely_not_sent_attempts_are_admitted(self):
        with mock.patch.object(self.mod, 'notify_channels', return_value={
                'zulip': {'ok': False, 'outcome': 'definitely_not_sent'}}) as send:
            for _ in range(4):
                self.assertEqual(self.send(), 1)
        self.assertEqual(send.call_count, 3)

    def test_unknown_telegram_html_acceptance_has_no_plaintext_retry(self):
        self.cfg.telegram = {'bot_token': 'synthetic'}
        with mock.patch.object(self.mod, 'http_json', return_value={}) as http:
            result = self.mod.telegram_send(self.cfg, chat_id='123', text='<b>message</b>', parse_mode='HTML')
        self.assertEqual(http.call_count, 1)
        self.assertEqual(result['outcome'], 'outcome_unknown')

    def test_partial_telegram_disconnect_retains_acknowledged_chunk(self):
        self.cfg.telegram = {'bot_token': 'synthetic'}
        with mock.patch.object(self.mod, 'http_json', side_effect=[
                {'ok': True, 'result': {'message_id': 42}}, TimeoutError('synthetic timeout')]):
            result = self.mod.telegram_send(self.cfg, chat_id='123', text='x' * 5000)
        self.assertEqual(result['outcome'], 'outcome_unknown')
        self.assertEqual(result['results'][0]['result']['message_id'], 42)

    def test_explicit_fallback_destination_reuses_its_acknowledgement(self):
        self.cfg.telegram = {'bot_token': 'synthetic', 'allowed_chat_ids': ['123']}
        with mock.patch.object(self.mod, 'zulip_send', return_value={
                'ok': False, 'outcome': 'definitely_not_sent'}), \
             mock.patch.object(self.mod, 'telegram_send', return_value={
                 'ok': True, 'outcome': 'acknowledged', 'results': [{'ok': True, 'result': {'message_id': 42}}]}) as send:
            self.args.channel = 'zulip'; self.assertEqual(self.send(), 0)
            self.args.channel = 'telegram'; self.assertEqual(self.send(), 0)
        self.assertEqual(send.call_count, 1)

    def test_partial_chunk_ids_remain_in_intent_without_replay(self):
        self.cfg.telegram = {'bot_token': 'synthetic', 'allowed_chat_ids': ['123']}
        self.args.event_json = None; self.args.text = 'x' * 5000; self.args.channel = 'telegram'
        with mock.patch.object(self.mod, 'http_json', side_effect=[
                {'ok': True, 'result': {'message_id': 42}}, TimeoutError('synthetic disconnect')]) as send:
            self.assertEqual(self.send(), 1)
            self.assertEqual(self.send(), 1)
        self.assertEqual(send.call_count, 2)
        registry = self.mod._secure_notification_registry_read(self.mod.Mailbox())
        self.assertTrue(any(42 in record.get('remote_ids', []) for record in registry['intents'].values()))
        self.assertTrue(all(record['state'] == 'outcome_unknown' for record in registry['intents'].values()))

    def test_oversized_state_keeps_existing_unknown_outcome(self):
        mailbox = self.mod.Mailbox()
        key = 'a' * 64; payload_hash = 'b' * 64
        self.mod.notification_intent(key, payload_hash, mailbox, text='first')
        self.mod.notification_intent(key, payload_hash, mailbox, outcome='outcome_unknown')
        with self.assertRaises(OSError):
            self.mod.notification_intent('c' * 64, 'd' * 64, mailbox, text='x' * 2_000_000)
        registry = self.mod._secure_notification_registry_read(mailbox)
        self.assertEqual(set(registry['intents']), {key})
        self.assertEqual(registry['intents'][key]['state'], 'outcome_unknown')


class RawIntentTests(unittest.TestCase):
    def test_explicit_milestone_events_use_normal_transport_gate(self):
        import test_autonomous_research_loop as arl_fixtures
        runtime = arl_fixtures.NotifyPolicyTests()._mod()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime.init_loop(runtime.selftest_init_args(root, max_iterations=3))
            with mock.patch.dict(os.environ, {'AAS_AUTOLOOP_EXTERNAL_NOTIFY_EGRESS': 'allow'}), \
                 mock.patch.object(runtime, 'resolve_remote_notify_argv', return_value=['mock-transport']), \
                 mock.patch.object(runtime.subprocess, 'run', return_value=mock.Mock(returncode=0, stdout=json.dumps({
                     'ok': True, 'delivery': {'delivered': True}}))) as send:
                for name in ['synthesis_started', 'synthesis_completed', 'loop_completed']:
                    runtime.emit_loop_progress(root, name, notify_channel='zulip', to_stderr=False)
                self.assertEqual(send.call_count, 3)
                runtime.emit_loop_progress(root, 'run_completed', notify_channel='off', to_stderr=False)
                self.assertEqual(send.call_count, 3)

    def test_raw_hook_cannot_replay_after_unknown_exit_or_success(self):
        import test_autonomous_research_loop as arl_fixtures
        runtime = arl_fixtures.NotifyPolicyTests()._mod()
        for outcome in [TimeoutError('synthetic'), mock.Mock(returncode=0), mock.Mock(returncode=1)]:
            with self.subTest(outcome=type(outcome).__name__), tempfile.TemporaryDirectory() as tmp, \
                 mock.patch.dict(os.environ, {'AAS_ALLOW_RAW_NOTIFY_CMD': '1'}), \
                 mock.patch.object(runtime.subprocess, 'run', side_effect=outcome if isinstance(outcome, Exception) else None,
                                   return_value=outcome) as send:
                payload = {'AUTOLOOP_DIR': tmp, 'AUTOLOOP_EVENT_ID': 'stable-event',
                           'AUTOLOOP_EVENT': 'terminal', 'AUTOLOOP_TEXT': 'One completion.'}
                runtime.watch_notify('operator-approved-hook', payload)
                runtime.watch_notify('operator-approved-hook', payload)
                self.assertEqual(send.call_count, 1)

    def test_raw_hook_outcome_cannot_overwrite_a_concurrent_event(self):
        import test_autonomous_research_loop as arl_fixtures
        runtime = arl_fixtures.NotifyPolicyTests()._mod()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {'AAS_ALLOW_RAW_NOTIFY_CMD': '1'}):
            path = Path(tmp) / 'raw_notify_intents.json'
            def concurrent_event(*args, **kwargs):
                state = json.loads(path.read_text(encoding="utf-8"))
                state['intents']['concurrent'] = {'state': 'attempted', 'semantic_sha256': 'a' * 64}
                runtime.commit_transaction(Path(tmp), json_files={path.name: state})
                return mock.Mock(returncode=0)
            with mock.patch.object(runtime.subprocess, 'run', side_effect=concurrent_event):
                result = runtime.watch_notify('approved-hook', {'AUTOLOOP_DIR': tmp, 'AUTOLOOP_EVENT_ID': 'one',
                                               'AUTOLOOP_EVENT': 'terminal', 'AUTOLOOP_TEXT': 'Completed.'})
            self.assertEqual(result['status'], 'unknown')
            self.assertIn('concurrent', json.loads(path.read_text(encoding="utf-8"))['intents'])


if __name__ == '__main__':
    unittest.main()
