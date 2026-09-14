"""Tests for the Stalker-portal-rejection backoff path in the retry loop.

Host 185.80.197.39 (a Stalker/Ministra portal) was observed rejecting every
playback attempt with HTTP 456 while handshake/get_events/create_link all
succeeded. Before this fix, the retry loop treated that identically to any
other transport failure: a ~0.25s-per-failure backoff (capped at 3s), so five
retries re-authenticated a brand-new session against the portal in ~10-16
seconds. These tests cover the two pieces that change that:

1. ``_note_http_reader_rejection`` classifies a portal-rejection status code
   (456) from the HTTP reader thread as a distinct failure reason.
2. ``_reconnect_backoff_seconds`` uses a much longer backoff once that reason
   is recorded, instead of the generic schedule.
"""
from collections import deque

from django.test import SimpleTestCase

from apps.proxy.live_proxy.input.manager import StreamManager


def _make_manager():
    sm = StreamManager.__new__(StreamManager)
    sm.channel_id = "test-channel"
    sm.url = "http://185.80.197.39:80/play/live.php?mac=00:1A:79:2A:75:37"
    sm.transcode = False
    sm.http_reader = None
    sm.last_transport_failure = None
    sm.last_transport_failure_at = None
    sm.recent_stderr_lines = deque(maxlen=20)
    return sm


class _FakeHttpReader:
    def __init__(self, last_status_code):
        self.last_status_code = last_status_code


class NoteHttpReaderRejectionTests(SimpleTestCase):
    def test_records_stalker_portal_rejected_reason_for_known_status(self):
        sm = _make_manager()
        sm.http_reader = _FakeHttpReader(456)

        sm._note_http_reader_rejection()

        self.assertIsNotNone(sm.last_transport_failure)
        self.assertEqual(sm.last_transport_failure["reason"], "stalker_portal_rejected")
        self.assertEqual(sm.last_transport_failure["details"]["status_code"], 456)

    def test_ignores_successful_or_unset_status(self):
        sm = _make_manager()
        sm.http_reader = _FakeHttpReader(200)

        sm._note_http_reader_rejection()

        self.assertIsNone(sm.last_transport_failure)

    def test_ignores_other_error_status_codes(self):
        sm = _make_manager()
        sm.http_reader = _FakeHttpReader(503)

        sm._note_http_reader_rejection()

        self.assertIsNone(sm.last_transport_failure)

    def test_no_op_without_an_http_reader(self):
        sm = _make_manager()
        sm.http_reader = None

        sm._note_http_reader_rejection()

        self.assertIsNone(sm.last_transport_failure)


class ReconnectBackoffSecondsTests(SimpleTestCase):
    def test_uses_longer_backoff_after_portal_rejection(self):
        sm = _make_manager()
        sm.last_transport_failure = {
            "reason": "stalker_portal_rejected",
            "details": {"status_code": 456},
        }

        self.assertEqual(sm._reconnect_backoff_seconds(1), 2)
        self.assertEqual(sm._reconnect_backoff_seconds(4), 8)
        # Capped at 10s even with many failures.
        self.assertEqual(sm._reconnect_backoff_seconds(10), 10)

    def test_uses_default_short_backoff_for_generic_failures(self):
        sm = _make_manager()
        sm.last_transport_failure = {"reason": "read_timeout", "details": {}}

        self.assertEqual(sm._reconnect_backoff_seconds(1), 0.25)
        # Capped at 3s, unlike the portal-rejection schedule.
        self.assertEqual(sm._reconnect_backoff_seconds(20), 3)

    def test_uses_default_short_backoff_when_no_failure_recorded_yet(self):
        sm = _make_manager()

        self.assertEqual(sm._reconnect_backoff_seconds(2), 0.5)
