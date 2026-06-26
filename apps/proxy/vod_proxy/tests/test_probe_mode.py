from django.test import TestCase

from apps.proxy.vod_proxy import views


class TestProbeModeHeuristics(TestCase):
    def setUp(self):
        views._probe_activity.clear()

    def tearDown(self):
        views._probe_activity.clear()

    def test_probe_mode_requires_scan_burst(self):
        user_agent = "Lavf/60.16.100"
        kwargs = {
            "client_ip": "10.0.0.10",
            "client_user_agent": user_agent,
            "range_header": "bytes=0-",
            "session_id": None,
            "offset": None,
            "utc_start": None,
            "utc_end": None,
        }

        self.assertFalse(
            views._should_use_probe_mode(
                content_type="movie",
                content_id="movie-1",
                **kwargs,
            )
        )
        self.assertFalse(
            views._should_use_probe_mode(
                content_type="movie",
                content_id="movie-2",
                **kwargs,
            )
        )
        self.assertTrue(
            views._should_use_probe_mode(
                content_type="movie",
                content_id="movie-3",
                **kwargs,
            )
        )

    def test_probe_mode_accepts_non_zero_open_ended_ranges_during_burst(self):
        user_agent = "Lavf/60.16.100"
        kwargs = {
            "client_ip": "10.0.0.10",
            "client_user_agent": user_agent,
            "range_header": "bytes=1825399766-",
            "session_id": None,
            "offset": None,
            "utc_start": None,
            "utc_end": None,
        }

        self.assertFalse(
            views._should_use_probe_mode(
                content_type="movie",
                content_id="movie-1",
                **kwargs,
            )
        )
        self.assertFalse(
            views._should_use_probe_mode(
                content_type="movie",
                content_id="movie-2",
                **kwargs,
            )
        )
        self.assertTrue(
            views._should_use_probe_mode(
                content_type="movie",
                content_id="movie-3",
                **kwargs,
            )
        )

    def test_probe_mode_rejects_non_probe_shapes(self):
        self.assertFalse(
            views._should_use_probe_mode(
                client_ip="10.0.0.10",
                client_user_agent="Mozilla/5.0",
                content_type="movie",
                content_id="movie-1",
                range_header="bytes=0-",
                session_id=None,
                offset=None,
                utc_start=None,
                utc_end=None,
            )
        )
        self.assertFalse(
            views._should_use_probe_mode(
                client_ip="10.0.0.10",
                client_user_agent="Lavf/60.16.100",
                content_type="movie",
                content_id="movie-1",
                range_header="bytes=100-200",
                session_id=None,
                offset=None,
                utc_start=None,
                utc_end=None,
            )
        )
        self.assertFalse(
            views._should_use_probe_mode(
                client_ip="10.0.0.10",
                client_user_agent="Lavf/60.16.100",
                content_type="movie",
                content_id="movie-1",
                range_header="bytes=0-",
                session_id="vod_existing",
                offset=None,
                utc_start=None,
                utc_end=None,
            )
        )
