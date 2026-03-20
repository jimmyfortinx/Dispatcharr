import json
import logging
from dataclasses import dataclass
from secrets import token_hex
from posixpath import dirname, join
from urllib.parse import quote, quote_plus, urlparse, urlunparse

import requests


logger = logging.getLogger(__name__)

DEFAULT_MODEL = "MAG254"
DEFAULT_SERIAL_NUMBER = "0000000000000"
DEFAULT_DEVICE_ID = "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
DEFAULT_DEVICE_ID2 = "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
DEFAULT_SIGNATURE = "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
DEFAULT_TIMEZONE = "UTC"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 "
    "(KHTML, like Gecko) MAG200 stbapp ver: 4 rev: 2116 Mobile Safari/533.3"
)


class StalkerError(Exception):
    pass


class StalkerRecoverableError(StalkerError):
    pass


@dataclass
class StalkerConnectionResult:
    normalized_portal_url: str
    profile_name: str
    genre_count: int
    token: str
    used_authentication: bool


@dataclass
class StalkerGenreDiscoveryResult:
    normalized_portal_url: str
    profile_name: str
    genres: list
    token: str
    used_authentication: bool


@dataclass
class StalkerChannelDiscoveryResult:
    normalized_portal_url: str
    profile_name: str
    genres: list
    channels: list
    token: str
    used_authentication: bool


@dataclass
class StalkerVodDiscoveryResult:
    normalized_portal_url: str
    profile_name: str
    samples: dict
    token: str
    used_authentication: bool


@dataclass
class StalkerVodCategoryDiscoveryResult:
    normalized_portal_url: str
    profile_name: str
    movie_categories: list
    series_categories: list
    token: str
    used_authentication: bool


class StalkerClient:
    def __init__(
        self,
        server_url,
        mac,
        username="",
        password="",
        user_agent=None,
        custom_properties=None,
        timeout=15,
    ):
        self.server_url = server_url or ""
        self.mac = (mac or "").strip().upper()
        self.username = username or ""
        self.password = password or ""
        self.custom_properties = custom_properties or {}
        self.timeout = timeout
        self.token = self.custom_properties.get("token") or token_hex(16).upper()
        self.user_agent = user_agent or DEFAULT_USER_AGENT
        self.model = self.custom_properties.get("model") or DEFAULT_MODEL
        self.serial_number = self._configured_identity_value(
            "serial_number",
            DEFAULT_SERIAL_NUMBER,
        )
        self.device_id = self._configured_identity_value(
            "device_id",
            DEFAULT_DEVICE_ID,
        )
        self.device_id2 = self._configured_identity_value(
            "device_id2",
            DEFAULT_DEVICE_ID2,
        )
        self.signature = self._configured_identity_value(
            "signature",
            DEFAULT_SIGNATURE,
        )
        self.timezone = self.custom_properties.get("timezone") or DEFAULT_TIMEZONE

        self.session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=1,
            pool_maxsize=2,
            max_retries=1,
            pool_block=False,
        )
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    @classmethod
    def normalize_portal_candidates(cls, raw_url):
        if not raw_url or not str(raw_url).strip():
            raise StalkerError("Portal URL cannot be empty.")

        parsed = urlparse(str(raw_url).strip())
        if not parsed.scheme or not parsed.netloc:
            raise StalkerError("Portal URL must include protocol and host.")

        base_path = parsed.path or ""
        stripped_path = base_path.rstrip("/")
        base = parsed._replace(params="", query="", fragment="")
        candidates = []

        def add_candidate(path):
            normalized = urlunparse(base._replace(path=path))
            if normalized not in candidates:
                candidates.append(normalized)

        if stripped_path.endswith("/server/load.php") or stripped_path.endswith(
            "/portal.php"
        ):
            add_candidate(stripped_path or base_path)
            sibling_base = stripped_path.rsplit("/", 1)[0]
            add_candidate(f"{sibling_base}/server/load.php")
            add_candidate(f"{sibling_base}/portal.php")
            return candidates

        if stripped_path.endswith("/c"):
            parent = stripped_path[: -len("/c")] or ""
            add_candidate(f"{parent}/server/load.php")
            add_candidate(f"{parent}/portal.php")
            add_candidate(f"{stripped_path}/")
            return candidates

        if stripped_path.endswith("/stalker_portal"):
            add_candidate(f"{stripped_path}/server/load.php")
            add_candidate(f"{stripped_path}/portal.php")
            add_candidate(f"{stripped_path}/c/")
            return candidates

        clean_path = stripped_path
        if clean_path:
            add_candidate(f"{clean_path}/server/load.php")
            add_candidate(f"{clean_path}/portal.php")
            add_candidate(f"{clean_path}/c/")
        else:
            add_candidate("/stalker_portal/server/load.php")
            add_candidate("/stalker_portal/portal.php")
            add_candidate("/stalker_portal/c/")
            add_candidate("/server/load.php")
            add_candidate("/portal.php")

        return candidates

    def test_connection(self):
        result = self.discover_live_genres()
        return StalkerConnectionResult(
            normalized_portal_url=result.normalized_portal_url,
            profile_name=result.profile_name,
            genre_count=len(result.genres),
            token=result.token,
            used_authentication=result.used_authentication,
        )

    def discover_live_genres(self):
        errors = []
        for candidate in self.normalize_portal_candidates(self.server_url):
            try:
                return self._discover_candidate(candidate)
            except StalkerError as exc:
                errors.append(f"{candidate}: {exc}")
                logger.info("Stalker connection attempt failed for %s: %s", candidate, exc)

        detail = errors[-1] if errors else "No portal endpoints could be tested."
        raise StalkerError(detail)

    def discover_live_channels(self):
        errors = []
        for candidate in self.normalize_portal_candidates(self.server_url):
            try:
                return self._discover_channels_candidate(candidate)
            except StalkerError as exc:
                errors.append(f"{candidate}: {exc}")
                logger.info(
                    "Stalker channel discovery attempt failed for %s: %s",
                    candidate,
                    exc,
                )

        detail = errors[-1] if errors else "No portal endpoints could be tested."
        raise StalkerError(detail)

    def discover_vod_protocol(self):
        errors = []
        for candidate in self.normalize_portal_candidates(self.server_url):
            try:
                return self._discover_vod_candidate(candidate)
            except StalkerError as exc:
                errors.append(f"{candidate}: {exc}")
                logger.info(
                    "Stalker VOD discovery attempt failed for %s: %s",
                    candidate,
                    exc,
                )

        detail = errors[-1] if errors else "No portal endpoints could be tested."
        raise StalkerError(detail)

    def discover_vod_categories(self):
        errors = []
        for candidate in self.normalize_portal_candidates(self.server_url):
            try:
                return self._discover_vod_categories_candidate(candidate)
            except StalkerError as exc:
                errors.append(f"{candidate}: {exc}")
                logger.info(
                    "Stalker VOD category discovery attempt failed for %s: %s",
                    candidate,
                    exc,
                )

        detail = errors[-1] if errors else "No portal endpoints could be tested."
        raise StalkerError(detail)

    def _discover_candidate(self, portal_url):
        self.handshake(portal_url)
        used_authentication = False
        if self.username or self.password:
            self.authenticate(portal_url)
            used_authentication = True
        profile = self.get_profile(portal_url)
        genres = self.get_genres(portal_url)

        profile_name = (
            profile.get("name")
            or profile.get("fname")
            or profile.get("login")
            or self.username
            or self.mac
        )
        if not isinstance(genres, list):
            raise StalkerError("Portal returned an invalid genres response.")

        return StalkerGenreDiscoveryResult(
            normalized_portal_url=portal_url,
            profile_name=str(profile_name).strip() or self.mac,
            genres=genres,
            token=self.token,
            used_authentication=used_authentication,
        )

    def _discover_channels_candidate(self, portal_url):
        self.handshake(portal_url)
        used_authentication = False
        if self.username or self.password:
            self.authenticate(portal_url)
            used_authentication = True

        profile = self.get_profile(portal_url)
        genres = self.get_genres(portal_url)
        channels = self.get_all_channels(portal_url)

        profile_name = (
            profile.get("name")
            or profile.get("fname")
            or profile.get("login")
            or self.username
            or self.mac
        )
        if not isinstance(genres, list):
            raise StalkerError("Portal returned an invalid genres response.")
        if not isinstance(channels, list):
            raise StalkerError("Portal returned an invalid channels response.")

        genre_map = {}
        for genre in genres:
            genre_id = genre.get("id")
            if genre_id is None:
                continue
            genre_title = genre.get("title") or genre.get("name") or genre.get("alias") or ""
            genre_map[str(genre_id)] = str(genre_title).strip()

        normalized_channels = [
            self._normalize_channel(channel, portal_url, genre_map)
            for channel in channels
        ]

        return StalkerChannelDiscoveryResult(
            normalized_portal_url=portal_url,
            profile_name=str(profile_name).strip() or self.mac,
            genres=genres,
            channels=normalized_channels,
            token=self.token,
            used_authentication=used_authentication,
        )

    def _discover_vod_candidate(self, portal_url):
        self.handshake(portal_url)
        used_authentication = False
        if self.username or self.password:
            self.authenticate(portal_url)
            used_authentication = True
        elif self._should_use_device_id_auth():
            self.authenticate_with_device_ids(portal_url)
        profile = self.get_profile(portal_url)

        profile_name = (
            profile.get("name")
            or profile.get("fname")
            or profile.get("login")
            or self.username
            or self.mac
        )

        movie_categories = self.get_vod_categories(portal_url)
        series_categories = self.get_series_categories(portal_url)
        movie_list = self.get_vod_movies(
            portal_url,
            category_id=self._extract_first_vod_category_id(movie_categories),
        )
        series_list = self.get_vod_series(
            portal_url,
            category_id=self._extract_first_vod_category_id(series_categories),
        )

        movie_item = self._extract_first_dict(movie_list)
        series_item = self._extract_first_dict(series_list)

        series_detail = []
        if series_item:
            series_id = self._extract_vod_item_id(series_item)
            if series_id:
                series_detail = self.get_series_seasons(portal_url, series_id)

        season_item = self._extract_first_dict(series_detail)
        episode_list = []
        if series_item and season_item:
            series_id = self._extract_vod_item_id(series_item)
            season_id = self._extract_vod_item_id(season_item)
            if series_id and season_id:
                episode_list = self.get_series_episodes(
                    portal_url,
                    series_id=series_id,
                    season_id=season_id,
                )

        episode_item = self._extract_first_dict(episode_list)
        link_cmd = (
            self._extract_vod_cmd(movie_item)
            or self._extract_vod_cmd(episode_item)
            or self._extract_vod_cmd(series_item)
        )
        vod_link = self.create_vod_link(portal_url, link_cmd) if link_cmd else None

        samples = {
            "movie_categories": self._sample_list(movie_categories),
            "series_categories": self._sample_list(series_categories),
            "movie_list": self._sample_list(movie_list),
            "series_list": self._sample_list(series_list),
            "series_detail": self._sample_list(series_detail),
            "episodes": self._sample_list(episode_list),
            "vod_link": vod_link,
        }

        return StalkerVodDiscoveryResult(
            normalized_portal_url=portal_url,
            profile_name=str(profile_name).strip() or self.mac,
            samples=samples,
            token=self.token,
            used_authentication=used_authentication,
        )

    def _discover_vod_categories_candidate(self, portal_url):
        self.handshake(portal_url)
        used_authentication = False
        if self.username or self.password:
            self.authenticate(portal_url)
            used_authentication = True
        elif self._should_use_device_id_auth():
            self.authenticate_with_device_ids(portal_url)
        profile = self.get_profile(portal_url)

        profile_name = (
            profile.get("name")
            or profile.get("fname")
            or profile.get("login")
            or self.username
            or self.mac
        )

        movie_categories = self.get_vod_categories(portal_url)
        series_categories = self.get_series_categories(portal_url)

        if not isinstance(movie_categories, list):
            raise StalkerError("Portal returned an invalid movie categories response.")
        if not isinstance(series_categories, list):
            raise StalkerError("Portal returned an invalid series categories response.")

        return StalkerVodCategoryDiscoveryResult(
            normalized_portal_url=portal_url,
            profile_name=str(profile_name).strip() or self.mac,
            movie_categories=movie_categories,
            series_categories=series_categories,
            token=self.token,
            used_authentication=used_authentication,
        )

    def handshake(self, portal_url):
        headers = self._handshake_headers(portal_url)
        try:
            response = self.session.request(
                "GET",
                portal_url,
                headers=headers,
                params={
                    "type": "stb",
                    "action": "handshake",
                    "token": self.token,
                    "JsHttpRequest": "1-xml",
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise StalkerError(f"Request failed: {exc}") from exc

        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            body = response.text.strip()[:200]
            raise StalkerError(f"Invalid portal response: {body or 'empty body'}") from exc

        if not isinstance(payload, dict):
            raise StalkerError("Portal returned an unexpected response shape.")

        js = payload.get("js")
        if isinstance(js, dict) and js.get("token"):
            self.token = str(js["token"])
        elif not isinstance(js, dict):
            raise StalkerError("Handshake response was not recognized.")

    def authenticate(self, portal_url):
        data = {
            "type": "stb",
            "action": "do_auth",
            "login": self.username,
            "password": self.password,
            "JsHttpRequest": "1-xml",
        }
        if self.device_id:
            data["device_id"] = self.device_id
        if self.device_id2:
            data["device_id2"] = self.device_id2

        payload = self._request(
            "POST",
            portal_url,
            data=data,
            with_auth=True,
        )
        if payload.get("js") is not True:
            text = payload.get("text") or "Portal rejected the provided credentials."
            raise StalkerError(str(text))

    def authenticate_with_device_ids(self, portal_url):
        query = {
            "type": "stb",
            "action": "get_profile",
            "hd": "1",
            "stb_type": self.model,
            "auth_second_step": "1",
            "JsHttpRequest": "1-xml",
        }
        if self.serial_number:
            query["sn"] = self.serial_number
        if self.device_id:
            query["device_id"] = self.device_id
        if self.device_id2:
            query["device_id2"] = self.device_id2

        payload = self._request(
            "GET",
            portal_url,
            query=query,
            with_auth=True,
        )
        js = payload.get("js")
        if not isinstance(js, dict):
            raise StalkerError("Portal device authentication response was not recognized.")

        profile_id = js.get("id")
        if profile_id in (None, "", 0, "0"):
            text = payload.get("text") or "Portal rejected the provided device identity."
            if (
                text == "Portal rejected the provided device identity."
                and not any(
                    [
                        self.serial_number,
                        self.device_id,
                        self.device_id2,
                        self.signature,
                    ]
                )
            ):
                text = (
                    "Portal rejected the provided device identity. "
                    "This portal likely requires the real serial/device ID values "
                    "for the MAC address, or username/password authentication."
                )
            raise StalkerError(str(text))

        return js

    def get_profile(self, portal_url):
        query = {
            "type": "stb",
            "action": "get_profile",
            "hd": "1",
            "stb_type": self.model,
            "auth_second_step": "1",
            "JsHttpRequest": "1-xml",
        }
        if self.serial_number:
            query["sn"] = self.serial_number
        if self.device_id:
            query["device_id"] = self.device_id
        if self.device_id2:
            query["device_id2"] = self.device_id2
        if self.signature:
            query["signature"] = self.signature

        payload = self._request(
            "GET",
            portal_url,
            query=query,
            with_auth=True,
        )
        js = payload.get("js")
        if not isinstance(js, dict):
            raise StalkerError("Portal profile response was not recognized.")
        return js

    def get_genres(self, portal_url):
        payload = self._request(
            "GET",
            portal_url,
            query={
                "type": "itv",
                "action": "get_genres",
                "JsHttpRequest": "1-xml",
            },
            with_auth=True,
        )
        genres = payload.get("js")
        if genres is None:
            raise StalkerError("Portal did not return any live TV genres.")
        return genres

    def watchdog_update(self, portal_url):
        payload = self._request(
            "GET",
            portal_url,
            query={
                "action": "get_events",
                "event_active_id": "0",
                "init": "0",
                "type": "watchdog",
                "cur_play_type": "1",
                "JsHttpRequest": "1-xml",
            },
            with_auth=True,
        )
        return payload

    def get_all_channels(self, portal_url):
        payload = self._request(
            "GET",
            portal_url,
            query={
                "type": "itv",
                "action": "get_all_channels",
                "JsHttpRequest": "1-xml",
            },
            with_auth=True,
        )
        js = payload.get("js")
        if isinstance(js, dict):
            channels = js.get("data")
        elif isinstance(js, list):
            channels = js
        else:
            raise StalkerRecoverableError(
                self._payload_error_text(
                    payload,
                    "Portal channels response was not recognized.",
                )
            )

        if channels is None:
            raise StalkerRecoverableError(
                self._payload_error_text(
                    payload,
                    "Portal did not return any live channels.",
                )
            )
        if not isinstance(channels, list):
            raise StalkerRecoverableError(
                self._payload_error_text(
                    payload,
                    "Portal returned an invalid live channels response.",
                )
            )
        return channels

    def get_vod_categories(self, portal_url):
        return self._get_categories(portal_url, provider_type="vod")

    def get_series_categories(self, portal_url):
        return self._get_categories(portal_url, provider_type="series")

    def _get_categories(self, portal_url, provider_type):
        payload = self._request(
            "GET",
            portal_url,
            query={
                "type": provider_type,
                "action": "get_categories",
                "JsHttpRequest": "1-xml",
            },
            with_auth=True,
        )
        categories = payload.get("js")
        if not isinstance(categories, list):
            raise StalkerRecoverableError(
                self._payload_error_text(
                    payload,
                    "Portal VOD categories response was not recognized.",
                )
            )
        return categories

    def get_vod_movies(self, portal_url, category_id=None, page=1):
        return self._get_vod_ordered_list(
            portal_url,
            category_id=category_id,
            page=page,
            provider_type="vod",
        )

    def get_vod_series(self, portal_url, category_id=None, page=1):
        return self._get_vod_ordered_list(
            portal_url,
            category_id=category_id,
            page=page,
            provider_type="series",
        )

    def get_series_seasons(self, portal_url, series_id, page=1):
        return self._get_vod_ordered_list(
            portal_url,
            page=page,
            movie_id=series_id,
            season_id="0",
            episode_id="0",
            provider_type="series",
        )

    def get_series_episodes(self, portal_url, series_id, season_id, page=1):
        return self._get_vod_ordered_list(
            portal_url,
            page=page,
            movie_id=series_id,
            season_id=season_id,
            episode_id="0",
            provider_type="series",
        )

    def create_vod_link(self, portal_url, cmd):
        normalized_cmd = str(cmd or "").strip()
        if not normalized_cmd:
            raise StalkerError("Stalker VOD item is missing the source command.")

        encoded_cmd = quote(
            normalized_cmd,
            safe="!$&'()*+,;=:@-._~",
        )
        request_url = (
            f"{portal_url}?action=create_link&type=vod"
            f"&cmd={encoded_cmd}"
            f"&JsHttpRequest=1-xml"
        )
        payload = self._request("GET", request_url, with_auth=True)
        return self._extract_create_link_url(payload)

    def _get_vod_ordered_list(
        self,
        portal_url,
        category_id=None,
        page=1,
        movie_id=None,
        season_id=None,
        episode_id=None,
        provider_type="vod",
    ):
        query = {
            "type": provider_type,
            "action": "get_ordered_list",
            "JsHttpRequest": "1-xml",
            "p": page,
        }
        if category_id not in (None, ""):
            query["category"] = category_id
        if movie_id not in (None, ""):
            query["movie_id"] = movie_id
        if season_id not in (None, ""):
            query["season_id"] = season_id
        if episode_id not in (None, ""):
            query["episode_id"] = episode_id

        payload = self._request(
            "GET",
            portal_url,
            query=query,
            with_auth=True,
        )
        items = self._extract_ordered_list_items(payload)
        if not isinstance(items, list):
            raise StalkerRecoverableError(
                self._payload_error_text(
                    payload,
                    "Portal returned an invalid VOD ordered list response.",
                )
        )
        return items

    def prepare_authenticated_session(self, portal_url):
        self.handshake(portal_url)
        if self.username or self.password:
            self.authenticate(portal_url)
        elif self._should_use_device_id_auth():
            self.authenticate_with_device_ids(portal_url)

    def prepare_playback_session(self, portal_url):
        self.prepare_authenticated_session(portal_url)
        self.watchdog_update(portal_url)

    def _resolve_playback_url_once(self, portal_url, channel_metadata):
        self.prepare_playback_session(portal_url)
        fresh_cmd = self.get_fresh_channel_cmd(portal_url, channel_metadata)
        if not fresh_cmd:
            raise StalkerError("Stalker stream is missing a usable live command.")
        return self.create_link(portal_url, fresh_cmd)

    def _resolve_vod_playback_url_once(self, portal_url, cmd):
        self.prepare_playback_session(portal_url)
        return self.create_vod_link(portal_url, cmd)

    def _should_retry_playback_resolution(self, exc):
        if isinstance(exc, StalkerRecoverableError):
            return True

        message = str(exc).lower()
        retry_markers = (
            "empty playback link",
            "invalid playback link",
            "create_link response",
            "auth",
            "token",
            "session",
            "401",
            "403",
            "forbidden",
            "unauthorized",
            "access denied",
        )
        return any(marker in message for marker in retry_markers)

    def get_fresh_channel_cmd(self, portal_url, channel_metadata):
        target_channel_id = str(
            channel_metadata.get("stalker_channel_id")
            or channel_metadata.get("id")
            or ""
        ).strip()
        target_cmd_id = str(channel_metadata.get("cmd_id") or "").strip()
        target_cmd_ch_id = str(channel_metadata.get("cmd_ch_id") or "").strip()

        channels = self.get_all_channels(portal_url)
        for channel in channels:
            if not isinstance(channel, dict):
                continue

            channel_id = str(channel.get("id") or "").strip()
            if target_channel_id and channel_id == target_channel_id:
                fresh_cmd = str(channel.get("cmd") or "").strip()
                if fresh_cmd:
                    return fresh_cmd

            cmds = channel.get("cmds")
            if not isinstance(cmds, list):
                continue

            for cmd_entry in cmds:
                if not isinstance(cmd_entry, dict):
                    continue
                cmd_id = str(cmd_entry.get("id") or "").strip()
                cmd_ch_id = str(cmd_entry.get("ch_id") or "").strip()
                if (
                    target_cmd_id
                    and target_cmd_ch_id
                    and cmd_id == target_cmd_id
                    and cmd_ch_id == target_cmd_ch_id
                ):
                    fresh_cmd = str(channel.get("cmd") or "").strip()
                    if fresh_cmd:
                        return fresh_cmd

        return str(channel_metadata.get("cmd") or "").strip()

    def create_link(self, portal_url, cmd):
        normalized_cmd = str(cmd or "").strip()
        if not normalized_cmd:
            raise StalkerError("Stalker stream is missing the source command.")

        # Match stalkerhek's use of Go's url.PathEscape for the cmd payload.
        # That keeps reserved path-segment characters like ':' and '&' intact
        # while still escaping spaces, '/', and '?'.
        encoded_cmd = quote(
            normalized_cmd,
            safe="!$&'()*+,;=:@-._~",
        )
        request_url = (
            f"{portal_url}?action=create_link&type=itv"
            f"&cmd={encoded_cmd}"
            f"&JsHttpRequest=1-xml"
        )
        payload = self._request("GET", request_url, with_auth=True)
        resolved_url = self._extract_create_link_url(payload)
        if "stream=&" in resolved_url or resolved_url.endswith("stream="):
            logger.warning(
                "Stalker create_link returned an unusable playback URL for portal %s: %s; payload=%s",
                portal_url,
                resolved_url,
                payload,
            )
        return resolved_url

    def build_media_headers(self, media_url):
        """Build headers for fetching the resolved Stalker media URL."""
        return self._headers(media_url, with_auth=True)

    def resolve_playback_url(self, portal_url, channel_metadata):
        try:
            return self._resolve_playback_url_once(portal_url, channel_metadata)
        except StalkerError as exc:
            if not self._should_retry_playback_resolution(exc):
                raise

            logger.info(
                "Retrying Stalker playback URL resolution after session recovery for %s: %s",
                portal_url,
                exc,
            )
            return self._resolve_playback_url_once(portal_url, channel_metadata)

    def resolve_vod_playback_url(self, portal_url, cmd):
        try:
            return self._resolve_vod_playback_url_once(portal_url, cmd)
        except StalkerError as exc:
            if not self._should_retry_playback_resolution(exc):
                raise

            logger.info(
                "Retrying Stalker VOD playback URL resolution after session recovery for %s: %s",
                portal_url,
                exc,
            )
            return self._resolve_vod_playback_url_once(portal_url, cmd)

    def _normalize_channel(self, channel, portal_url, genre_map):
        if not isinstance(channel, dict):
            raise StalkerError("Portal returned an invalid channel item.")

        normalized = dict(channel)
        raw_genre_id = (
            channel.get("tv_genre_id")
            or channel.get("genre_id")
            or channel.get("category_id")
        )
        genre_id = str(raw_genre_id).strip() if raw_genre_id is not None else ""

        cmds = channel.get("cmds")
        if isinstance(cmds, list) and cmds:
            primary_cmd = cmds[0] if isinstance(cmds[0], dict) else {}
            normalized.setdefault("cmd_id", primary_cmd.get("id"))
            normalized.setdefault("cmd_ch_id", primary_cmd.get("ch_id"))

        normalized["genre_id"] = genre_id
        normalized["genre_name"] = genre_map.get(genre_id, "")

        logo = channel.get("logo") or channel.get("logo_link") or ""
        if logo:
            normalized["logo_url"] = self._logo_url(portal_url, str(logo).strip())

        return normalized

    def _logo_url(self, portal_url, logo_path):
        if not logo_path:
            return ""
        parsed_logo = urlparse(logo_path)
        if parsed_logo.scheme and parsed_logo.netloc:
            return logo_path

        parsed_portal = urlparse(portal_url)
        portal_path = parsed_portal.path or "/"
        if portal_path.endswith("/server/load.php"):
            base_path = portal_path[: -len("/server/load.php")] or "/"
        elif portal_path.endswith("/portal.php"):
            base_path = portal_path[: -len("/portal.php")] or "/"
        else:
            base_path = dirname(portal_path)
        normalized_path = join(base_path, "misc", "logos", "320", logo_path.lstrip("/"))
        return urlunparse(
            parsed_portal._replace(
                path=normalized_path,
                params="",
                query="",
                fragment="",
            )
        )

    def _extract_create_link_url(self, payload):
        js = payload.get("js")
        if not isinstance(js, dict):
            raise StalkerRecoverableError(
                self._payload_error_text(
                    payload,
                    "Portal create_link response was not recognized.",
                )
            )

        resolved_cmd = str(js.get("cmd") or "").strip()
        if not resolved_cmd:
            raise StalkerRecoverableError(
                self._payload_error_text(
                    payload,
                    "Portal returned an empty playback link.",
                )
            )

        parts = resolved_cmd.split()
        if not parts:
            raise StalkerRecoverableError("Portal returned an invalid playback link.")

        return parts[-1]

    def _extract_ordered_list_items(self, payload):
        js = payload.get("js")
        if isinstance(js, dict):
            return js.get("data")
        if isinstance(js, list):
            return js
        raise StalkerRecoverableError(
            self._payload_error_text(
                payload,
                "Portal ordered list response was not recognized.",
            )
        )

    def _extract_first_vod_category_id(self, categories):
        first_category = self._extract_first_dict(categories)
        if not first_category:
            return None
        for key in ("id", "category_id"):
            value = first_category.get(key)
            if value not in (None, ""):
                return value
        return None

    def _extract_vod_item_id(self, item):
        if not isinstance(item, dict):
            return None
        for key in ("id", "movie_id", "series_id", "season_id", "episode_id"):
            value = item.get(key)
            if value not in (None, ""):
                return value
        return None

    def _extract_vod_cmd(self, item):
        if not isinstance(item, dict):
            return ""
        for key in ("cmd", "stream_cmd", "play_cmd", "play_url"):
            value = item.get(key)
            if value:
                return str(value).strip()
        return ""

    def _extract_first_dict(self, items):
        if not isinstance(items, list):
            return None
        for item in items:
            if isinstance(item, dict):
                return item
        return None

    def _sample_list(self, items, limit=2):
        if not isinstance(items, list):
            return []
        return items[:limit]

    def _request(self, method, portal_url, query=None, data=None, with_auth=False):
        headers = self._headers(portal_url, with_auth=with_auth)
        request_kwargs = {
            "headers": headers,
            "timeout": self.timeout,
        }
        if query:
            request_kwargs["params"] = query
        if data:
            request_kwargs["data"] = data

        try:
            response = self.session.request(method, portal_url, **request_kwargs)
            response.raise_for_status()
        except requests.RequestException as exc:
            if with_auth and self._is_auth_status_error(exc):
                raise StalkerRecoverableError(f"Request failed: {exc}") from exc
            raise StalkerError(f"Request failed: {exc}") from exc

        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            body = response.text.strip()[:200]
            error_cls = StalkerRecoverableError if with_auth else StalkerError
            raise error_cls(
                f"Invalid portal response: {body or 'empty body'}"
            ) from exc

        if not isinstance(payload, dict):
            error_cls = StalkerRecoverableError if with_auth else StalkerError
            raise error_cls("Portal returned an unexpected response shape.")
        return payload

    def _payload_error_text(self, payload, default_message):
        text = str(payload.get("text") or "").strip()
        return text or default_message

    def _is_auth_status_error(self, exc):
        response = getattr(exc, "response", None)
        return bool(response is not None and response.status_code in (401, 403))

    def _configured_identity_value(self, key, placeholder):
        value = str(self.custom_properties.get(key) or "").strip()
        if not value or value == placeholder:
            return ""
        return value

    def _should_use_device_id_auth(self):
        return bool(self.device_id and self.device_id2)

    def _headers(self, portal_url, with_auth=False):
        parsed = urlparse(portal_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        cookie_parts = ["PHPSESSID=null"]
        if self.serial_number:
            cookie_parts.append(f"sn={quote_plus(self.serial_number)}")
        cookie_parts.extend(
            [
                f"mac={quote_plus(self.mac)}",
                "stb_lang=en",
                f"timezone={quote_plus(self.timezone)}",
            ]
        )
        headers = {
            "User-Agent": self.user_agent,
            "X-User-Agent": f"Model: {self.model}; Link: Ethernet",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Referer": f"{origin}/",
            "Origin": origin,
            # Match stalkerhek's authenticated request cookie formatting.
            "Cookie": "; ".join(cookie_parts) + ";",
        }
        if with_auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _handshake_headers(self, portal_url):
        parsed = urlparse(portal_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        cookie_parts = []
        if self.serial_number:
            cookie_parts.append(f"sn={self.serial_number}")
        cookie_parts.extend(
            [
                f"mac={self.mac}",
                "stb_lang=en",
                f"timezone={self.timezone}",
            ]
        )
        return {
            "User-Agent": self.user_agent,
            "X-User-Agent": f"Model: {self.model}; Link: Ethernet",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Referer": f"{origin}/",
            "Origin": origin,
            # Match stalkerhek's special-case handshake cookie formatting.
            "Cookie": "; ".join(cookie_parts),
        }
