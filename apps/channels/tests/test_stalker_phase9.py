import os
import tempfile
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, TestCase
from django.utils import timezone
from requests.exceptions import ChunkedEncodingError

from apps.channels.models import Channel, Recording, Stream
from apps.channels.tasks import build_dvr_request_headers, build_dvr_stream_url, run_recording
from apps.m3u.models import M3UAccount, M3UAccountProfile
from apps.proxy.ts_proxy.views import stream_ts
from core.models import PROXY_PROFILE_NAME, StreamProfile, UserAgent


class _FakeStreamingResponse:
    def __init__(self, chunks, terminal_error=None):
        self._chunks = list(chunks)
        self._terminal_error = terminal_error

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=8192):
        for chunk in self._chunks:
            yield chunk
        if self._terminal_error is not None:
            raise self._terminal_error


class StalkerPhase9DvrTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user_agent = UserAgent.objects.create(
            name="Portal UA",
            user_agent="DispatcharrTest/1.0",
        )
        self.proxy_profile = StreamProfile.objects.create(
            name=PROXY_PROFILE_NAME,
            locked=True,
        )
        self.account = M3UAccount.objects.create(
            name="Stalker DVR",
            account_type=M3UAccount.Types.STALKER,
            server_url="http://portal.example.com/c/",
            username="demo",
            password="secret",
            user_agent=self.user_agent,
            custom_properties={
                "mac": "00:1A:79:00:00:40",
                "token": "OLD-TOKEN",
            },
        )
        self.account_profile = M3UAccountProfile.objects.create(
            m3u_account=self.account,
            name="Default",
            is_default=True,
            is_active=True,
            max_streams=0,
        )
        self.stream = Stream.objects.create(
            name="World News",
            url="http://portal.example.com/stalker_portal/server/load.php",
            m3u_account=self.account,
            stream_profile=self.proxy_profile,
            stream_hash="phase9-stalker-stream-hash",
            custom_properties={
                "portal_url": "http://portal.example.com/stalker_portal/server/load.php",
                "cmd": "ffmpeg http://upstream.example.com/live/world-news",
                "stalker_channel_id": "5001",
                "provider_type": "stalker",
            },
        )
        self.channel = Channel.objects.create(
            channel_number=401,
            name="World News",
            stream_profile=self.proxy_profile,
        )
        self.channel.streams.add(self.stream)

    def test_stream_ts_initializes_stalker_runtime_context_for_dvr_client(self):
        channel_id = str(self.channel.uuid)
        request = self.factory.get(
            f"/proxy/ts/stream/{channel_id}",
            HTTP_USER_AGENT="Dispatcharr-DVR/recording-42",
        )

        runtime_headers = {
            "Authorization": "Bearer REFRESHED-TOKEN",
            "User-Agent": "DispatcharrTest/2.0",
        }
        proxy_server = MagicMock()
        proxy_server.worker_id = "worker-1"
        proxy_server.check_if_channel_exists.return_value = False
        proxy_server.am_i_owner.return_value = False
        proxy_server.stream_buffers = {channel_id: MagicMock(channel_id=channel_id)}
        proxy_server.client_managers = {channel_id: MagicMock(add_client=MagicMock())}

        redis_client = MagicMock()
        redis_client.exists.return_value = False

        def redis_get(key):
            if key == f"channel_stream:{self.channel.id}":
                return str(self.stream.id).encode("utf-8")
            if key == f"stream_profile:{self.stream.id}":
                return str(self.account_profile.id).encode("utf-8")
            return None

        redis_client.get.side_effect = redis_get
        proxy_server.redis_client = redis_client

        with patch(
            "apps.proxy.ts_proxy.views.network_access_allowed",
            return_value=True,
        ), patch(
            "apps.proxy.ts_proxy.views.ProxyServer.get_instance",
            return_value=proxy_server,
        ), patch(
            "apps.proxy.ts_proxy.views.generate_stream_url",
            return_value=(
                "http://resolved.example.com/live/world-news",
                "DispatcharrTest/2.0",
                runtime_headers,
                False,
                self.proxy_profile.id,
                None,
            ),
        ) as mock_generate_stream_url, patch(
            "apps.proxy.ts_proxy.views.ChannelService.initialize_channel",
            return_value=True,
        ) as mock_initialize_channel, patch(
            "apps.proxy.ts_proxy.views.create_stream_generator",
            return_value=lambda: iter([b"ts"]),
        ):
            response = stream_ts(request, channel_id)

        self.assertEqual(response.status_code, 200)
        mock_generate_stream_url.assert_called_once_with(channel_id)
        mock_initialize_channel.assert_called_once_with(
            channel_id,
            "http://resolved.example.com/live/world-news",
            "DispatcharrTest/2.0",
            runtime_headers,
            False,
            self.proxy_profile.id,
            self.stream.id,
            self.account_profile.id,
        )
        proxy_server.client_managers[channel_id].add_client.assert_called_once()

    def test_run_recording_reconnects_to_ts_proxy_for_stalker_channels(self):
        now = timezone.now()
        recording = Recording.objects.create(
            channel=self.channel,
            start_time=now - timedelta(minutes=1),
            end_time=now + timedelta(minutes=1),
            custom_properties={},
        )

        channel_layer = MagicMock()
        base_url = "http://127.0.0.1:9191"
        expected_url = build_dvr_stream_url(base_url, self.channel.uuid)
        expected_headers = build_dvr_request_headers(recording.id)

        with tempfile.TemporaryDirectory() as tmpdir:
            final_path = os.path.join(tmpdir, "world-news.mkv")
            temp_ts_path = os.path.join(tmpdir, "world-news.ts")

            def fake_ffmpeg_run(*args, **kwargs):
                with open(final_path, "wb") as output_file:
                    output_file.write(b"mkv-data")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            first_response = _FakeStreamingResponse(
                [b"first-chunk"],
                terminal_error=ChunkedEncodingError("upstream reset"),
            )
            second_response = _FakeStreamingResponse([b"second-chunk"])

            with patch(
                "apps.channels.tasks.async_to_sync",
                side_effect=lambda func: func,
            ), patch(
                "apps.channels.tasks.get_channel_layer",
                return_value=channel_layer,
            ), patch(
                "core.utils.log_system_event",
                side_effect=lambda *args, **kwargs: None,
            ), patch(
                "apps.channels.tasks._resolve_poster_for_program",
                return_value=(None, None),
            ), patch(
                "apps.channels.tasks._build_output_paths",
                return_value=(final_path, temp_ts_path, "world-news.mkv"),
            ), patch(
                "apps.channels.tasks.build_dvr_candidates",
                return_value=[base_url],
            ), patch(
                "apps.channels.tasks.requests.get",
                side_effect=[first_response, second_response],
            ) as mock_requests_get, patch(
                "apps.channels.tasks.time.sleep",
                side_effect=lambda *args, **kwargs: None,
            ), patch(
                "apps.channels.tasks.subprocess.run",
                side_effect=fake_ffmpeg_run,
            ), patch(
                "core.utils.RedisClient.get_client",
                return_value=None,
            ), patch(
                "core.models.CoreSettings.get_dvr_comskip_enabled",
                return_value=False,
            ):
                run_recording(
                    recording.id,
                    self.channel.id,
                    str(recording.start_time),
                    str(recording.end_time),
                )

        self.assertEqual(mock_requests_get.call_count, 2)
        for call in mock_requests_get.call_args_list:
            self.assertEqual(call.args[0], expected_url)
            self.assertEqual(call.kwargs["headers"], expected_headers)
            self.assertTrue(call.kwargs["stream"])
            self.assertEqual(call.kwargs["timeout"], (10, 15))

        recording.refresh_from_db()
        self.assertEqual(recording.custom_properties.get("status"), "completed")
        self.assertEqual(recording.custom_properties.get("remux_success"), True)
