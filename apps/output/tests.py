from django.test import TestCase, Client, RequestFactory
from django.http import Http404
from django.utils import timezone
from django.urls import reverse
from apps.channels.models import Channel, ChannelGroup
from apps.epg.models import EPGData, EPGSource
from apps.m3u.models import M3UAccount
from apps.vod.models import (
    Movie,
    Series,
    VODCategory,
    M3UMovieRelation,
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
