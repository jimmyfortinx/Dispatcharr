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


class StartStateAcrossRetriesTests(SimpleTestCase):
    @patch("apps.proxy.live_proxy.views.ConfigHelper.stream_retry_timeout", return_value=5)
    @patch("apps.proxy.live_proxy.views.gevent.sleep")
    @patch("apps.proxy.live_proxy.views.generate_stream_url")
    def test_one_state_is_shared_and_a_stop_ends_the_retries(self, mock_generate, _sleep, _timeout):
        from apps.proxy.live_proxy.views import _obtain_stream_with_retries

        seen_states = []

        def generate(*_args, start_state=None, **_kwargs):
            seen_states.append(start_state)
            if len(seen_states) == 2:
                # e.g. the failed stream's slot could not be released
                start_state.stop = True
                return (None, None, {}, False, None, False, "403", None)
            start_state.failed_stream_ids.append(1)
            return (
                None, None, {}, False, None, False,
                "403; other streams: All active M3U profiles have reached maximum connection limits",
                None,
            )

        mock_generate.side_effect = generate
        renew = lambda: True  # noqa: E731

        res = _obtain_stream_with_retries("ch", "client", None, None, keep_alive=renew)

        self.assertIsNone(res[0])
        self.assertEqual(res[8], 2)  # stopped after the second attempt
        self.assertIs(seen_states[0], seen_states[1])
        self.assertIs(seen_states[0].keep_alive, renew)

    @patch("apps.proxy.live_proxy.views.ConfigHelper.stream_retry_timeout", return_value=5)
    @patch("apps.proxy.live_proxy.views.gevent.sleep")
    @patch("apps.proxy.live_proxy.views.generate_stream_url")
    def test_resolution_error_after_a_retry_is_not_retried(self, mock_generate, _sleep, _timeout):
        from apps.proxy.live_proxy.views import _obtain_stream_with_retries

        def generate(*_args, start_state=None, **_kwargs):
            start_state.failed_stream_ids.append(1)
            return (None, None, {}, False, None, False, "403 Forbidden", None)

        mock_generate.side_effect = generate

        res = _obtain_stream_with_retries("ch", "client", None, None)

        self.assertEqual(res[8], 1)
