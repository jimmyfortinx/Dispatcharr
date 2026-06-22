from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from apps.m3u.models import M3UAccount, M3UAccountProfile
from apps.m3u.stalker import (
    DEFAULT_USER_AGENT,
    StalkerClient,
    StalkerRecoverableError,
)
from apps.proxy.vod_proxy.views import VODStreamView
from apps.vod.models import M3UMovieRelation, Movie
from apps.vod.resolvers import resolve_vod_stream_context


class StalkerPhase14MovieResolverTests(TestCase):
    def setUp(self):
        self.account = M3UAccount.objects.create(
            name="Stalker Movies Playback",
            account_type=M3UAccount.Types.STALKER,
            server_url="http://portal.example.com/c/",
            username="demo",
            password="secret",
            custom_properties={
                "mac": "00:1A:79:00:00:94",
                "token": "OLD-TOKEN",
                "enable_vod": True,
            },
        )
        self.movie = Movie.objects.create(name="Heat")
        self.relation = M3UMovieRelation.objects.create(
            m3u_account=self.account,
            movie=self.movie,
            stream_id="100",
            container_extension="mkv",
            custom_properties={
                "basic_data": {
                    "id": "100",
                    "title": "Heat",
                    "cmd": "ffmpeg http://provider.example.com/movie-100.mkv",
                },
                "detailed_fetched": False,
            },
        )

    def test_resolve_vod_playback_url_retries_once_after_recoverable_create_link_failure(self):
        client = StalkerClient(
            server_url=self.account.server_url,
            mac="00:1A:79:00:00:94",
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
            "prepare_vod_playback_session",
            side_effect=fake_prepare,
        ) as mock_prepare, patch.object(
            client,
            "create_vod_link",
            side_effect=[
                StalkerRecoverableError("Portal returned an empty playback link."),
                "http://resolved.example.com/movie-100.mkv",
            ],
        ) as mock_create_vod_link:
            resolved = client.resolve_vod_playback_url(
                "http://portal.example.com/stalker_portal/server/load.php",
                "ffmpeg http://provider.example.com/movie-100.mkv",
            )

        self.assertEqual(resolved, "http://resolved.example.com/movie-100.mkv")
        self.assertEqual(client.token, "NEW-TOKEN")
        self.assertEqual(mock_prepare.call_count, 2)
        self.assertEqual(mock_create_vod_link.call_count, 2)

    def test_prepare_vod_playback_session_skips_watchdog(self):
        client = StalkerClient(
            server_url=self.account.server_url,
            mac="00:1A:79:00:00:94",
            username=self.account.username,
            password=self.account.password,
            custom_properties={"token": "OLD-TOKEN"},
        )

        with patch.object(
            client,
            "prepare_authenticated_session",
        ) as mock_prepare_authenticated, patch.object(
            client,
            "watchdog_update",
        ) as mock_watchdog:
            client.prepare_vod_playback_session(
                "http://portal.example.com/stalker_portal/server/load.php"
            )

        mock_prepare_authenticated.assert_called_once_with(
            "http://portal.example.com/stalker_portal/server/load.php"
        )
        mock_watchdog.assert_not_called()

    @patch("apps.vod.resolvers.StalkerClient.resolve_vod_playback_url", autospec=True)
    @patch("apps.vod.resolvers.StalkerClient.discover_vod_categories", autospec=True)
    def test_resolver_builds_stalker_movie_link_and_persists_runtime_state(
        self,
        mock_discover_vod_categories,
        mock_resolve_vod_playback_url,
    ):
        mock_discover_vod_categories.return_value = SimpleNamespace(
            normalized_portal_url="http://portal.example.com/stalker_portal/server/load.php"
        )

        def fake_resolve(client, portal_url, cmd, series=None):
            self.assertEqual(
                portal_url,
                "http://portal.example.com/stalker_portal/server/load.php",
            )
            self.assertEqual(
                cmd,
                "ffmpeg http://provider.example.com/movie-100.mkv",
            )
            self.assertIsNone(series)
            client.token = "REFRESHED-TOKEN"
            return "http://resolved.example.com/movie-100.mkv"

        mock_resolve_vod_playback_url.side_effect = fake_resolve

        stream_context = resolve_vod_stream_context(self.relation)

        self.assertEqual(
            stream_context.url,
            "http://resolved.example.com/movie-100.mkv",
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
    
    @patch("apps.vod.resolvers.StalkerClient.resolve_vod_playback_url", autospec=True)
    @patch("apps.vod.resolvers.StalkerClient.discover_vod_categories", autospec=True)
    def test_resolver_reuses_cached_live_portal_url_for_vod_playback(
        self,
        mock_discover_vod_categories,
        mock_resolve_vod_playback_url,
    ):
        self.account.custom_properties["stalker_portal_url"] = (
            "http://portal.example.com/stalker_portal/server/load.php"
        )
        self.account.save(update_fields=["custom_properties"])

        def fake_resolve(client, portal_url, cmd, series=None):
            self.assertEqual(
                portal_url,
                "http://portal.example.com/stalker_portal/server/load.php",
            )
            self.assertEqual(
                cmd,
                "ffmpeg http://provider.example.com/movie-100.mkv",
            )
            self.assertIsNone(series)
            client.token = "REFRESHED-TOKEN"
            return "http://resolved.example.com/movie-100.mkv"

        mock_resolve_vod_playback_url.side_effect = fake_resolve

        stream_context = resolve_vod_stream_context(self.relation)

        self.assertEqual(
            stream_context.url,
            "http://resolved.example.com/movie-100.mkv",
        )
        mock_discover_vod_categories.assert_not_called()

        self.account.refresh_from_db()
        self.assertEqual(
            self.account.custom_properties["stalker_vod_portal_url"],
            "http://portal.example.com/stalker_portal/server/load.php",
        )

    def test_resolver_keeps_xtream_movie_urls_on_existing_route_pattern(self):
        account = M3UAccount.objects.create(
            name="XC Movies Playback",
            account_type=M3UAccount.Types.XC,
            server_url="http://xc.example.com",
            username="demo",
            password="secret",
        )
        movie = Movie.objects.create(name="XC Movie")
        relation = M3UMovieRelation.objects.create(
            m3u_account=account,
            movie=movie,
            stream_id="200",
            container_extension="mp4",
        )

        stream_context = resolve_vod_stream_context(relation)

        self.assertEqual(
            stream_context.url,
            "http://xc.example.com/movie/demo/secret/200.mp4",
        )
        self.assertIsNone(stream_context.input_headers)


class StalkerPhase14VODProxyTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.account = M3UAccount.objects.create(
            name="Stalker Movie Proxy",
            account_type=M3UAccount.Types.STALKER,
            server_url="http://portal.example.com/c/",
            custom_properties={
                "mac": "00:1A:79:00:00:95",
                "enable_vod": True,
            },
        )
        self.profile = M3UAccountProfile.objects.create(
            m3u_account=self.account,
            name="Default",
            is_default=True,
            is_active=True,
            search_pattern=r"/movie/",
            replace_pattern="/movie-hd/",
        )
        self.movie = Movie.objects.create(name="Proxy Movie")
        self.relation = M3UMovieRelation.objects.create(
            m3u_account=self.account,
            movie=self.movie,
            stream_id="300",
            custom_properties={
                "basic_data": {
                    "id": "300",
                    "cmd": "ffmpeg http://provider.example.com/movie-300.mkv",
                },
                "detailed_fetched": False,
            },
        )

    @patch("apps.proxy.vod_proxy.views.MultiWorkerVODConnectionManager.get_instance")
    @patch("apps.proxy.vod_proxy.views.resolve_vod_stream_context")
    @patch.object(VODStreamView, "_get_m3u_profile")
    @patch.object(VODStreamView, "_get_content_and_relation")
    def test_vod_proxy_uses_resolved_movie_url_before_profile_transform(
        self,
        mock_get_content_and_relation,
        mock_get_m3u_profile,
        mock_resolve_vod_stream_context,
        mock_get_connection_manager,
    ):
        request = self.factory.get(
            f"/proxy/vod/movie/{self.movie.uuid}/phase14-session",
            HTTP_USER_AGENT="DispatcharrTestClient/1.0",
        )
        mock_get_content_and_relation.return_value = (self.movie, self.relation)
        mock_get_m3u_profile.return_value = (self.profile, 0)
        mock_resolve_vod_stream_context.return_value = SimpleNamespace(
            url="http://resolved.example.com/movie/300.mkv",
            user_agent=DEFAULT_USER_AGENT,
            input_headers={
                "Authorization": "Bearer PLAY-TOKEN",
                "User-Agent": DEFAULT_USER_AGENT,
            },
        )

        manager = Mock()
        manager.stream_content_with_session.return_value = HttpResponse("ok")
        mock_get_connection_manager.return_value = manager

        response = VODStreamView().get(
            request,
            "movie",
            self.movie.uuid,
            "phase14-session",
            self.profile.id,
        )

        self.assertEqual(response.status_code, 200)
        manager.stream_content_with_session.assert_called_once()
        _, kwargs = manager.stream_content_with_session.call_args
        self.assertEqual(
            kwargs["stream_url"],
            "http://resolved.example.com/movie-hd/300.mkv",
        )
        self.assertEqual(
            kwargs["input_headers"]["Authorization"],
            "Bearer PLAY-TOKEN",
        )


from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import RequestFactory, TestCase

from apps.m3u.models import M3UAccount, M3UAccountProfile
from apps.m3u.stalker import DEFAULT_USER_AGENT, StalkerClient
from apps.proxy.vod_proxy.multi_worker_connection_manager import (
    RedisBackedVODConnection,
)
from apps.proxy.vod_proxy.views import VODStreamView, _get_stream_context_for_request
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


class StalkerPhase15SessionReuseTests(TestCase):
    def setUp(self):
        self.account = M3UAccount.objects.create(
            name="Stalker Reuse Playback",
            account_type=M3UAccount.Types.STALKER,
            server_url="http://portal.example.com/c/",
            custom_properties={
                "mac": "00:1A:79:00:00:99",
                "enable_vod": True,
            },
        )
        self.series = Series.objects.create(name="Reuse Series")
        self.episode = Episode.objects.create(
            series=self.series,
            season_number=1,
            episode_number=1,
            name="Episode 1",
        )
        self.relation = M3UEpisodeRelation.objects.create(
            m3u_account=self.account,
            episode=self.episode,
            stream_id="1002",
            custom_properties={
                "provider_type": "stalker",
                "cmd": "ffmpeg http://provider.example.com/episode-1002.mkv",
            },
        )

    @patch("apps.proxy.vod_proxy.views.resolve_vod_stream_context")
    def test_existing_session_target_skips_fresh_stalker_resolution(
        self,
        mock_resolve_vod_stream_context,
    ):
        fake_redis = _FakeRedis()
        connection = RedisBackedVODConnection(
            "phase15-reuse-session",
            redis_client=fake_redis,
        )
        connection.create_connection(
            stream_url="http://stored.example.com/episode-1002.mkv",
            headers={
                "Authorization": "Bearer STORED-TOKEN",
                "User-Agent": "StoredAgent/1.0",
            },
            m3u_profile_id=1,
        )

        with patch(
            "apps.proxy.vod_proxy.views.RedisBackedVODConnection",
            side_effect=lambda session_id: RedisBackedVODConnection(
                session_id,
                redis_client=fake_redis,
            ),
        ):
            stream_context = _get_stream_context_for_request(
                self.relation,
                session_id="phase15-reuse-session",
            )

        self.assertEqual(
            stream_context["url"],
            "http://stored.example.com/episode-1002.mkv",
        )
        self.assertEqual(
            stream_context["input_headers"]["Authorization"],
            "Bearer STORED-TOKEN",
        )
        mock_resolve_vod_stream_context.assert_not_called()
