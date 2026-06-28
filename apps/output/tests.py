from django.test import TestCase, Client, RequestFactory
from django.http import Http404
from django.utils import timezone
from django.urls import reverse
from datetime import timedelta
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch
from apps.accounts.models import User
from apps.channels.models import Channel, ChannelGroup
from apps.epg.models import EPGData, EPGSource
from apps.m3u.models import M3UAccount
from apps.vod.models import (
    Episode,
    Movie,
    Series,
    VODCategory,
    M3UMovieRelation,
    M3UEpisodeRelation,
    M3USeriesRelation,
    M3UVODCategoryRelation,
)
from apps.output.views import (
    xc_get_vod_categories,
    xc_get_vod_streams,
    xc_get_series_categories,
    xc_get_series,
    xc_get_series_info,
    xc_get_vod_info,
    xc_movie_stream,
)
import xml.etree.ElementTree as ET

class OutputM3UTest(TestCase):
    def setUp(self):
        self.client = Client()
    
    def test_generate_m3u_response(self):
        """
        Test that the M3U endpoint returns a valid M3U file.
        """
        url = reverse('output:generate_m3u')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("#EXTM3U", content)

    def test_generate_m3u_response_post_empty_body(self):
        """
        Test that a POST request with an empty body returns 200 OK.
        """
        url = reverse('output:generate_m3u')

        response = self.client.post(url, data=None, content_type='application/x-www-form-urlencoded')
        content = response.content.decode()

        self.assertEqual(response.status_code, 200, "POST with empty body should return 200 OK")
        self.assertIn("#EXTM3U", content)

    def test_generate_m3u_response_post_with_body(self):
        """
        Test that a POST request with a non-empty body returns 403 Forbidden.
        """
        url = reverse('output:generate_m3u')

        response = self.client.post(url, data={'evilstring': 'muhahaha'})

        self.assertEqual(response.status_code, 403, "POST with body should return 403 Forbidden")
        self.assertIn("POST requests with body are not allowed, body is:", response.content.decode())


class OutputEPGXMLEscapingTest(TestCase):
    """Test XML escaping of channel_id attributes in EPG generation"""

    def setUp(self):
        self.client = Client()
        self.group = ChannelGroup.objects.create(name="Test Group")

    def test_channel_id_with_ampersand(self):
        """Test channel ID with ampersand is properly escaped"""
        channel = Channel.objects.create(
            channel_number=1.0,
            name="Test Channel",
            tvg_id="News & Sports",
            channel_group=self.group
        )

        url = reverse('output:generate_epg') + '?tvg_id_source=tvg_id'
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()

        # Should contain escaped ampersand
        self.assertIn('id="News &amp; Sports"', content)
        self.assertNotIn('id="News & Sports"', content)

        # Verify XML is parseable
        try:
            ET.fromstring(content)
        except ET.ParseError as e:
            self.fail(f"Generated EPG is not valid XML: {e}")

    def test_channel_id_with_angle_brackets(self):
        """Test channel ID with < and > characters"""
        channel = Channel.objects.create(
            channel_number=2.0,
            name="HD Channel",
            tvg_id="Channel <HD>",
            channel_group=self.group
        )

        url = reverse('output:generate_epg') + '?tvg_id_source=tvg_id'
        response = self.client.get(url)

        content = response.content.decode()
        self.assertIn('id="Channel &lt;HD&gt;"', content)

        try:
            ET.fromstring(content)
        except ET.ParseError as e:
            self.fail(f"Generated EPG with < > is not valid XML: {e}")

    def test_channel_id_with_all_special_chars(self):
        """Test channel ID with all XML special characters"""
        channel = Channel.objects.create(
            channel_number=3.0,
            name="Complex Channel",
            tvg_id='Test & "Special" <Chars>',
            channel_group=self.group
        )

        url = reverse('output:generate_epg') + '?tvg_id_source=tvg_id'
        response = self.client.get(url)

        content = response.content.decode()
        self.assertIn('id="Test &amp; &quot;Special&quot; &lt;Chars&gt;"', content)

        try:
            tree = ET.fromstring(content)
            # Verify we can find the channel with correct ID in parsed tree
            channel_elem = tree.find('.//channel[@id="Test & \\"Special\\" <Chars>"]')
            self.assertIsNotNone(channel_elem)
        except ET.ParseError as e:
            self.fail(f"Generated EPG with all special chars is not valid XML: {e}")

    def test_program_channel_attribute_escaping(self):
        """Test that programme elements also have escaped channel attributes"""
        epg_source = EPGSource.objects.create(name="Test EPG", source_type="dummy")
        epg_data = EPGData.objects.create(name="Test EPG Data", epg_source=epg_source)
        channel = Channel.objects.create(
            channel_number=4.0,
            name="Program Test",
            tvg_id="News & Sports",
            epg_data=epg_data,
            channel_group=self.group
        )

        url = reverse('output:generate_epg') + '?tvg_id_source=tvg_id'
        response = self.client.get(url)

        content = response.content.decode()

        # Check programme elements have escaped channel attributes
        self.assertIn('channel="News &amp; Sports"', content)

        try:
            tree = ET.fromstring(content)
            programmes = tree.findall('.//programme[@channel="News & Sports"]')
            self.assertGreater(len(programmes), 0)
        except ET.ParseError as e:
            self.fail(f"Generated EPG with programme elements is not valid XML: {e}")


class OutputXtreamVodVisibilityTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.account = M3UAccount.objects.create(
            name="vod-account",
            account_type=M3UAccount.Types.XC,
            is_active=True,
            custom_properties={"enable_vod": True},
        )

        self.enabled_movie_category = VODCategory.objects.create(
            name="Enabled Movies",
            category_type="movie",
        )
        self.disabled_movie_category = VODCategory.objects.create(
            name="Disabled Movies",
            category_type="movie",
        )
        self.enabled_series_category = VODCategory.objects.create(
            name="Enabled Series",
            category_type="series",
        )
        self.disabled_series_category = VODCategory.objects.create(
            name="Disabled Series",
            category_type="series",
        )

        M3UVODCategoryRelation.objects.create(
            m3u_account=self.account,
            category=self.enabled_movie_category,
            enabled=True,
        )
        M3UVODCategoryRelation.objects.create(
            m3u_account=self.account,
            category=self.disabled_movie_category,
            enabled=False,
        )
        M3UVODCategoryRelation.objects.create(
            m3u_account=self.account,
            category=self.enabled_series_category,
            enabled=True,
        )
        M3UVODCategoryRelation.objects.create(
            m3u_account=self.account,
            category=self.disabled_series_category,
            enabled=False,
        )

        self.enabled_movie = Movie.objects.create(name="Enabled Movie")
        self.disabled_movie = Movie.objects.create(name="Disabled Movie")
        self.enabled_movie_relation = M3UMovieRelation.objects.create(
            m3u_account=self.account,
            movie=self.enabled_movie,
            category=self.enabled_movie_category,
            stream_id="enabled-movie",
            last_advanced_refresh=timezone.now(),
        )
        self.disabled_movie_relation = M3UMovieRelation.objects.create(
            m3u_account=self.account,
            movie=self.disabled_movie,
            category=self.disabled_movie_category,
            stream_id="disabled-movie",
            last_advanced_refresh=timezone.now(),
        )

        self.enabled_series = Series.objects.create(name="Enabled Series Title")
        self.disabled_series = Series.objects.create(name="Disabled Series Title")
        self.enabled_series_relation = M3USeriesRelation.objects.create(
            m3u_account=self.account,
            series=self.enabled_series,
            category=self.enabled_series_category,
            external_series_id="enabled-series",
            last_episode_refresh=timezone.now(),
            custom_properties={
                "episodes_fetched": True,
                "detailed_fetched": True,
            },
        )
        self.disabled_series_relation = M3USeriesRelation.objects.create(
            m3u_account=self.account,
            series=self.disabled_series,
            category=self.disabled_series_category,
            external_series_id="disabled-series",
            last_episode_refresh=timezone.now(),
            custom_properties={
                "episodes_fetched": True,
                "detailed_fetched": True,
            },
        )

    def test_xc_get_vod_categories_only_returns_enabled_categories(self):
        response = xc_get_vod_categories(user=None)
        category_names = {row["category_name"] for row in response}

        self.assertIn("Enabled Movies", category_names)
        self.assertNotIn("Disabled Movies", category_names)

    def test_xc_get_vod_streams_excludes_disabled_category_content(self):
        request = self.factory.get("/player_api.php")

        response = xc_get_vod_streams(request, user=None)
        movie_names = {row["name"] for row in response}

        self.assertIn("Enabled Movie", movie_names)
        self.assertNotIn("Disabled Movie", movie_names)

        filtered_response = xc_get_vod_streams(
            request,
            user=None,
            category_id=self.disabled_movie_category.id,
        )
        self.assertEqual(filtered_response, [])

    def test_xc_get_series_categories_only_returns_enabled_categories(self):
        response = xc_get_series_categories(user=None)
        category_names = {row["category_name"] for row in response}

        self.assertIn("Enabled Series", category_names)
        self.assertNotIn("Disabled Series", category_names)

    def test_xc_get_series_excludes_disabled_category_content(self):
        request = self.factory.get("/player_api.php")

        response = xc_get_series(request, user=None)
        series_names = {row["name"] for row in response}

        self.assertIn("Enabled Series Title", series_names)
        self.assertNotIn("Disabled Series Title", series_names)

        filtered_response = xc_get_series(
            request,
            user=None,
            category_id=self.disabled_series_category.id,
        )
        self.assertEqual(filtered_response, [])

    def test_xc_get_series_info_rejects_disabled_category_relation(self):
        request = self.factory.get("/player_api.php")

        with self.assertRaises(Http404):
            xc_get_series_info(
                request,
                user=None,
                series_id=self.disabled_series_relation.id,
            )

    def test_xc_get_vod_info_rejects_disabled_category_relation(self):
        request = self.factory.get("/player_api.php")

        with self.assertRaises(Http404):
            xc_get_vod_info(
                request,
                user=None,
                vod_id=self.disabled_movie.id,
            )

    def test_xtream_output_excludes_accounts_with_vod_disabled(self):
        disabled_account = M3UAccount.objects.create(
            name="vod-disabled-account",
            account_type=M3UAccount.Types.STALKER,
            is_active=True,
            custom_properties={"enable_vod": False},
        )
        category = VODCategory.objects.create(name="Hidden Movies", category_type="movie")
        movie = Movie.objects.create(name="Hidden Movie")
        M3UVODCategoryRelation.objects.create(
            m3u_account=disabled_account,
            category=category,
            enabled=True,
        )
        M3UMovieRelation.objects.create(
            m3u_account=disabled_account,
            movie=movie,
            category=category,
            stream_id="hidden-movie",
            last_advanced_refresh=timezone.now(),
        )

        request = self.factory.get("/player_api.php")

        categories = xc_get_vod_categories(user=None)
        streams = xc_get_vod_streams(request, user=None)

        self.assertNotIn("Hidden Movies", {row["category_name"] for row in categories})
        self.assertNotIn("Hidden Movie", {row["name"] for row in streams})


class OutputXtreamRelationSelectionTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.account = M3UAccount.objects.create(
            name="stalker-output-account",
            account_type=M3UAccount.Types.STALKER,
            is_active=True,
            priority=100,
            custom_properties={"enable_vod": True},
        )
        self.user = User.objects.create_user(
            username="xtream-user",
            password="testpass123",
            custom_properties={"xc_password": "secret"},
        )

        self.fr_movie_category = VODCategory.objects.create(
            name="|FR| FILMS 2026",
            category_type="movie",
        )
        self.bg_movie_category = VODCategory.objects.create(
            name="|BG| BULGARIA FILMI",
            category_type="movie",
        )
        self.fr_series_category = VODCategory.objects.create(
            name="|FR| SERIES",
            category_type="series",
        )
        self.bg_series_category = VODCategory.objects.create(
            name="|BG| SERIES",
            category_type="series",
        )

        for category in (
            self.fr_movie_category,
            self.bg_movie_category,
            self.fr_series_category,
            self.bg_series_category,
        ):
            M3UVODCategoryRelation.objects.create(
                m3u_account=self.account,
                category=category,
                enabled=True,
            )

        self.movie = Movie.objects.create(
            name="BG - Accused (2026)",
            year=2026,
        )
        self.fr_movie_relation = M3UMovieRelation.objects.create(
            m3u_account=self.account,
            movie=self.movie,
            category=self.fr_movie_category,
            stream_id="fr-movie-stream",
            last_advanced_refresh=timezone.now(),
            custom_properties={
                "basic_data": {
                    "title": "FR - Accused (2026)",
                }
            },
        )
        self.bg_movie_relation = M3UMovieRelation.objects.create(
            m3u_account=self.account,
            movie=self.movie,
            category=self.bg_movie_category,
            stream_id="bg-movie-stream",
            last_advanced_refresh=timezone.now(),
            custom_properties={
                "basic_data": {
                    "title": "BG - Accused (2026)",
                }
            },
        )

        self.series = Series.objects.create(
            name="BG - Example Series",
            year=2026,
        )
        self.fr_series_relation = M3USeriesRelation.objects.create(
            m3u_account=self.account,
            series=self.series,
            category=self.fr_series_category,
            external_series_id="fr-series",
            last_episode_refresh=timezone.now(),
            custom_properties={
                "basic_data": {
                    "title": "FR - Example Series",
                },
                "episodes_fetched": True,
                "detailed_fetched": True,
            },
        )
        self.bg_series_relation = M3USeriesRelation.objects.create(
            m3u_account=self.account,
            series=self.series,
            category=self.bg_series_category,
            external_series_id="bg-series",
            last_episode_refresh=timezone.now(),
            custom_properties={
                "basic_data": {
                    "title": "BG - Example Series",
                },
                "episodes_fetched": True,
                "detailed_fetched": True,
            },
        )

    def test_xc_get_vod_streams_uses_relation_title_and_relation_id(self):
        request = self.factory.get("/player_api.php")

        response = xc_get_vod_streams(
            request,
            user=None,
            category_id=self.fr_movie_category.id,
        )

        self.assertEqual(len(response), 1)
        self.assertEqual(response[0]["name"], "FR - Accused (2026)")
        self.assertEqual(response[0]["stream_id"], self.fr_movie_relation.id)
        self.assertEqual(response[0]["num"], self.fr_movie_relation.id)

    def test_xc_get_vod_info_uses_relation_title_and_relation_id(self):
        request = self.factory.get("/player_api.php")

        response = xc_get_vod_info(
            request,
            user=None,
            vod_id=self.fr_movie_relation.id,
        )

        self.assertEqual(response["info"]["name"], "FR - Accused (2026)")
        self.assertEqual(response["movie_data"]["name"], "FR - Accused (2026)")
        self.assertEqual(response["movie_data"]["stream_id"], self.fr_movie_relation.id)
        self.assertEqual(
            response["movie_data"]["category_id"],
            str(self.fr_movie_category.id),
        )

    def test_xc_movie_stream_redirects_with_selected_relation_stream(self):
        request = self.factory.get("/movie/xtream-user/secret/1.mp4")

        response = xc_movie_stream(
            request,
            username=self.user.username,
            password="secret",
            stream_id=str(self.fr_movie_relation.id),
            extension="mp4",
        )

        self.assertEqual(response.status_code, 302)
        location = response["Location"]
        parsed = urlparse(location)
        self.assertEqual(
            parsed.path,
            reverse(
                "proxy:vod_proxy:vod_stream",
                kwargs={
                    "content_type": "movie",
                    "content_id": self.movie.uuid,
                },
            ),
        )
        params = parse_qs(parsed.query)
        self.assertEqual(params["stream_id"], [self.fr_movie_relation.stream_id])
        self.assertEqual(params["m3u_account_id"], [str(self.account.id)])

    def test_xc_get_series_uses_relation_title(self):
        request = self.factory.get("/player_api.php")

        response = xc_get_series(
            request,
            user=None,
            category_id=self.fr_series_category.id,
        )

        self.assertEqual(len(response), 1)
        self.assertEqual(response[0]["name"], "FR - Example Series")
        self.assertEqual(response[0]["series_id"], self.fr_series_relation.id)

    def test_xc_get_series_info_uses_relation_title(self):
        request = self.factory.get("/player_api.php")

        response = xc_get_series_info(
            request,
            user=None,
            series_id=self.fr_series_relation.id,
        )

        self.assertEqual(response["info"]["name"], "FR - Example Series")
        self.assertEqual(
            response["info"]["category_id"],
            str(self.fr_series_category.id),
        )


class OutputXtreamPlexStalkerCatalogOptimizationTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.account = M3UAccount.objects.create(
            name="stalker-plex-account",
            account_type=M3UAccount.Types.STALKER,
            is_active=True,
            custom_properties={"enable_vod": True},
        )
        self.movie_category = VODCategory.objects.create(
            name="Movies",
            category_type="movie",
        )
        self.series_category = VODCategory.objects.create(
            name="Series",
            category_type="series",
        )
        M3UVODCategoryRelation.objects.create(
            m3u_account=self.account,
            category=self.movie_category,
            enabled=True,
        )
        M3UVODCategoryRelation.objects.create(
            m3u_account=self.account,
            category=self.series_category,
            enabled=True,
        )

        self.movie = Movie.objects.create(
            name="Catalog Movie",
            custom_properties={"director": "Cached Director"},
        )
        self.movie_relation = M3UMovieRelation.objects.create(
            m3u_account=self.account,
            movie=self.movie,
            category=self.movie_category,
            stream_id="catalog-movie",
            last_advanced_refresh=timezone.now() - timedelta(days=2),
        )

        self.series = Series.objects.create(name="Catalog Series")
        self.series_relation = M3USeriesRelation.objects.create(
            m3u_account=self.account,
            series=self.series,
            category=self.series_category,
            external_series_id="catalog-series",
            last_episode_refresh=timezone.now() - timedelta(days=2),
            custom_properties={
                "episodes_fetched": True,
                "detailed_fetched": True,
            },
        )
        self.episode = Episode.objects.create(
            series=self.series,
            name="Episode 1",
            season_number=1,
            episode_number=1,
        )
        M3UEpisodeRelation.objects.create(
            m3u_account=self.account,
            episode=self.episode,
            series_relation=self.series_relation,
            stream_id="catalog-series-episode-1",
        )

    @patch("apps.vod.tasks.refresh_movie_advanced_data")
    def test_xc_get_vod_info_skips_stalker_refresh_for_plex_catalog_requests(
        self,
        mock_refresh_movie_advanced_data,
    ):
        request = self.factory.get(
            "/player_api.php",
            HTTP_USER_AGENT="Plex Media Server",
        )

        response = xc_get_vod_info(
            request,
            user=None,
            vod_id=self.movie_relation.id,
        )

        mock_refresh_movie_advanced_data.assert_not_called()
        self.assertEqual(response["info"]["name"], "Catalog Movie")

    @patch("apps.vod.tasks.refresh_movie_advanced_data")
    def test_xc_get_vod_info_refreshes_for_non_plex_requests(
        self,
        mock_refresh_movie_advanced_data,
    ):
        request = self.factory.get(
            "/player_api.php",
            HTTP_USER_AGENT="VLC/3.0",
        )

        xc_get_vod_info(
            request,
            user=None,
            vod_id=self.movie_relation.id,
        )

        mock_refresh_movie_advanced_data.assert_called_once_with(self.movie_relation.id)

    @patch("apps.vod.tasks.refresh_series_episodes")
    def test_xc_get_series_info_skips_stalker_refresh_for_plex_catalog_requests(
        self,
        mock_refresh_series_episodes,
    ):
        request = self.factory.get(
            "/player_api.php",
            HTTP_X_PLEX_PRODUCT="Plex Media Server",
        )

        response = xc_get_series_info(
            request,
            user=None,
            series_id=self.series_relation.id,
        )

        mock_refresh_series_episodes.assert_not_called()
        self.assertEqual(response["info"]["name"], "Catalog Series")
        self.assertIn("1", response["episodes"])

    @patch("apps.vod.tasks.refresh_series_episodes")
    def test_xc_get_series_info_refreshes_for_non_plex_requests(
        self,
        mock_refresh_series_episodes,
    ):
        request = self.factory.get(
            "/player_api.php",
            HTTP_USER_AGENT="Kodi",
        )

        xc_get_series_info(
            request,
            user=None,
            series_id=self.series_relation.id,
        )

        mock_refresh_series_episodes.assert_called_once_with(
            self.account,
            self.series,
            self.series_relation.external_series_id,
        )
