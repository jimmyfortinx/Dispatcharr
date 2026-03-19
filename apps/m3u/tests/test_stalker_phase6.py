from unittest.mock import patch

from django.test import TestCase

from apps.channels.models import Stream
from apps.m3u.models import M3UAccount
from apps.m3u.stalker import StalkerClient, StalkerError, StalkerRecoverableError
from apps.proxy.ts_proxy.url_utils import resolve_live_stream_url


class StalkerPhase6ResolverHardeningTests(TestCase):
    def setUp(self):
        self.account = M3UAccount.objects.create(
            name="Stalker Resolver",
            account_type=M3UAccount.Types.STALKER,
            server_url="http://portal.example.com/c/",
            username="demo",
            password="secret",
            custom_properties={
                "mac": "00:1A:79:00:00:40",
                "token": "OLD-TOKEN",
            },
        )
        self.stream = Stream.objects.create(
            name="World News",
            url="http://portal.example.com/stalker_portal/server/load.php",
            m3u_account=self.account,
            stream_hash="phase6-stream-hash",
            custom_properties={
                "portal_url": "http://portal.example.com/stalker_portal/server/load.php",
                "cmd": "ffmpeg http://upstream.example.com/live/world-news",
                "stalker_channel_id": "5001",
                "provider_type": "stalker",
            },
        )

    def test_resolve_playback_url_retries_once_after_recoverable_create_link_failure(self):
        client = StalkerClient(
            server_url=self.account.server_url,
            mac="00:1A:79:00:00:40",
            username=self.account.username,
            password=self.account.password,
            custom_properties={"token": "OLD-TOKEN"},
        )

        def fake_prepare(portal_url):
            if fake_prepare.calls == 0:
                client.token = "OLD-TOKEN"
            else:
                client.token = "NEW-TOKEN"
            fake_prepare.calls += 1

        fake_prepare.calls = 0

        with patch.object(
            client,
            "prepare_playback_session",
            side_effect=fake_prepare,
        ) as mock_prepare, patch.object(
            client,
            "get_fresh_channel_cmd",
            return_value="ffmpeg http://upstream.example.com/live/world-news",
        ) as mock_get_fresh_cmd, patch.object(
            client,
            "create_link",
            side_effect=[
                StalkerRecoverableError("Portal returned an empty playback link."),
                "http://resolved.example.com/live/world-news",
            ],
        ) as mock_create_link:
            resolved = client.resolve_playback_url(
                "http://portal.example.com/stalker_portal/server/load.php",
                self.stream.custom_properties,
            )

        self.assertEqual(
            resolved,
            "http://resolved.example.com/live/world-news",
        )
        self.assertEqual(client.token, "NEW-TOKEN")
        self.assertEqual(mock_prepare.call_count, 2)
        self.assertEqual(mock_get_fresh_cmd.call_count, 2)
        self.assertEqual(mock_create_link.call_count, 2)

    def test_resolve_live_stream_url_persists_refreshed_token_after_session_recovery(self):
        def fake_prepare(client, portal_url):
            fake_prepare.calls += 1
            if fake_prepare.calls == 2:
                client.token = "REFRESHED-TOKEN"

        fake_prepare.calls = 0

        def fake_create_link(client, portal_url, cmd):
            if fake_create_link.calls == 0:
                fake_create_link.calls += 1
                raise StalkerRecoverableError("Portal session expired.")
            fake_create_link.calls += 1
            return "http://resolved.example.com/live/world-news"

        fake_create_link.calls = 0

        with patch.object(
            StalkerClient,
            "prepare_playback_session",
            autospec=True,
            side_effect=fake_prepare,
        ), patch.object(
            StalkerClient,
            "get_fresh_channel_cmd",
            autospec=True,
            return_value="ffmpeg http://upstream.example.com/live/world-news",
        ), patch.object(
            StalkerClient,
            "create_link",
            autospec=True,
            side_effect=fake_create_link,
        ):
            resolved = resolve_live_stream_url(self.stream)

        self.assertEqual(
            resolved,
            "http://resolved.example.com/live/world-news",
        )
        self.account.refresh_from_db()
        self.assertEqual(
            self.account.custom_properties["token"],
            "REFRESHED-TOKEN",
        )

    def test_resolve_playback_url_does_not_retry_non_recoverable_errors(self):
        client = StalkerClient(
            server_url=self.account.server_url,
            mac="00:1A:79:00:00:40",
            username=self.account.username,
            password=self.account.password,
            custom_properties={"token": "OLD-TOKEN"},
        )

        with patch.object(client, "prepare_playback_session") as mock_prepare, patch.object(
            client,
            "get_fresh_channel_cmd",
            return_value="ffmpeg http://upstream.example.com/live/world-news",
        ) as mock_get_fresh_cmd, patch.object(
            client,
            "create_link",
            side_effect=StalkerError("Request failed: 500 Server Error"),
        ) as mock_create_link:
            with self.assertRaisesMessage(
                StalkerError,
                "Request failed: 500 Server Error",
            ):
                client.resolve_playback_url(
                    "http://portal.example.com/stalker_portal/server/load.php",
                    self.stream.custom_properties,
                )

        mock_prepare.assert_called_once()
        mock_get_fresh_cmd.assert_called_once()
        mock_create_link.assert_called_once()
