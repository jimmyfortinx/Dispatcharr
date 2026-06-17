from types import SimpleNamespace
from unittest.mock import ANY, patch
from uuid import uuid4

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase

from apps.proxy.live_proxy.views import stream_xc


class StreamXcViewTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_ts_extension_forwards_force_output_format(self):
        request = self.factory.get(
            "/live/test-user/test-pass/42.ts",
            HTTP_USER_AGENT="TiviMate/5.1.0",
        )
        xc_user = SimpleNamespace(
            username="test-user",
            custom_properties={"xc_password": "test-pass"},
            user_level=10,
        )
        channel = SimpleNamespace(id=42, uuid=uuid4())

        with patch(
            "apps.proxy.live_proxy.views.get_object_or_404",
            side_effect=[xc_user, channel],
        ), patch(
            "apps.proxy.live_proxy.views.network_access_allowed",
            return_value=True,
        ), patch(
            "apps.proxy.live_proxy.views._stream_ts_impl",
            return_value=HttpResponse(status=200),
        ) as mock_stream_impl:
            response = stream_xc(request, "test-user", "test-pass", "42.ts")

        self.assertEqual(response.status_code, 200)
        mock_stream_impl.assert_called_once_with(
            ANY,
            str(channel.uuid),
            user=xc_user,
            force_output_format="mpegts",
            force_redirect=False,
        )
