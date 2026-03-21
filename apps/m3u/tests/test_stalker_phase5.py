from unittest.mock import patch

from django.test import TestCase

from apps.channels.models import Stream
from apps.m3u.models import M3UAccount, M3UAccountProfile
from apps.m3u.stalker import (
    DEFAULT_USER_AGENT,
    StalkerClient,
    StalkerError,
    StalkerRecoverableError,
)
from apps.proxy.ts_proxy.url_utils import generate_stream_url, resolve_live_stream_url
from core.models import PROXY_PROFILE_NAME, StreamProfile, UserAgent


class StalkerPhase5PreviewTests(TestCase):
    def setUp(self):
        self.user_agent = UserAgent.objects.create(
            name="Portal UA",
            user_agent="DispatcharrTest/1.0",
        )
        self.proxy_profile = StreamProfile.objects.create(
            name=PROXY_PROFILE_NAME,
            locked=True,
        )
        self.account = M3UAccount.objects.create(
            name="Stalker Preview",
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
            search_pattern=r"world-news",
            replace_pattern="world-news-hd",
        )
        self.stream = Stream.objects.create(
            name="World News",
            url="http://portal.example.com/stalker_portal/server/load.php",
            m3u_account=self.account,
            stream_hash="stalker-stream-hash",
            custom_properties={
                "portal_url": "http://portal.example.com/stalker_portal/server/load.php",
                "cmd": "ffmpeg http://upstream.example.com/live/world-news",
                "provider_type": "stalker",
            },
        )

    def test_resolve_live_stream_url_returns_stored_url_for_non_stalker_accounts(self):
        standard_account = M3UAccount.objects.create(
            name="Standard Preview",
            account_type=M3UAccount.Types.STADNARD,
            server_url="http://playlist.example.com/playlist.m3u",
        )
        stream = Stream.objects.create(
            name="Standard Stream",
            url="http://playlist.example.com/live/standard.ts",
            m3u_account=standard_account,
            stream_hash="standard-stream-hash",
        )

        with patch("apps.proxy.ts_proxy.url_utils.StalkerClient.create_link") as mock_create_link:
            self.assertEqual(
                resolve_live_stream_url(stream),
                "http://playlist.example.com/live/standard.ts",
            )

        mock_create_link.assert_not_called()

    def test_generate_stream_url_uses_stalker_create_link_before_profile_transform(self):
        def fake_resolve_playback_url(client, portal_url, channel_metadata):
            self.assertEqual(
                portal_url,
                "http://portal.example.com/stalker_portal/server/load.php",
            )
            self.assertEqual(
                channel_metadata["cmd"],
                "ffmpeg http://upstream.example.com/live/world-news",
            )
            client.token = "NEW-TOKEN"
            return "http://resolved.example.com/live/world-news"

        with patch.object(Stream, "get_stream", return_value=(self.stream.id, self.account_profile.id, None)), patch(
            "apps.proxy.ts_proxy.url_utils.M3UAccountProfile.objects.get",
            return_value=self.account_profile,
        ), patch.object(
            Stream, "get_stream_profile", return_value=self.proxy_profile
        ), patch(
            "apps.proxy.ts_proxy.url_utils.StalkerClient.resolve_playback_url",
            autospec=True,
            side_effect=fake_resolve_playback_url,
        ):
            stream_url, user_agent, input_headers, transcode, stream_profile_id, error_reason = generate_stream_url(
                self.stream.stream_hash
            )

        self.assertEqual(
            stream_url,
            "http://resolved.example.com/live/world-news-hd",
        )
        self.assertEqual(user_agent, DEFAULT_USER_AGENT)
        self.assertIsNotNone(input_headers)
        self.assertEqual(input_headers["Authorization"], "Bearer NEW-TOKEN")
        self.assertFalse(transcode)
        self.assertEqual(stream_profile_id, self.proxy_profile.id)
        self.assertIsNone(error_reason)

        self.account.refresh_from_db()
        self.assertEqual(self.account.custom_properties["token"], "NEW-TOKEN")

    def test_create_link_uses_go_pathescape_style_cmd_encoding(self):
        client = StalkerClient(
            server_url="http://portal.example.com/stalker_portal/portal.php",
            mac="00:1A:79:00:00:40",
        )

        with patch.object(client, "handshake"), patch.object(
            client, "authenticate_with_device_ids", return_value={"id": "1"}
        ), patch.object(
            client, "watchdog_update", return_value={}
        ), patch.object(
            client, "_request", return_value={"js": {"cmd": "ffmpeg http://resolved.example.com/live.ts"}}
        ) as mock_request:
            resolved = client.create_link(
                "http://portal.example.com/stalker_portal/portal.php",
                "ffmpeg http://upstream.example.com/live.php?stream=176913&extension=ts",
            )

        self.assertEqual(resolved, "http://resolved.example.com/live.ts")
        mock_request.assert_called_once_with(
            "GET",
            "http://portal.example.com/stalker_portal/portal.php?action=create_link&type=itv&cmd=ffmpeg%20http:%2F%2Fupstream.example.com%2Flive.php%3Fstream=176913&extension=ts&JsHttpRequest=1-xml",
            with_auth=True,
        )

    def test_resolve_playback_url_skips_device_id_auth_for_mac_only_portals(self):
        client = StalkerClient(
            server_url="http://portal.example.com/stalker_portal/portal.php",
            mac="00:1A:79:00:00:40",
        )

        with patch.object(client, "handshake"), patch.object(
            client, "authenticate_with_device_ids", return_value={"id": "1"}
        ) as mock_device_auth, patch.object(
            client, "watchdog_update", return_value={}
        ) as mock_watchdog, patch.object(
            client, "get_fresh_channel_cmd",
            return_value="ffmpeg http://upstream.example.com/live.php?stream=176913&extension=ts",
        ) as mock_get_fresh_cmd, patch.object(
            client, "create_link",
            return_value="http://resolved.example.com/live.ts",
        ) as mock_create_link:
            client.resolve_playback_url(
                "http://portal.example.com/stalker_portal/portal.php",
                {"stalker_channel_id": "5001", "cmd": "stale"},
            )

        mock_device_auth.assert_not_called()
        mock_watchdog.assert_called_once_with(
            "http://portal.example.com/stalker_portal/portal.php"
        )
        mock_get_fresh_cmd.assert_not_called()
        mock_create_link.assert_called_once_with(
            "http://portal.example.com/stalker_portal/portal.php",
            "stale",
        )

    def test_resolve_playback_url_uses_device_id_auth_when_ids_are_configured(self):
        client = StalkerClient(
            server_url="http://portal.example.com/stalker_portal/portal.php",
            mac="00:1A:79:00:00:40",
            custom_properties={
                "device_id": "device-1",
                "device_id2": "device-2",
            },
        )

        with patch.object(client, "handshake"), patch.object(
            client, "authenticate_with_device_ids", return_value={"id": "1"}
        ) as mock_device_auth, patch.object(
            client, "watchdog_update", return_value={}
        ), patch.object(
            client, "get_fresh_channel_cmd",
            return_value="ffmpeg http://upstream.example.com/live.php?stream=176913&extension=ts",
        ) as mock_get_fresh_cmd, patch.object(
            client, "create_link",
            return_value="http://resolved.example.com/live.ts",
        ) as mock_create_link:
            client.resolve_playback_url(
                "http://portal.example.com/stalker_portal/portal.php",
                {"stalker_channel_id": "5001", "cmd": "stale"},
            )

        mock_device_auth.assert_called_once_with(
            "http://portal.example.com/stalker_portal/portal.php"
        )
        mock_get_fresh_cmd.assert_not_called()
        mock_create_link.assert_called_once_with(
            "http://portal.example.com/stalker_portal/portal.php",
            "stale",
        )

    def test_resolve_playback_url_refreshes_channel_cmd_after_cached_link_failure(self):
        client = StalkerClient(
            server_url="http://portal.example.com/stalker_portal/portal.php",
            mac="00:1A:79:00:00:40",
        )

        with patch.object(client, "handshake"), patch.object(
            client, "watchdog_update", return_value={}
        ), patch.object(
            client,
            "get_fresh_channel_cmd",
            return_value="ffmpeg http://fresh.example.com/live.php?stream=176913&extension=ts",
        ) as mock_get_fresh_cmd, patch.object(
            client,
            "create_link",
            side_effect=[
                StalkerRecoverableError("Portal returned an empty playback link."),
                "http://resolved.example.com/live.ts",
            ],
        ) as mock_create_link:
            resolved = client.resolve_playback_url(
                "http://portal.example.com/stalker_portal/portal.php",
                {"stalker_channel_id": "5001", "cmd": "stale"},
            )

        self.assertEqual(resolved, "http://resolved.example.com/live.ts")
        mock_get_fresh_cmd.assert_called_once_with(
            "http://portal.example.com/stalker_portal/portal.php",
            {"stalker_channel_id": "5001", "cmd": "stale"},
        )
        self.assertEqual(mock_create_link.call_count, 2)

    def test_create_link_logs_unusable_resolved_url_payload(self):
        client = StalkerClient(
            server_url="http://portal.example.com/stalker_portal/portal.php",
            mac="00:1A:79:00:00:40",
        )

        with patch.object(client, "handshake"), patch.object(
            client, "authenticate_with_device_ids", return_value={"id": "1"}
        ), patch.object(
            client, "watchdog_update", return_value={}
        ), patch.object(
            client, "_request",
            return_value={"js": {"cmd": "ffmpeg http://portal.example.com/live.php?stream=&token=abc"}},
        ), patch("apps.m3u.stalker.logger.warning") as mock_warning:
            client.create_link(
                "http://portal.example.com/stalker_portal/portal.php",
                "ffmpeg http://upstream.example.com/live.php?stream=176913&extension=ts",
            )

        mock_warning.assert_called_once()

    def test_get_fresh_channel_cmd_prefers_current_session_channel_cmd(self):
        client = StalkerClient(
            server_url="http://portal.example.com/stalker_portal/portal.php",
            mac="00:1A:79:00:00:40",
        )

        with patch.object(
            client,
            "get_all_channels",
            return_value=[
                {
                    "id": "5001",
                    "cmd": "ffmpeg http://fresh.example.com/live.php?stream=200001&extension=ts",
                    "cmds": [{"id": "1001", "ch_id": "7001"}],
                }
            ],
        ):
            fresh_cmd = client.get_fresh_channel_cmd(
                "http://portal.example.com/stalker_portal/portal.php",
                {
                    "stalker_channel_id": "5001",
                    "cmd": "ffmpeg http://stale.example.com/live.php?stream=old&extension=ts",
                    "cmd_id": "1001",
                    "cmd_ch_id": "7001",
                },
            )

        self.assertEqual(
            fresh_cmd,
            "ffmpeg http://fresh.example.com/live.php?stream=200001&extension=ts",
        )

    def test_authenticate_with_device_ids_requires_non_empty_profile_id(self):
        client = StalkerClient(
            server_url="http://portal.example.com/stalker_portal/portal.php",
            mac="00:1A:79:00:00:40",
        )

        with patch.object(
            client,
            "_request",
            return_value={"js": {"id": None}, "text": "bad device auth"},
        ):
            with self.assertRaisesMessage(StalkerError, "bad device auth"):
                client.authenticate_with_device_ids(
                    "http://portal.example.com/stalker_portal/portal.php"
                )

    def test_authenticate_with_device_ids_omits_placeholder_identity_values(self):
        client = StalkerClient(
            server_url="http://portal.example.com/stalker_portal/portal.php",
            mac="00:1A:79:00:00:40",
        )

        with patch.object(
            client,
            "_request",
            return_value={"js": {"id": "1"}},
        ) as mock_request:
            client.authenticate_with_device_ids(
                "http://portal.example.com/stalker_portal/portal.php"
            )

        query = mock_request.call_args.kwargs["query"]
        self.assertNotIn("sn", query)
        self.assertNotIn("device_id", query)
        self.assertNotIn("device_id2", query)
        self.assertEqual(query["stb_type"], "MAG254")

    def test_authenticated_headers_match_stalkerhek_cookie_encoding(self):
        client = StalkerClient(
            server_url="http://portal.example.com/stalker_portal/portal.php",
            mac="00:1A:79:36:6A:E9",
            custom_properties={"timezone": "America/Toronto"},
        )
        client.token = "TOKEN-123"

        headers = client._headers(
            "http://portal.example.com/stalker_portal/portal.php",
            with_auth=True,
        )

        self.assertEqual(headers["Authorization"], "Bearer TOKEN-123")
        self.assertEqual(
            headers["Cookie"],
            "PHPSESSID=null; mac=00%3A1A%3A79%3A36%3A6A%3AE9; stb_lang=en; timezone=America%2FToronto;",
        )
