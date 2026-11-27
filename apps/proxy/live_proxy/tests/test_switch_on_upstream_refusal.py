"""An upstream that refuses a stream URL (HTTP 4xx) triggers failover at once.

Seen in production: a Stalker stream answered HTTP 405 five times in a row.
Each retry re-created the URL and waited, so the channel switched to its
backup stream only after ~16 s; the player gave up twice meanwhile. When
another stream exists, a refusal now switches on the first answer.
"""

from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import TestCase

from apps.proxy.live_proxy.input.manager import StreamManager
from apps.proxy.live_proxy.server import ProxyServer

CHANNEL_ID = 'refusal-channel'


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
        for name in (
            '_monitor_health', '_close_socket', '_close_all_connections',
            '_refresh_runtime_stream_url', '_process_stream_data',
        ):
            self.enterContext(patch.object(self.manager, name))
        self.enterContext(patch.object(self.manager, '_ensure_owner_or_stop', return_value=True))
        self.enterContext(patch.object(self.manager, '_stop_if_idle', return_value=False))
        self.enterContext(patch('apps.proxy.live_proxy.input.manager.close_old_connections'))
        self.enterContext(patch('apps.proxy.live_proxy.input.manager.connection'))
        self.enterContext(patch('apps.proxy.live_proxy.input.manager.log_system_event'))
        self.enterContext(patch('apps.proxy.live_proxy.input.manager.gevent.sleep'))

        self.status = 405

        def establish():
            # The HTTP reader thread records the upstream status, then ends.
            self.manager.http_reader = SimpleNamespace(last_status_code=self.status)
            return True

        self.establish = self.enterContext(
            patch.object(self.manager, '_establish_http_connection', side_effect=establish)
        )

        def switch(reason=None):
            # The backup plays until the channel stops.
            self.status = None
            self.manager.stop_requested = True
            self.manager.running = False
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

        self.establish.assert_called_once_with()
        self.switch.assert_called_once_with(reason='max_retries_exceeded')

    def test_refusal_without_a_backup_keeps_retrying_the_same_stream(self):
        self._alternates([])
        self.switch.side_effect = lambda reason=None: False

        self.manager.run()

        self.assertEqual(self.establish.call_count, 5)

    def test_other_failures_keep_the_usual_retries(self):
        self._alternates([{'stream_id': 2}])
        self.status = 502  # server error: may be transient

        self.switch.side_effect = lambda reason=None: False
        self.manager.run()

        self.assertEqual(self.establish.call_count, 5)

    def test_portal_busy_status_keeps_its_backoff_and_retries(self):
        # 456: the portal still sees an older session; waiting is the fix.
        self._alternates([{'stream_id': 2}])
        self.status = 456
        self.switch.side_effect = lambda reason=None: False

        self.manager.run()

        self.assertEqual(self.establish.call_count, 5)

    def test_a_refusal_is_acted_on_only_once(self):
        self._alternates([{'stream_id': 2}])
        self.manager.http_reader = SimpleNamespace(last_status_code=405)

        self.assertTrue(self.manager._switch_now_after_refusal())
        self.assertFalse(self.manager._switch_now_after_refusal())
