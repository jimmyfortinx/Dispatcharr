"""Keep the shared VPN exit IP usable for every provider.

Dispatcharr can share a gluetun VPN with its sidecars, so every provider sees
the same exit IP. VPN exit IPs get blocked by providers because of other VPN
users. This guard probes each active provider through the VPN. When one of
them rejects the exit IP and nothing is playing, it asks gluetun for a new
server and probes again. It runs as ``python manage.py vpn_guard``.
"""

import json
import logging
import os
import random
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional
from urllib.parse import urljoin

import requests

logger = logging.getLogger(__name__)

PROBE_READ_BYTES = 64 * 1024
PROBE_DEADLINE_SECONDS = 20
PROBE_STREAMS_PER_ACCOUNT = 2
HLS_MAX_HOPS = 2
BAD_IP_KEY_PREFIX = "vpn_guard:bad_ip:"
ACTIVE_CONNECTION_PATTERNS = (
    "live:channel:*:clients:*",
    "timeshift:channel:*:clients:*",
    "vod_persistent_connection:*",
)

OK = "ok"
BLOCKED = "blocked"
ERROR = "error"


@dataclass
class GuardConfig:
    gluetun_url: str = "http://127.0.0.1:8000"
    gluetun_api_key: str = ""
    interval_seconds: int = 1800
    backoff_seconds: int = 3 * 3600
    startup_delay_seconds: int = 120
    max_rotations: int = 5
    rotation_timeout_seconds: int = 90
    bad_ip_ttl_seconds: int = 24 * 3600
    block_statuses: frozenset = field(default_factory=lambda: frozenset({456}))

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        defaults = cls()

        def as_int(name, default):
            raw = (env.get(name) or "").strip()
            return int(raw) if raw else default

        statuses_raw = (env.get("VPN_GUARD_BLOCK_STATUSES") or "").strip()
        block_statuses = (
            frozenset(int(s) for s in statuses_raw.split(",") if s.strip())
            if statuses_raw
            else defaults.block_statuses
        )
        return cls(
            gluetun_url=(env.get("VPN_GUARD_GLUETUN_URL") or defaults.gluetun_url).rstrip("/"),
            gluetun_api_key=_api_key_from_env(env),
            interval_seconds=as_int("VPN_GUARD_INTERVAL_SECONDS", defaults.interval_seconds),
            backoff_seconds=as_int("VPN_GUARD_BACKOFF_SECONDS", defaults.backoff_seconds),
            startup_delay_seconds=as_int(
                "VPN_GUARD_STARTUP_DELAY_SECONDS", defaults.startup_delay_seconds
            ),
            max_rotations=as_int("VPN_GUARD_MAX_ROTATIONS", defaults.max_rotations),
            rotation_timeout_seconds=as_int(
                "VPN_GUARD_ROTATION_TIMEOUT_SECONDS", defaults.rotation_timeout_seconds
            ),
            bad_ip_ttl_seconds=as_int("VPN_GUARD_BAD_IP_TTL_SECONDS", defaults.bad_ip_ttl_seconds),
            block_statuses=block_statuses,
        )


def _api_key_from_env(env):
    """Read the gluetun API key directly, or from gluetun's own role JSON.

    Reusing ``HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE`` lets the deployment point
    both containers at the same secret key.
    """
    api_key = (env.get("VPN_GUARD_GLUETUN_API_KEY") or "").strip()
    if api_key:
        return api_key
    role_raw = (env.get("HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE") or "").strip()
    if not role_raw:
        return ""
    try:
        role = json.loads(role_raw)
    except ValueError:
        logger.warning("HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE is not valid JSON")
        return ""
    return str(role.get("apikey") or "").strip() if isinstance(role, dict) else ""


class GluetunError(Exception):
    pass


class GluetunClient:
    """Minimal client for gluetun's HTTP control server."""

    def __init__(self, base_url, api_key="", timeout=10, sleep=time.sleep, clock=time.monotonic):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        if api_key:
            self.session.headers["X-API-Key"] = api_key
        self._sleep = sleep
        self._clock = clock

    def public_ip(self):
        response = self.session.get(f"{self.base_url}/v1/publicip/ip", timeout=self.timeout)
        response.raise_for_status()
        return str((response.json() or {}).get("public_ip") or "").strip()

    def vpn_status(self):
        response = self.session.get(f"{self.base_url}/v1/vpn/status", timeout=self.timeout)
        response.raise_for_status()
        return str((response.json() or {}).get("status") or "").strip()

    def _set_vpn_status(self, status):
        response = self.session.put(
            f"{self.base_url}/v1/vpn/status",
            json={"status": status},
            timeout=self.timeout,
        )
        response.raise_for_status()

    def ensure_running(self, attempts=3):
        """Start the VPN again if a previous rotation left it stopped."""
        for attempt in range(1, attempts + 1):
            try:
                if self.vpn_status() == "running":
                    return
                logger.warning("vpn_guard: gluetun VPN is not running, starting it")
                self._set_vpn_status("running")
                return
            except requests.RequestException:
                if attempt == attempts:
                    raise
                self._sleep(3)

    def rotate(self, previous_ip, timeout_seconds=90):
        """Restart the VPN so gluetun picks a new random server.

        Returns the new public IP. gluetun can report the old IP for a moment
        after the restart, so a different IP is awaited until the deadline. It
        can still equal ``previous_ip`` when gluetun picks the same server.
        """
        self._set_vpn_status("stopped")
        try:
            self._set_vpn_status("running")
        except requests.RequestException:
            # Never leave the shared VPN stopped.
            self.ensure_running()
        deadline = self._clock() + timeout_seconds
        last_ip = ""
        while self._clock() < deadline:
            self._sleep(3)
            try:
                last_ip = self.public_ip() or last_ip
            except requests.RequestException:
                continue
            if last_ip and last_ip != previous_ip:
                return last_ip
        if last_ip:
            return last_ip
        raise GluetunError(f"gluetun did not report a public IP within {timeout_seconds}s")


@dataclass
class ProbeResult:
    account_name: str
    outcome: str
    detail: str = ""


def _blocked_status_in_chain(exc, block_statuses):
    """Return a block status carried by an exception or the errors it wraps."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        if status in block_statuses:
            return status
        exc = exc.__cause__ or exc.__context__
    return None


def _hls_next_url(body, base_url):
    """First variant or segment URL of an HLS playlist, if the body is one."""
    text = body.decode("utf-8", errors="replace")
    if not text.lstrip().startswith("#EXTM3U"):
        return None
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return urljoin(base_url, line)
    return ""


def _read_stream(url, headers, block_statuses, clock=time.monotonic):
    """Fetch the start of a stream and classify the answer.

    HLS playlists are followed to a media segment, because a provider can
    serve the playlist and block the segments.
    """
    deadline = clock() + PROBE_DEADLINE_SECONDS
    for _hop in range(HLS_MAX_HOPS + 1):
        try:
            response = requests.get(url, headers=headers, stream=True, timeout=(5, 15))
        except requests.RequestException as exc:
            return ERROR, f"request failed: {exc.__class__.__name__}"
        try:
            if response.status_code in block_statuses:
                return BLOCKED, f"HTTP {response.status_code}"
            if response.status_code != 200:
                return ERROR, f"HTTP {response.status_code}"
            content_type = (response.headers.get("Content-Type") or "").lower()
            if "text/html" in content_type:
                return ERROR, "HTTP 200 with an HTML page"
            body = b""
            for chunk in response.iter_content(8192):
                body += chunk
                if len(body) >= PROBE_READ_BYTES or clock() >= deadline:
                    break
        except requests.RequestException as exc:
            return ERROR, f"read failed: {exc.__class__.__name__}"
        finally:
            response.close()

        if not body:
            return ERROR, "HTTP 200 without data"
        next_url = _hls_next_url(body, response.url or url)
        if next_url is None:
            return OK, f"HTTP 200, {len(body)} bytes"
        if not next_url:
            return ERROR, "HLS playlist without media"
        url = next_url
    return ERROR, "HLS playlist nesting too deep"


def _playback_profile(account):
    """The profile live playback would pick: active, default first."""
    return account.profiles.filter(is_active=True).order_by("-is_default", "id").first()


def _resolve_probe_request(stream, profile):
    """Build the upstream URL and headers the live proxy would use."""
    from apps.m3u.models import M3UAccount
    from apps.proxy.live_proxy.url_utils import (
        _resolve_live_stream_context,
        _resolve_live_stream_url,
        transform_url,
    )

    account = stream.m3u_account
    context = _resolve_live_stream_context(stream)
    if account.account_type == M3UAccount.Types.STALKER:
        url = transform_url(context["url"], profile.search_pattern, profile.replace_pattern)
    else:
        url = _resolve_live_stream_url(stream, account, profile)
    if not url:
        raise ValueError("profile URL transform did not match")
    headers = dict(context.get("input_headers") or {})
    if context.get("user_agent") and "User-Agent" not in headers:
        headers["User-Agent"] = context["user_agent"]
    return url, headers


def _probe_streams(account):
    from apps.channels.models import Stream

    streams = Stream.objects.filter(m3u_account=account)
    ids = list(streams.filter(channels__isnull=False).values_list("id", flat=True).distinct())
    if not ids:
        ids = list(streams.values_list("id", flat=True)[:500])
    picked = random.sample(ids, min(PROBE_STREAMS_PER_ACCOUNT, len(ids)))
    by_id = Stream.objects.select_related("m3u_account__user_agent").in_bulk(picked)
    return [by_id[i] for i in picked if i in by_id]


def probe_account(account, block_statuses):
    """Probe a few streams of one account. One readable stream is enough."""
    profile = _playback_profile(account)
    if profile is None:
        return ProbeResult(account.name, ERROR, "no active profile")
    streams = _probe_streams(account)
    if not streams:
        return ProbeResult(account.name, ERROR, "no streams")

    details = []
    blocked = False
    for stream in streams:
        try:
            url, headers = _resolve_probe_request(stream, profile)
        except Exception as exc:  # provider-specific resolution errors
            status = _blocked_status_in_chain(exc, block_statuses)
            if status is not None:
                details.append(f"stream {stream.id}: portal HTTP {status}")
                blocked = True
            else:
                details.append(f"stream {stream.id}: resolve failed: {exc}")
            continue
        outcome, detail = _read_stream(url, headers, block_statuses)
        details.append(f"stream {stream.id}: {detail}")
        if outcome == OK:
            return ProbeResult(account.name, OK, "; ".join(details))
        blocked = blocked or outcome == BLOCKED
    return ProbeResult(account.name, BLOCKED if blocked else ERROR, "; ".join(details))


def active_accounts():
    from apps.m3u.models import M3UAccount

    return list(M3UAccount.objects.filter(is_active=True).order_by("id"))


def active_connection_count(redis_client):
    """Count live, timeshift and VOD clients.

    Unlike the proxy's own helper, a Redis error propagates: an unknown
    viewer state must stop the cycle, not look idle.
    """
    if redis_client is None:
        raise RuntimeError("Redis is unavailable, viewer state is unknown")
    count = 0
    for pattern in ACTIVE_CONNECTION_PATTERNS:
        for _key in redis_client.scan_iter(match=pattern, count=1000):
            count += 1
    return count


class VpnGuard:
    def __init__(
        self,
        config: GuardConfig,
        gluetun: GluetunClient,
        redis_factory: Callable[[], object],
        probe: Optional[Callable[[], Iterable[ProbeResult]]] = None,
        connection_count: Optional[Callable[[object], int]] = None,
    ):
        self.config = config
        self.gluetun = gluetun
        self._redis_factory = redis_factory
        self._probe = probe or self._probe_all
        self._connection_count = connection_count or active_connection_count
        self.redis = None

    def _probe_all(self):
        return [probe_account(a, self.config.block_statuses) for a in active_accounts()]

    def _busy(self):
        return self._connection_count(self.redis)

    def _mark_bad(self, ip):
        if ip:
            self.redis.set(f"{BAD_IP_KEY_PREFIX}{ip}", "1", ex=self.config.bad_ip_ttl_seconds)

    def _is_bad(self, ip):
        return bool(ip and self.redis.exists(f"{BAD_IP_KEY_PREFIX}{ip}"))

    def _log(self, results, ip):
        for result in results:
            log = logger.warning if result.outcome == BLOCKED else logger.info
            log("vpn_guard ip=%s account=%s outcome=%s %s", ip, result.account_name, result.outcome, result.detail)

    def run_cycle(self, rotate=True):
        """Probe once and rotate if needed. Returns seconds to wait before the next cycle."""
        self.redis = self._redis_factory()
        if self.redis is None:
            raise RuntimeError("Redis is unavailable, viewer state is unknown")
        if rotate:
            self.gluetun.ensure_running()

        busy = self._busy()
        if busy:
            logger.info("vpn_guard: %s active connection(s), skipping probe", busy)
            return self.config.interval_seconds

        ip = self.gluetun.public_ip()
        results = list(self._probe())
        self._log(results, ip)
        blocked = {r.account_name for r in results if r.outcome == BLOCKED}
        if not blocked:
            logger.info("vpn_guard: no provider blocks exit IP %s", ip)
            return self.config.interval_seconds
        if not rotate:
            return self.config.interval_seconds

        # A new IP must make the blocked accounts play and keep the working ones.
        required = blocked | {r.account_name for r in results if r.outcome == OK}
        self._mark_bad(ip)
        for attempt in range(1, self.config.max_rotations + 1):
            busy = self._busy()
            if busy:
                logger.info("vpn_guard: %s active connection(s), stopping rotation", busy)
                return self.config.interval_seconds

            ip = self.gluetun.rotate(ip, self.config.rotation_timeout_seconds)
            logger.info("vpn_guard: rotation %s/%s, new exit IP %s", attempt, self.config.max_rotations, ip)
            if self._is_bad(ip):
                logger.info("vpn_guard: %s was blocked recently, rotating again", ip)
                continue

            results = list(self._probe())
            self._log(results, ip)
            failing = required - {r.account_name for r in results if r.outcome == OK}
            if not failing:
                logger.info("vpn_guard: exit IP %s works for every provider", ip)
                return self.config.interval_seconds
            if any(r.outcome == BLOCKED for r in results):
                self._mark_bad(ip)
            logger.info("vpn_guard: still failing on %s: %s", ip, ", ".join(sorted(failing)))

        logger.warning(
            "vpn_guard: still failing after %s rotations; waiting %ss. "
            "A provider may be refusing the account itself, not the IP.",
            self.config.max_rotations,
            self.config.backoff_seconds,
        )
        return self.config.backoff_seconds
