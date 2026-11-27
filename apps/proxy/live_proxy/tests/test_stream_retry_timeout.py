from unittest.mock import patch
from django.test import SimpleTestCase


class ObtainStreamWithRetriesTimeoutTests(SimpleTestCase):
    @patch("apps.proxy.live_proxy.views.ConfigHelper.stream_retry_timeout", return_value=0.2)
    @patch("apps.proxy.live_proxy.views.generate_stream_url")
    def test_obtain_stream_uses_configured_timeout(self, mock_generate, mock_timeout):
        """_obtain_stream_with_retries retries until the configured stream retry timeout expires."""
        from apps.proxy.live_proxy.views import _obtain_stream_with_retries

        mock_generate.return_value = (None, None, {}, False, None, False, "maximum connection limits reached", None)

        res = _obtain_stream_with_retries(
            channel_id="test-channel",
            client_id="test-client",
            user=None,
            allowed_m3u_profiles=None,
        )
        stream_url = res[0]
        attempts = res[8]

        mock_timeout.assert_called_once()
        self.assertIsNone(stream_url)
        # In 0.2s with ~0.1s sleep, it should make 2 attempts before timing out
        self.assertGreaterEqual(attempts, 1)

    @patch("apps.proxy.live_proxy.views.ConfigHelper.stream_retry_timeout", return_value=0)
    @patch("apps.proxy.live_proxy.views.generate_stream_url")
    def test_obtain_stream_timeout_zero_tries_once(self, mock_generate, mock_timeout):
        """timeout 0 still tries once, then fails without waiting for a free slot."""
        from apps.proxy.live_proxy.views import _obtain_stream_with_retries

        mock_generate.return_value = (None, None, {}, False, None, False, "maximum connection limits reached", None)

        res = _obtain_stream_with_retries(
            channel_id="test-channel",
            client_id="test-client",
            user=None,
            allowed_m3u_profiles=None,
        )
        stream_url = res[0]
        attempts = res[8]

        mock_timeout.assert_called_once()
        mock_generate.assert_called_once()
        self.assertIsNone(stream_url)
        self.assertEqual(attempts, 1)


class RedirectRouteKeepsOneStreamTests(SimpleTestCase):
    @patch("apps.proxy.live_proxy.views.ConfigHelper.stream_retry_timeout", return_value=0)
    @patch("apps.proxy.live_proxy.views.generate_stream_url")
    def test_fallback_flag_is_passed_only_when_turned_off(self, mock_generate, _mock_timeout):
        from apps.proxy.live_proxy.views import _obtain_stream_with_retries

        mock_generate.return_value = (None, None, {}, False, None, False, "403", None)

        _obtain_stream_with_retries("ch", "client", None, None)
        self.assertNotIn("allow_stream_fallback", mock_generate.call_args.kwargs)

        _obtain_stream_with_retries("ch", "client", None, None, allow_stream_fallback=False)
        self.assertFalse(mock_generate.call_args.kwargs["allow_stream_fallback"])

    @patch("apps.proxy.live_proxy.views._obtain_stream_with_retries")
    def test_redirect_handler_turns_the_fallback_off(self, mock_obtain):
        from types import SimpleNamespace
        import time

        from apps.proxy.live_proxy.views import _handle_redirect_stream

        mock_obtain.return_value = (
            None, None, {}, False, None, False, "403", None, 1, time.time(),
        )
        channel = SimpleNamespace(release_stream=lambda: True)

        _handle_redirect_stream(channel, "ch", "client", None, None)

        self.assertFalse(mock_obtain.call_args.kwargs["allow_stream_fallback"])
