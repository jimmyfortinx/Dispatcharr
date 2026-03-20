from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import RequestFactory, TestCase

from apps.m3u.models import M3UAccount, M3UAccountProfile
from apps.m3u.stalker import DEFAULT_USER_AGENT, StalkerClient
from apps.proxy.vod_proxy.multi_worker_connection_manager import (
    RedisBackedVODConnection,
)
from apps.proxy.vod_proxy.views import VODStreamView
from apps.vod.models import Episode, M3UEpisodeRelation, Series
from apps.vod.resolvers import resolve_vod_stream_context


class _FakeRedis:
    def __init__(self):
        self.hashes = {}
        self.values = {}

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def hset(self, key, mapping):
        normalized = {str(k): str(v) for k, v in mapping.items()}
        self.hashes.setdefault(key, {}).update(normalized)
        return True

    def expire(self, key, ttl):
        return True

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def delete(self, key):
        self.values.pop(key, None)
        self.hashes.pop(key, None)
        return True


class _FakeHeadResponse:
    def __init__(self, status_code=206, headers=None):
        self.status_code = status_code
        self.headers = headers or {
            "Content-Range": "bytes 0-1/4096",
            "Content-Type": "video/x-matroska",
        }

    def close(self):
        return None


class _FakeUpstreamResponse:
    def __init__(self, url, headers=None):
        self.url = url
        self.headers = headers or {
            "content-length": "4096",
            "content-type": "video/mp4",
        }

    def raise_for_status(self):
        return None

    def close(self):
        return None


class StalkerPhase15EpisodeResolverTests(TestCase):
    def setUp(self):
        self.account = M3UAccount.objects.create(
            name="Stalker Episodes Playback",
            account_type=M3UAccount.Types.STALKER,
            server_url="http://portal.example.com/c/",
            username="demo",
            password="secret",
            custom_properties={
                "mac": "00:1A:79:00:00:96",
                "token": "OLD-TOKEN",
                "enable_vod": True,
            },
        )
        self.series = Series.objects.create(name="Proxy Series")
        self.episode = Episode.objects.create(
            series=self.series,
            season_number=1,
            episode_number=1,
            name="Episode 1",
        )
        self.relation = M3UEpisodeRelation.objects.create(
            m3u_account=self.account,
            episode=self.episode,
            stream_id="901",
            container_extension="mkv",
            custom_properties={
                "provider_type": "stalker",
                "info": {
                    "_stalker_placeholder_episode": True,
                    "id": "901",
                    "title": "Episode 1",
                    "episode_num": 1,
                    "portal_url": "http://portal.example.com/stalker_portal/server/load.php",
                    "cmd": "ffmpeg http://provider.example.com/episode-901.mkv",
                },
            },
        )

    @patch("apps.vod.resolvers.StalkerClient.resolve_vod_playback_url", autospec=True)
    def test_resolver_builds_stalker_episode_link_and_persists_runtime_state(
        self,
        mock_resolve_vod_playback_url,
    ):
        def fake_resolve(client, portal_url, cmd, series=None):
            self.assertEqual(
                portal_url,
                "http://portal.example.com/stalker_portal/server/load.php",
            )
            self.assertEqual(
                cmd,
                "ffmpeg http://provider.example.com/episode-901.mkv",
            )
            self.assertEqual(series, 1)
            client.token = "REFRESHED-TOKEN"
            return "http://resolved.example.com/episode-901.mkv"

        mock_resolve_vod_playback_url.side_effect = fake_resolve

        stream_context = resolve_vod_stream_context(self.relation)

        self.assertEqual(
            stream_context.url,
            "http://resolved.example.com/episode-901.mkv",
        )
        self.assertEqual(stream_context.user_agent, DEFAULT_USER_AGENT)
        self.assertEqual(
            stream_context.input_headers["Authorization"],
            "Bearer REFRESHED-TOKEN",
        )

        self.account.refresh_from_db()
        self.assertEqual(self.account.custom_properties["token"], "REFRESHED-TOKEN")
        self.assertEqual(
            self.account.custom_properties["stalker_vod_portal_url"],
            "http://portal.example.com/stalker_portal/server/load.php",
        )


class StalkerPhase15SeriesCreateLinkTests(TestCase):
    def test_create_vod_link_appends_series_selector_for_series_episodes(self):
        client = StalkerClient(
            server_url="http://portal.example.com/c/",
            mac="00:1A:79:00:00:98",
        )

        with patch.object(
            client,
            "_request",
            return_value={"js": {"cmd": "ffmpeg http://media.example.com/episode-1.avi"}},
        ) as mock_request:
            resolved = client.create_vod_link(
                "http://portal.example.com/stalker_portal/portal.php",
                "eyJzZXJpZXNfaWQiOjcxNDEsInNlYXNvbl9udW0iOjEsInR5cGUiOiJzZXJpZXMifQ==",
                series=1,
            )

        self.assertEqual(resolved, "http://media.example.com/episode-1.avi")
        mock_request.assert_called_once_with(
            "GET",
            "http://portal.example.com/stalker_portal/portal.php?action=create_link&type=vod&cmd=eyJzZXJpZXNfaWQiOjcxNDEsInNlYXNvbl9udW0iOjEsInR5cGUiOiJzZXJpZXMifQ%3D%3D&series=1&JsHttpRequest=1-xml",
            with_auth=True,
        )


class StalkerPhase15VODProxyTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.account = M3UAccount.objects.create(
            name="Stalker Series Proxy",
            account_type=M3UAccount.Types.STALKER,
            server_url="http://portal.example.com/c/",
            custom_properties={
                "mac": "00:1A:79:00:00:97",
                "enable_vod": True,
            },
        )
        self.profile = M3UAccountProfile.objects.create(
            m3u_account=self.account,
            name="Default",
            is_default=True,
            is_active=True,
            search_pattern=r"/episode/",
            replace_pattern="/episode-hd/",
        )
        self.series = Series.objects.create(name="Series Playback")
        self.episode = Episode.objects.create(
            series=self.series,
            season_number=1,
            episode_number=1,
            name="Pilot",
        )
        self.relation = M3UEpisodeRelation.objects.create(
            m3u_account=self.account,
            episode=self.episode,
            stream_id="1001",
            custom_properties={
                "provider_type": "stalker",
                "cmd": "ffmpeg http://provider.example.com/episode-1001.mkv",
            },
        )

    @patch("redis.StrictRedis")
    @patch("apps.proxy.vod_proxy.views.requests.get")
    @patch("apps.proxy.vod_proxy.views.resolve_vod_stream_context")
    @patch.object(VODStreamView, "_get_m3u_profile")
    def test_head_preflight_uses_episode_relation_for_series_playback_and_forwards_provider_headers(
        self,
        mock_get_m3u_profile,
        mock_resolve_vod_stream_context,
        mock_requests_get,
        mock_redis,
    ):
        request = self.factory.head(
            f"/proxy/vod/series/{self.series.uuid}/phase15-session/{self.profile.id}/",
            HTTP_USER_AGENT="DispatcharrTestClient/1.0",
        )
        mock_get_m3u_profile.return_value = (self.profile, 0)
        mock_resolve_vod_stream_context.return_value = SimpleNamespace(
            url="http://resolved.example.com/episode/1001.mkv",
            user_agent=DEFAULT_USER_AGENT,
            input_headers={
                "Authorization": "Bearer PLAY-TOKEN",
                "User-Agent": DEFAULT_USER_AGENT,
            },
        )
        mock_requests_get.return_value = _FakeHeadResponse()
        mock_redis.return_value = Mock(set=Mock(return_value=True))

        response = VODStreamView().head(
            request,
            "series",
            self.series.uuid,
            "phase15-session",
            self.profile.id,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Length"], "4096")
        self.assertEqual(response["Accept-Ranges"], "bytes")

        mock_resolve_vod_stream_context.assert_called_once_with(self.relation)
        mock_requests_get.assert_called_once()
        args, kwargs = mock_requests_get.call_args
        self.assertEqual(
            args[0],
            "http://resolved.example.com/episode-hd/1001.mkv",
        )
        self.assertEqual(kwargs["headers"]["Range"], "bytes=0-1")
        self.assertEqual(
            kwargs["headers"]["Authorization"],
            "Bearer PLAY-TOKEN",
        )
        self.assertEqual(
            kwargs["headers"]["User-Agent"],
            DEFAULT_USER_AGENT,
        )


class StalkerPhase15SessionRefreshTests(TestCase):
    def test_refresh_connection_target_reuses_new_stalker_url_for_later_range_requests(self):
        fake_redis = _FakeRedis()
        connection = RedisBackedVODConnection(
            "phase15-refresh-session",
            redis_client=fake_redis,
        )
        connection.create_connection(
            stream_url="http://expired.example.com/episode-1.mkv",
            headers={
                "Authorization": "Bearer OLD-TOKEN",
                "User-Agent": "DispatcharrTest/1.0",
            },
            m3u_profile_id=1,
        )

        state = connection._get_connection_state()
        state.final_url = "http://expired-cdn.example.com/episode-1.mkv"
        state.content_length = "4096"
        state.content_type = "video/mp4"
        state.request_count = 5
        connection._save_connection_state(state)

        refreshed = connection.refresh_connection_target(
            "http://fresh.example.com/episode-1.mkv",
            {
                "Authorization": "Bearer NEW-TOKEN",
                "User-Agent": "DispatcharrTest/2.0",
            },
        )

        self.assertTrue(refreshed)
        refreshed_state = connection._get_connection_state()
        self.assertEqual(
            refreshed_state.stream_url,
            "http://fresh.example.com/episode-1.mkv",
        )
        self.assertEqual(
            refreshed_state.headers["Authorization"],
            "Bearer NEW-TOKEN",
        )
        self.assertIsNone(refreshed_state.final_url)

        session = Mock()
        session.get.return_value = _FakeUpstreamResponse(
            "http://fresh-cdn.example.com/episode-1.mkv"
        )

        with patch(
            "apps.proxy.vod_proxy.multi_worker_connection_manager.requests.Session",
            return_value=session,
        ):
            response = connection.get_stream("bytes=0-10")

        self.assertIsNotNone(response)
        session.get.assert_called_once()
        _, kwargs = session.get.call_args
        self.assertEqual(
            kwargs["headers"]["Authorization"],
            "Bearer NEW-TOKEN",
        )
        self.assertEqual(
            kwargs["headers"]["User-Agent"],
            "DispatcharrTest/2.0",
        )
        self.assertEqual(kwargs["headers"]["Range"], "bytes=0-10")
        self.assertTrue(kwargs["allow_redirects"])

        final_state = connection._get_connection_state()
        self.assertEqual(
            final_state.final_url,
            "http://fresh-cdn.example.com/episode-1.mkv",
        )
