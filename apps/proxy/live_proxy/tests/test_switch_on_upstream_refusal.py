"""An upstream that refuses a stream URL (HTTP 4xx) triggers failover at once.

Seen in production: a Stalker stream answered HTTP 405 five times in a row.
Each retry re-created the URL and waited, so the channel switched to its
backup stream only after ~16 s; the player gave up twice meanwhile. When
another stream exists, a refusal now switches on the first answer.

The tests go through the real connection teardown (_close_socket), which
clears the HTTP reader before the retry loop looks at the status.
"""

from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from django.test import TestCase

from apps.proxy.live_proxy.input.manager import StreamManager
from apps.proxy.live_proxy.server import ProxyServer

CHANNEL_ID = 'refusal-channel'


class _FakeReader:
    def __init__(self, status_code):
        self.last_status_code = status_code

    def stop(self):
        pass


class SwitchOnUpstreamRefusalTests(TestCase):
    def setUp(self):
        self.redis = Mock()
        buffer = SimpleNamespace(channel_id=CHANNEL_ID, redis_client=self.redis, stopping=False)
        self.manager = StreamManager(
            CHANNEL_ID, 'http://portal.example.com/play/live.php?stream=1', buffer,
            stream_id=1, worker_id='test-worker', channel_name='HGTV - US',
        )
        self.manager.max_retries = 5
        server = SimpleNamespace(
            stream_managers={CHANNEL_ID: self.manager},
            _live_stream_managers={CHANNEL_ID: self.manager},
            stream_buffers={CHANNEL_ID: buffer},
        )
        self.enterContext(patch.object(ProxyServer, 'get_instance', return_value=server))
        for name in ('_monitor_health', '_close_all_connections', '_refresh_runtime_stream_url'):
            self.enterContext(patch.object(self.manager, name))
        self.enterContext(patch.object(self.manager, '_ensure_owner_or_stop', return_value=True))
        self.enterContext(patch.object(self.manager, '_stop_if_idle', return_value=False))
        self.enterContext(patch('apps.proxy.live_proxy.input.manager.close_old_connections'))
        self.enterContext(patch('apps.proxy.live_proxy.input.manager.connection'))
        self.enterContext(patch('apps.proxy.live_proxy.input.manager.log_system_event'))
        self.sleep = self.enterContext(patch('apps.proxy.live_proxy.input.manager.gevent.sleep'))

        # Status the upstream answers, per stream id.
        self.status_by_stream = {1: 405, 2: None}

        def establish():
            self.manager.http_reader = _FakeReader(
                self.status_by_stream.get(self.manager.current_stream_id)
            )
            self.manager.connected = True
            return True

        self.establish = self.enterContext(
            patch.object(self.manager, '_establish_http_connection', side_effect=establish)
        )

        def read_until_eof():
            if self.manager.current_stream_id == 2:
                # The backup plays until the channel stops.
                self.manager.stop_requested = True
                self.manager.running = False
                return
            # EOF after the refusal: fetch_chunk() tears the connection down,
            # which clears the reader before the retry loop runs.
            self.manager._close_socket()

        self.enterContext(
            patch.object(self.manager, '_process_stream_data', side_effect=read_until_eof)
        )

        def switch(reason=None):
            self.manager.current_stream_id = 2
            self.manager.url = 'http://backup.example.com/live.ts'
            return True

        self.switch = self.enterContext(
            patch.object(self.manager, '_try_next_stream_with_cooldown', side_effect=switch)
        )

    def _alternates(self, streams):
        return self.enterContext(patch(
            'apps.proxy.live_proxy.input.manager.get_alternate_streams', return_value=streams,
        ))

    def test_refusal_with_a_backup_switches_after_the_first_answer(self):
        self._alternates([{'stream_id': 2}])

        self.manager.run()

        # One refused attempt, then the backup's connection.
        self.assertEqual(self.establish.call_count, 2)
        self.switch.assert_called_once_with(reason='upstream_refused')

    def test_refusal_without_a_backup_keeps_retrying_the_same_stream(self):
        self._alternates([])
        self.switch.side_effect = lambda reason=None: False

        self.manager.run()

        self.assertEqual(self.establish.call_count, 5)
        self.switch.assert_called_once_with(reason='max_retries_exceeded')

    def test_failed_early_switch_resumes_the_usual_retries(self):
        # A stream is listed, but switching to it fails (e.g. same URL).
        self._alternates([{'stream_id': 2}])
        self.switch.side_effect = lambda reason=None: False

        self.manager.run()

        self.assertEqual(self.establish.call_count, 5)
        self.assertEqual(
            self.switch.call_args_list,
            [call(reason='upstream_refused'), call(reason='max_retries_exceeded')],
        )

    def test_server_errors_keep_the_usual_retries(self):
        self._alternates([{'stream_id': 2}])
        self.status_by_stream[1] = 502  # may be transient
        self.switch.side_effect = lambda reason=None: False

        self.manager.run()

        self.assertEqual(self.establish.call_count, 5)
        self.switch.assert_called_once_with(reason='max_retries_exceeded')

    def test_refusal_on_the_last_attempt_with_a_failed_switch_ends_normally(self):
        self.manager.max_retries = 1
        self._alternates([{'stream_id': 2}])
        self.switch.side_effect = lambda reason=None: False

        self.manager.run()  # must return, not spin

        self.establish.assert_called_once_with()
        self.switch.assert_called_once_with(reason='upstream_refused')

    def test_status_from_an_earlier_connection_is_not_reused(self):
        # e.g. an HTTP 405 kept before a switch to an ffmpeg stream
        self._alternates([{'stream_id': 2}])
        self.status_by_stream[1] = None  # EOF without any HTTP status
        self.manager._last_upstream_status = 405
        self.switch.side_effect = lambda reason=None: False

        self.manager.run()

        self.switch.assert_called_once_with(reason='max_retries_exceeded')

    def test_backup_tried_during_a_failed_early_switch_stays_eligible(self):
        self._alternates([{'stream_id': 2}])
        tried_at_switch = []

        def switch(reason=None):
            tried_at_switch.append(set(self.manager.tried_stream_ids))
            self.manager.tried_stream_ids.add(2)  # the backup failed to resolve
            return False

        self.switch.side_effect = switch

        self.manager.run()

        self.assertNotIn(2, tried_at_switch[1])

    def test_stable_playback_ends_the_failure_episode(self):
        self.manager._refusal_switch_failed_stream_id = 1
        self.manager._note_stable_connection()
        self.assertIsNone(self.manager._refusal_switch_failed_stream_id)

    def test_portal_busy_status_keeps_its_longer_backoff(self):
        # 456: the portal still sees an older session; waiting is the fix.
        self._alternates([{'stream_id': 2}])
        self.status_by_stream[1] = 456
        self.switch.side_effect = lambda reason=None: False

        self.manager.run()

        self.assertEqual(self.establish.call_count, 5)
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list[:4]], [2, 4, 6, 8])

    def test_a_refusal_is_acted_on_only_once(self):
        self._alternates([{'stream_id': 2}])
        self.manager.http_reader = _FakeReader(405)
        self.manager._close_socket()

        self.assertTrue(self.manager._switch_now_after_refusal())
        self.assertFalse(self.manager._switch_now_after_refusal())

    def test_no_early_switch_while_another_switch_is_under_way(self):
        self._alternates([{'stream_id': 2}])
        self.manager.http_reader = _FakeReader(405)
        self.manager.needs_stream_switch = True

        self.assertFalse(self.manager._switch_now_after_refusal())

    def test_refusal_outdated_by_a_stream_change_during_the_lookup(self):
        def lookup(channel_id, stream_id):
            self.manager.current_stream_id = 3  # manual switch landed meanwhile
            return [{'stream_id': 2}]

        self.enterContext(patch(
            'apps.proxy.live_proxy.input.manager.get_alternate_streams', side_effect=lookup,
        ))
        self.manager.http_reader = _FakeReader(405)

        self.assertFalse(self.manager._switch_now_after_refusal())
