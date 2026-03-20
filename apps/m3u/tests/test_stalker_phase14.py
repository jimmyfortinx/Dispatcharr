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
            "prepare_playback_session",
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

        def fake_resolve(client, portal_url, cmd):
            self.assertEqual(
                portal_url,
                "http://portal.example.com/stalker_portal/server/load.php",
            )
            self.assertEqual(
                cmd,
                "ffmpeg http://provider.example.com/movie-100.mkv",
            )
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
