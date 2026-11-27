"""Channel start must try the next stream when a stream's URL cannot be resolved.

Seen in production: the first stream of a channel was on a Stalker portal that
answered 403 to the login. generate_stream_url gave up at once, the client got
a 503, and the channel's second stream (another provider) was never tried.
"""

from unittest.mock import patch

from django.test import TestCase

from apps.channels.models import Channel, ChannelStream, Stream
from apps.m3u.models import M3UAccount, M3UAccountProfile
from apps.m3u.stalker import StalkerError
from apps.proxy.live_proxy.url_utils import StreamStartState, generate_stream_url
from core.models import PROXY_PROFILE_NAME, StreamProfile


class StartupTriesNextStreamTests(TestCase):
    def setUp(self):
        self.proxy_profile = StreamProfile.objects.create(name=PROXY_PROFILE_NAME, locked=True)
        self.channel = Channel.objects.create(channel_number=61, name="HGTV - US")
        self.streams = []
        self.profiles = []
        for index, name in enumerate(["tiger", "diablo"]):
            account = M3UAccount.objects.create(
                name=name,
                account_type=M3UAccount.Types.STALKER,
                server_url=f"http://{name}.example.com/c/",
                custom_properties={"mac": "00:1A:79:00:00:40"},
            )
            stream = Stream.objects.create(
                name=f"HGTV {name}",
                url=f"http://{name}.example.com/stalker_portal/server/load.php",
                m3u_account=account,
                stream_hash=f"hgtv-{name}",
                custom_properties={"cmd": "ffmpeg http://x/ch/1"},
            )
            ChannelStream.objects.create(channel=self.channel, stream=stream, order=index)
            self.streams.append(stream)
            self.profiles.append(
                M3UAccountProfile.objects.get(m3u_account=account, is_default=True)
            )

        self.enterContext(patch.object(Channel, "get_stream_profile", return_value=self.proxy_profile))
        self.enterContext(patch("apps.proxy.live_proxy.url_utils.close_old_connections"))
        self.release = self.enterContext(patch.object(Channel, "release_stream", return_value=True))

    def _assign_in_order(self, exclude_stream_ids=None, slot_reserved=True):
        for stream, profile in zip(self.streams, self.profiles):
            if stream.id not in (exclude_stream_ids or []):
                return stream.id, profile.id, None, slot_reserved
        # What the real get_stream() answers when every stream is excluded.
        return None, None, "No active profiles found for any assigned stream", False

    def _resolve(self, broken_stream_names):
        def resolve(stream, **_kwargs):
            if stream.name in broken_stream_names:
                raise StalkerError("Request failed: 403 Client Error: Forbidden (handshake)")
            return {"url": f"http://{stream.name}.ok/live.ts", "user_agent": "UA", "input_headers": {}}

        return self.enterContext(patch(
            "apps.proxy.live_proxy.url_utils._resolve_live_stream_context", side_effect=resolve,
        ))

    def test_start_moves_on_to_the_next_stream_when_the_first_fails(self):
        self._resolve({"HGTV tiger"})
        get_stream = self.enterContext(patch.object(
            Channel, "get_stream",
            side_effect=lambda user, profiles, exclude_stream_ids=None, avoid_streams_in_use=False: self._assign_in_order(
                exclude_stream_ids
            ),
        ))

        url, _ua, _headers, _transcode, _profile, slot_reserved, error, stream_id = (
            generate_stream_url(str(self.channel.uuid))
        )

        self.assertEqual(url, "http://HGTV diablo.ok/live.ts")
        self.assertIsNone(error)
        self.assertTrue(slot_reserved)
        self.assertEqual(stream_id, self.streams[1].id)
        # The failed stream's slot was given back, and it was excluded next time.
        self.release.assert_called_once_with()
        self.assertEqual(
            get_stream.call_args_list[1].kwargs,
            {"exclude_stream_ids": [self.streams[0].id], "avoid_streams_in_use": True},
        )

    def test_first_attempt_keeps_the_usual_get_stream_call(self):
        self._resolve(set())
        get_stream = self.enterContext(patch.object(
            Channel, "get_stream", side_effect=lambda user, profiles: self._assign_in_order(),
        ))

        url, *_rest = generate_stream_url(str(self.channel.uuid))

        self.assertEqual(url, "http://HGTV tiger.ok/live.ts")
        get_stream.assert_called_once_with(None, None)
        self.release.assert_not_called()

    def test_when_every_stream_fails_the_resolution_error_is_reported(self):
        self._resolve({"HGTV tiger", "HGTV diablo"})
        self.enterContext(patch.object(
            Channel, "get_stream",
            side_effect=lambda user, profiles, exclude_stream_ids=None, avoid_streams_in_use=False: self._assign_in_order(
                exclude_stream_ids
            ),
        ))

        url, *_middle, error, stream_id = generate_stream_url(str(self.channel.uuid))

        self.assertIsNone(url)
        self.assertIsNone(stream_id)
        self.assertIn("403", error)
        # Not the misleading "maximum connection limits" error: the view must not
        # keep retrying as if the provider were full.
        self.assertNotIn("maximum connection limits", error)
        self.assertEqual(self.release.call_count, 2)

    def test_reused_assignment_of_a_running_channel_is_not_replaced(self):
        # slot_reserved=False: the assignment belongs to a channel that is
        # already running; switching it here would fight its stream manager.
        self._resolve({"HGTV tiger"})
        get_stream = self.enterContext(patch.object(
            Channel, "get_stream",
            side_effect=lambda user, profiles: self._assign_in_order(slot_reserved=False),
        ))

        url, *_middle, error, _stream_id = generate_stream_url(str(self.channel.uuid))

        self.assertIsNone(url)
        self.assertIn("403", error)
        get_stream.assert_called_once_with(None, None)
        self.release.assert_not_called()

    def test_a_slot_that_could_not_be_released_stops_the_fallback(self):
        self._resolve({"HGTV tiger"})
        self.release.return_value = False
        get_stream = self.enterContext(patch.object(
            Channel, "get_stream",
            side_effect=lambda user, profiles, exclude_stream_ids=None, avoid_streams_in_use=False: self._assign_in_order(
                exclude_stream_ids
            ),
        ))

        url, *_middle, error, _stream_id = generate_stream_url(str(self.channel.uuid))

        self.assertIsNone(url)
        self.assertIn("403", error)
        get_stream.assert_called_once()

    def test_full_backup_keeps_the_capacity_error_so_the_view_retries(self):
        self._resolve({"HGTV tiger"})

        def assign(user, profiles, exclude_stream_ids=None, avoid_streams_in_use=False):
            if exclude_stream_ids:
                return None, None, "All active M3U profiles have reached maximum connection limits", False
            return self._assign_in_order()

        self.enterContext(patch.object(Channel, "get_stream", side_effect=assign))

        url, *_middle, error, _stream_id = generate_stream_url(str(self.channel.uuid))

        self.assertIsNone(url)
        self.assertIn("maximum connection limits", error)

    def test_redirect_profile_keeps_the_single_stream_behavior(self):
        redirect_profile = StreamProfile.objects.create(name="Redirect", command="redirect")
        self.enterContext(patch.object(Channel, "get_stream_profile", return_value=redirect_profile))
        self.enterContext(patch.object(StreamProfile, "is_redirect", return_value=True))
        self._resolve({"HGTV tiger"})
        get_stream = self.enterContext(patch.object(
            Channel, "get_stream",
            side_effect=lambda user, profiles, exclude_stream_ids=None, avoid_streams_in_use=False: self._assign_in_order(
                exclude_stream_ids
            ),
        ))

        url, *_middle, error, _stream_id = generate_stream_url(str(self.channel.uuid))

        self.assertIsNone(url)
        self.assertIn("403", error)
        get_stream.assert_called_once()

    def test_fallback_can_be_turned_off(self):
        # The explicit redirect route hands the URL to the client: keep one stream.
        self._resolve({"HGTV tiger"})
        get_stream = self.enterContext(patch.object(
            Channel, "get_stream",
            side_effect=lambda user, profiles, exclude_stream_ids=None, avoid_streams_in_use=False: self._assign_in_order(
                exclude_stream_ids
            ),
        ))

        url, *_middle, error, _stream_id = generate_stream_url(
            str(self.channel.uuid), allow_stream_fallback=False
        )

        self.assertIsNone(url)
        self.assertIn("403", error)
        get_stream.assert_called_once()

    def test_no_further_stream_once_the_time_budget_is_spent(self):
        self._resolve({"HGTV tiger"})
        get_stream = self.enterContext(patch.object(
            Channel, "get_stream",
            side_effect=lambda user, profiles, exclude_stream_ids=None, avoid_streams_in_use=False: self._assign_in_order(
                exclude_stream_ids
            ),
        ))
        # Start, then the first resolution ends after the 15 s budget.
        self.enterContext(patch(
            "apps.proxy.live_proxy.url_utils.time.monotonic", side_effect=[0.0, 20.0]
        ))

        url, *_middle, error, _stream_id = generate_stream_url(str(self.channel.uuid))

        self.assertIsNone(url)
        self.assertIn("403", error)
        get_stream.assert_called_once()

    def test_failed_slot_release_stops_the_whole_start(self):
        self._resolve({"HGTV tiger"})
        self.release.return_value = False
        self.enterContext(patch.object(
            Channel, "get_stream",
            side_effect=lambda user, profiles, exclude_stream_ids=None, avoid_streams_in_use=False: self._assign_in_order(
                exclude_stream_ids
            ),
        ))
        state = StreamStartState()

        url, *_rest = generate_stream_url(str(self.channel.uuid), start_state=state)

        self.assertIsNone(url)
        self.assertTrue(state.stop)

    def test_lost_startup_ownership_stops_the_fallback(self):
        self._resolve({"HGTV tiger"})
        get_stream = self.enterContext(patch.object(
            Channel, "get_stream",
            side_effect=lambda user, profiles, exclude_stream_ids=None, avoid_streams_in_use=False: self._assign_in_order(
                exclude_stream_ids
            ),
        ))
        state = StreamStartState(keep_alive=lambda: False)

        url, *_rest = generate_stream_url(str(self.channel.uuid), start_state=state)

        self.assertIsNone(url)
        self.assertTrue(state.stop)
        get_stream.assert_called_once()

    def test_startup_lease_is_renewed_before_the_next_stream(self):
        self._resolve({"HGTV tiger"})
        self.enterContext(patch.object(
            Channel, "get_stream",
            side_effect=lambda user, profiles, exclude_stream_ids=None, avoid_streams_in_use=False: self._assign_in_order(
                exclude_stream_ids
            ),
        ))
        renewals = []
        state = StreamStartState(keep_alive=lambda: renewals.append(1) or True)

        url, *_rest = generate_stream_url(str(self.channel.uuid), start_state=state)

        self.assertEqual(url, "http://HGTV diablo.ok/live.ts")
        self.assertEqual(renewals, [1])

    def test_failed_streams_are_remembered_across_view_retries(self):
        # Attempt 1: tiger fails and diablo is full. Attempt 2 must not go back
        # to tiger (another 403, another slot) but straight to diablo.
        self._resolve({"HGTV tiger"})
        full = {"diablo": True}

        def assign(user, profiles, exclude_stream_ids=None, avoid_streams_in_use=False):
            if exclude_stream_ids and full["diablo"]:
                return None, None, "All active M3U profiles have reached maximum connection limits", False
            return self._assign_in_order(exclude_stream_ids)

        get_stream = self.enterContext(patch.object(Channel, "get_stream", side_effect=assign))
        state = StreamStartState()

        first = generate_stream_url(str(self.channel.uuid), start_state=state)
        full["diablo"] = False
        second = generate_stream_url(str(self.channel.uuid), start_state=state)

        self.assertIn("maximum connection limits", first[6])
        self.assertIn("403", first[6])
        self.assertEqual(second[0], "http://HGTV diablo.ok/live.ts")
        self.assertEqual(
            get_stream.call_args_list[2].kwargs,
            {"exclude_stream_ids": [self.streams[0].id], "avoid_streams_in_use": True},
        )
        self.assertEqual(self.release.call_count, 1)

    def test_error_returned_to_the_client_has_no_url(self):
        def resolve(stream, **_kwargs):
            raise StalkerError(
                "Request failed: 403 Client Error: Forbidden for url: "
                "http://tiger.example.com/portal.php?action=handshake&token=SECRET"
            )

        self.enterContext(patch(
            "apps.proxy.live_proxy.url_utils._resolve_live_stream_context", side_effect=resolve,
        ))
        self.enterContext(patch.object(
            Channel, "get_stream",
            side_effect=lambda user, profiles, exclude_stream_ids=None, avoid_streams_in_use=False: self._assign_in_order(
                exclude_stream_ids
            ),
        ))

        *_rest, error, _stream_id = generate_stream_url(str(self.channel.uuid))

        self.assertIn("403", error)
        self.assertNotIn("SECRET", error)
        self.assertNotIn("http://", error)


class StartupNextStreamRedisTests(TestCase):
    """Real stream selection and slot release against the test Redis."""

    def setUp(self):
        from core.utils import RedisClient

        self.redis = RedisClient.get_client()
        self.proxy_profile = StreamProfile.objects.create(name=PROXY_PROFILE_NAME, locked=True)
        self.channel = Channel.objects.create(channel_number=62, name="HGTV - US (redis)")
        self.streams, self.profiles = [], []
        for index, name in enumerate(["tiger", "diablo"]):
            account = M3UAccount.objects.create(
                name=f"{name}-redis",
                account_type=M3UAccount.Types.STALKER,
                server_url=f"http://{name}.example.com/c/",
                max_streams=1,
                custom_properties={"mac": f"00:1A:79:00:00:4{index}"},
            )
            profile = M3UAccountProfile.objects.get(m3u_account=account, is_default=True)
            profile.max_streams = 1
            profile.save()
            stream = Stream.objects.create(
                name=f"HGTV {name}",
                url=f"http://{name}.example.com/stalker_portal/server/load.php",
                m3u_account=account,
                stream_hash=f"hgtv-redis-{name}",
                custom_properties={"cmd": "ffmpeg http://x/ch/1"},
            )
            ChannelStream.objects.create(channel=self.channel, stream=stream, order=index)
            self.streams.append(stream)
            self.profiles.append(profile)
        self.addCleanup(self._clear_keys)
        self.enterContext(patch.object(Channel, "get_stream_profile", return_value=self.proxy_profile))
        self.enterContext(patch("apps.proxy.live_proxy.url_utils.close_old_connections"))

    def _clear_keys(self):
        from apps.m3u.connection_pool import profile_connections_key

        keys = [f"channel_stream:{self.channel.id}"]
        for stream, profile in zip(self.streams, self.profiles):
            keys += [f"stream_profile:{stream.id}", profile_connections_key(profile.id)]
        self.redis.delete(*keys)

    def _count(self, profile):
        from apps.m3u.connection_pool import get_profile_connection_count

        return get_profile_connection_count(profile, self.redis)

    def _resolve(self, broken_stream_names):
        def resolve(stream, **_kwargs):
            if stream.name in broken_stream_names:
                raise StalkerError("Request failed: 403 Client Error: Forbidden (handshake)")
            return {"url": f"http://{stream.name}.ok/live.ts", "user_agent": "UA", "input_headers": {}}

        self.enterContext(patch(
            "apps.proxy.live_proxy.url_utils._resolve_live_stream_context", side_effect=resolve,
        ))

    def test_backup_takes_exactly_one_slot_and_the_failed_one_is_freed(self):
        self._resolve({"HGTV tiger"})

        url, *_middle, slot_reserved, error, stream_id = generate_stream_url(str(self.channel.uuid))

        self.assertEqual(url, "http://HGTV diablo.ok/live.ts")
        self.assertIsNone(error)
        self.assertTrue(slot_reserved)
        self.assertEqual(stream_id, self.streams[1].id)
        self.assertEqual(self._count(self.profiles[0]), 0)
        self.assertEqual(self._count(self.profiles[1]), 1)
        self.assertEqual(
            int(self.redis.get(f"channel_stream:{self.channel.id}")), self.streams[1].id
        )

    def test_every_stream_failing_leaves_no_slot_counted(self):
        self._resolve({"HGTV tiger", "HGTV diablo"})

        url, *_middle, error, _stream_id = generate_stream_url(str(self.channel.uuid))

        self.assertIsNone(url)
        self.assertIn("403", error)
        self.assertEqual(self._count(self.profiles[0]), 0)
        self.assertEqual(self._count(self.profiles[1]), 0)

    def test_backup_used_by_another_channel_is_not_taken_over(self):
        self._resolve({"HGTV tiger"})
        # Another channel is playing the backup stream.
        self.redis.set(f"stream_profile:{self.streams[1].id}", self.profiles[1].id)

        url, *_middle, error, _stream_id = generate_stream_url(str(self.channel.uuid))

        self.assertIsNone(url)
        self.assertIn("403", error)
        self.assertEqual(self._count(self.profiles[0]), 0)
        self.assertEqual(
            int(self.redis.get(f"stream_profile:{self.streams[1].id}")), self.profiles[1].id
        )
