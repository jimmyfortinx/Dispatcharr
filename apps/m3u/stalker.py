import json
import logging
from dataclasses import dataclass
from secrets import token_hex
from urllib.parse import urlparse, urlunparse

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
        self.serial_number = (
            self.custom_properties.get("serial_number") or DEFAULT_SERIAL_NUMBER
        )
        self.device_id = self.custom_properties.get("device_id") or DEFAULT_DEVICE_ID
        self.device_id2 = self.custom_properties.get("device_id2") or DEFAULT_DEVICE_ID2
        self.signature = self.custom_properties.get("signature") or DEFAULT_SIGNATURE
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

    def handshake(self, portal_url):
        payload = self._request(
            "GET",
            portal_url,
            query={
                "type": "stb",
                "action": "handshake",
                "token": self.token,
                "JsHttpRequest": "1-xml",
            },
        )
        js = payload.get("js")
        if isinstance(js, dict) and js.get("token"):
            self.token = str(js["token"])
        elif not isinstance(js, dict):
            raise StalkerError("Handshake response was not recognized.")

    def authenticate(self, portal_url):
        payload = self._request(
            "POST",
            portal_url,
            data={
                "type": "stb",
                "action": "do_auth",
                "login": self.username,
                "password": self.password,
                "device_id": self.device_id,
                "device_id2": self.device_id2,
                "JsHttpRequest": "1-xml",
            },
            with_auth=True,
        )
        if payload.get("js") is not True:
            text = payload.get("text") or "Portal rejected the provided credentials."
            raise StalkerError(str(text))

    def get_profile(self, portal_url):
        payload = self._request(
            "GET",
            portal_url,
            query={
                "type": "stb",
                "action": "get_profile",
                "hd": "1",
                "sn": self.serial_number,
                "stb_type": self.model,
                "device_id": self.device_id,
                "device_id2": self.device_id2,
                "signature": self.signature,
                "auth_second_step": "1",
                "JsHttpRequest": "1-xml",
            },
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
            raise StalkerError(f"Request failed: {exc}") from exc

        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            body = response.text.strip()[:200]
            raise StalkerError(f"Invalid portal response: {body or 'empty body'}") from exc

        if not isinstance(payload, dict):
            raise StalkerError("Portal returned an unexpected response shape.")
        return payload

    def _headers(self, portal_url, with_auth=False):
        parsed = urlparse(portal_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        cookie_parts = [
            "PHPSESSID=null",
            f"sn={self.serial_number}",
            f"mac={self.mac}",
            "stb_lang=en",
            f"timezone={self.timezone}",
        ]
        headers = {
            "User-Agent": self.user_agent,
            "X-User-Agent": f"Model: {self.model}; Link: Ethernet",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Referer": f"{origin}/",
            "Origin": origin,
            "Cookie": "; ".join(cookie_parts),
        }
        if with_auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers
