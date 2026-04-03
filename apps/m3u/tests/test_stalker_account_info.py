from datetime import datetime, timezone as dt_timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.m3u.models import M3UAccount
from apps.m3u.stalker import (
    StalkerAccountInfoResult,
    StalkerClient,
    StalkerGenreDiscoveryResult,
)
from apps.m3u.tasks import refresh_m3u_groups


User = get_user_model()


class StalkerPhase16AccountInfoClientTests(TestCase):
    def test_client_discovers_and_normalizes_account_info(self):
        client = StalkerClient(
            server_url="http://portal.example.com/c/",
            mac="00:1A:79:00:00:80",
            custom_properties={"timezone": "America/Toronto"},
        )

        with (
            patch.object(client, "handshake"),
            patch.object(
                client,
                "get_profile",
                return_value={"name": "Demo", "status": "1", "online": "1"},
            ),
            patch.object(
                client,
                "get_main_info",
                return_value={
                    "login": "demo-user",
                    "end_date": "2030-04-01",
                    "account_number": "12345",
                    "tariff_plan": "Premium",
                },
            ),
        ):
            result = client.discover_account_info()

        expected_expiration = datetime(
            2030,
            4,
            1,
            23,
            59,
            59,
            tzinfo=ZoneInfo("America/Toronto"),
        )

        self.assertEqual(
            result.normalized_portal_url,
            "http://portal.example.com/server/load.php",
        )
        self.assertEqual(result.account_info["user_info"]["username"], "demo-user")
        self.assertEqual(result.account_info["user_info"]["status"], "Active")
        self.assertEqual(
            result.account_info["user_info"]["exp_date"],
            str(int(expected_expiration.timestamp())),
        )
        self.assertEqual(result.account_info["user_info"]["account_number"], "12345")
        self.assertEqual(result.account_info["user_info"]["tariff_plan"], "Premium")

    def test_client_maps_date_like_phone_to_expiration_and_numeric_name_to_account_number(
        self,
    ):
        client = StalkerClient(
            server_url="http://portal.example.com/c/",
            mac="00:1A:79:00:00:81",
        )

        with (
            patch.object(client, "handshake"),
            patch.object(
                client,
                "get_profile",
                return_value={
                    "name": "1252202",
                    "status": 0,
                    "blocked": "0",
                    "created": "0",
                    "default_timezone": "Europe/Amsterdam",
                },
            ),
            patch.object(
                client,
                "get_main_info",
                return_value={"phone": "December 25, 2026, 8:48 pm"},
            ),
        ):
            result = client.discover_account_info()

        expected_expiration = datetime(
            2026,
            12,
            25,
            20,
            48,
            tzinfo=ZoneInfo("Europe/Amsterdam"),
        )

        self.assertEqual(
            result.account_info["user_info"]["exp_date"],
            str(int(expected_expiration.timestamp())),
        )
        self.assertEqual(result.account_info["user_info"]["status"], "Active")
        self.assertEqual(
            result.account_info["user_info"]["account_number"],
            "1252202",
        )
        self.assertIsNone(result.account_info["user_info"]["phone"])
        self.assertIsNone(result.account_info["user_info"]["full_name"])
        self.assertIsNone(result.account_info["user_info"]["created_at"])
        self.assertEqual(
            result.account_info["server_info"]["timezone"],
            "Europe/Amsterdam",
        )

    def test_client_marks_blocked_accounts_as_disabled(self):
        client = StalkerClient(
            server_url="http://portal.example.com/c/",
            mac="00:1A:79:00:00:82",
        )

        with (
            patch.object(client, "handshake"),
            patch.object(
                client,
                "get_profile",
                return_value={
                    "status": 0,
                    "blocked": "1",
                },
            ),
            patch.object(
                client,
                "get_main_info",
                return_value={"phone": "December 25, 2026, 8:48 pm"},
            ),
        ):
            result = client.discover_account_info()

        self.assertEqual(result.account_info["user_info"]["status"], "Disabled")


class StalkerPhase16AccountInfoPersistenceTests(TestCase):
    def setUp(self):
        self.account = M3UAccount.objects.create(
            name="Stalker Account Info",
            account_type=M3UAccount.Types.STALKER,
            server_url="http://portal.example.com/c/",
            username="demo",
            password="secret",
            custom_properties={
                "mac": "00:1A:79:00:00:81",
                "timezone": "America/Toronto",
            },
        )
        self.default_profile = self.account.profiles.get(is_default=True)
        self.secondary_profile = self.account.profiles.create(
            name="Secondary",
            max_streams=1,
            search_pattern="(.*)",
            replace_pattern="\\1",
        )

    def test_profile_save_with_custom_properties_update_persists_exp_date(self):
        self.default_profile.custom_properties = {
            "user_info": {
                "status": "Active",
                "exp_date": "1893456000",
            }
        }

        self.default_profile.save(update_fields=["custom_properties"])
        self.default_profile.refresh_from_db()

        self.assertEqual(
            self.default_profile.exp_date,
            datetime.fromtimestamp(1893456000, tz=dt_timezone.utc),
        )

    @patch("apps.m3u.tasks.send_m3u_update")
    @patch("apps.m3u.tasks.release_task_lock")
    @patch("apps.m3u.tasks.TaskLockRenewer")
    @patch("apps.m3u.tasks.acquire_task_lock", return_value=True)
    @patch("apps.m3u.tasks.StalkerClient.discover_account_info")
    @patch("apps.m3u.tasks.StalkerClient.discover_live_genres")
    def test_refresh_groups_persists_stalker_account_info_on_profiles(
        self,
        mock_discover_live_genres,
        mock_discover_account_info,
        _mock_lock,
        mock_renewer_cls,
        _mock_release,
        _mock_update,
    ):
        mock_renewer = mock_renewer_cls.return_value
        mock_discover_live_genres.return_value = StalkerGenreDiscoveryResult(
            normalized_portal_url="http://portal.example.com/stalker_portal/server/load.php",
            profile_name="Demo",
            genres=[{"id": "10", "title": "News"}],
            token="TOKEN-123",
            used_authentication=True,
        )
        mock_discover_account_info.return_value = StalkerAccountInfoResult(
            normalized_portal_url="http://portal.example.com/stalker_portal/server/load.php",
            profile_name="Demo",
            account_info={
                "last_refresh": "2026-03-28T12:00:00Z",
                "auth_timestamp": 1711627200,
                "user_info": {
                    "username": "demo-user",
                    "status": "Active",
                    "exp_date": "1893456000",
                },
                "server_info": {
                    "url": "http://portal.example.com/stalker_portal/server/load.php",
                    "timezone": "America/Toronto",
                },
            },
            token="TOKEN-456",
            used_authentication=True,
        )

        extinf_data, groups = refresh_m3u_groups(self.account.id)

        self.assertEqual(extinf_data, [])
        self.assertEqual(groups, {"News": {"stalker_genre_id": "10"}})

        self.account.refresh_from_db()
        self.default_profile.refresh_from_db()
        self.secondary_profile.refresh_from_db()

        self.assertEqual(
            self.account.custom_properties["stalker_portal_url"],
            "http://portal.example.com/stalker_portal/server/load.php",
        )
        self.assertEqual(self.account.custom_properties["token"], "TOKEN-456")
        self.assertEqual(
            self.default_profile.custom_properties["user_info"]["status"],
            "Active",
        )
        self.assertEqual(
            self.secondary_profile.custom_properties["user_info"]["status"],
            "Active",
        )
        self.assertEqual(
            self.default_profile.exp_date,
            datetime.fromtimestamp(1893456000, tz=dt_timezone.utc),
        )
        self.assertEqual(
            self.secondary_profile.exp_date,
            datetime.fromtimestamp(1893456000, tz=dt_timezone.utc),
        )
        mock_renewer.start.assert_called_once()
        mock_renewer.stop.assert_called_once()


class StalkerPhase16AccountInfoApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="admin",
            password="testpass123",
            user_level=10,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.account = M3UAccount.objects.create(
            name="Stalker API Refresh",
            account_type=M3UAccount.Types.STALKER,
            server_url="http://portal.example.com/c/",
            custom_properties={"mac": "00:1A:79:00:00:82"},
        )
        self.profile = self.account.profiles.get(is_default=True)

    @patch("apps.m3u.api_views.refresh_account_info.delay")
    def test_refresh_account_info_endpoint_accepts_stalker(self, mock_delay):
        response = self.client.post(
            f"/api/m3u/refresh-account-info/{self.profile.id}/"
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        mock_delay.assert_called_once_with(self.profile.id)
