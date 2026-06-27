import base64
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Dict, Optional

from apps.m3u.models import M3UAccount
from apps.m3u.stalker import StalkerAuthError, StalkerClient, StalkerError
from core.utils import RedisClient


logger = logging.getLogger(__name__)


@dataclass
class ResolvedVODStreamContext:
    url: Optional[str]
    user_agent: Optional[str] = None
    input_headers: Optional[Dict[str, str]] = None


STALKER_VOD_PLAYBACK_CACHE_TTL_SECONDS = 30
STALKER_VOD_AUTH_FAILURE_COOLDOWN_SECONDS = 15


def resolve_vod_stream_context(
    relation,
    *,
    force_refresh: bool = False,
) -> ResolvedVODStreamContext:
    """Resolve a playable upstream URL and request context for a VOD relation."""
    m3u_account = getattr(relation, "m3u_account", None)
    if not m3u_account:
        return ResolvedVODStreamContext(url=None)

    if m3u_account.account_type == M3UAccount.Types.XC:
        return ResolvedVODStreamContext(url=_build_xtream_vod_url(relation))

    if m3u_account.account_type != M3UAccount.Types.STALKER:
        return ResolvedVODStreamContext(url=None)

    if _get_relation_content_type(relation) not in {"movie", "episode"}:
        return ResolvedVODStreamContext(url=None)

    return _resolve_stalker_vod_stream_context(
        relation,
        force_refresh=force_refresh,
    )


def _build_xtream_vod_url(relation) -> Optional[str]:
    from core.xtream_codes import Client as XtreamCodesClient

    content_type = _get_relation_content_type(relation)
    if content_type not in {"movie", "episode"}:
        return None

    normalized_url = XtreamCodesClient(
        relation.m3u_account.server_url,
        "",
        "",
    )._normalize_url(relation.m3u_account.server_url)
    username = relation.m3u_account.username
    password = relation.m3u_account.password
    stream_id = getattr(relation, "stream_id", None)
    container_extension = getattr(relation, "container_extension", None) or "mp4"
    path_type = "movie" if content_type == "movie" else "series"
    return (
        f"{normalized_url}/{path_type}/"
        f"{username}/{password}/{stream_id}.{container_extension}"
    )


def _resolve_stalker_vod_stream_context(
    relation,
    *,
    force_refresh: bool = False,
) -> ResolvedVODStreamContext:
    m3u_account = relation.m3u_account
    account_properties = dict(m3u_account.custom_properties or {})
    relation_properties = dict(relation.custom_properties or {})
    cmd = _extract_stalker_vod_cmd(relation_properties)
    if not cmd:
        raise StalkerError(
            "Stalker VOD item is missing portal metadata required for playback."
        )

    series_number = _get_stalker_episode_series_selector(
        relation,
        relation_properties,
        cmd,
    )
    if not force_refresh:
        cached_context = _load_cached_stalker_vod_stream_context(
            relation,
            cmd,
            series_number,
        )
        if cached_context is not None:
            return cached_context

    client = StalkerClient(
        server_url=m3u_account.server_url,
        mac=account_properties.get("mac", ""),
        username=m3u_account.username or "",
        password=m3u_account.password or "",
        custom_properties=account_properties,
    )

    portal_url = _get_stalker_vod_portal_url(
        relation=relation,
        client=client,
        account_properties=account_properties,
    )
    _ensure_stalker_vod_auth_cooldown_allows_request(relation, portal_url)
    try:
        resolved_url = client.resolve_vod_playback_url(
            portal_url,
            cmd,
            series=series_number,
        )
    except StalkerAuthError as exc:
        _mark_stalker_vod_auth_failure_cooldown(relation, portal_url, exc)
        raise
    input_headers = client.build_media_headers(resolved_url)

    _persist_stalker_runtime_state(
        m3u_account,
        account_properties,
        client,
        portal_url=portal_url,
    )

    stream_context = ResolvedVODStreamContext(
        url=resolved_url,
        user_agent=input_headers.get("User-Agent") or client.user_agent,
        input_headers=input_headers,
    )
    _store_cached_stalker_vod_stream_context(
        relation,
        cmd,
        series_number,
        stream_context,
    )
    return stream_context


def _get_stalker_vod_cache_key(relation, cmd, series_number) -> str:
    account_id = getattr(getattr(relation, "m3u_account", None), "id", "unknown")
    stream_id = getattr(relation, "stream_id", "") or ""
    relation_type = _get_relation_content_type(relation) or "unknown"
    cmd_hash = hashlib.sha1(str(cmd).encode("utf-8")).hexdigest()[:16]
    series_part = str(series_number) if series_number is not None else "none"
    return (
        "stalker_vod_playback:"
        f"{account_id}:{relation_type}:{stream_id}:{series_part}:{cmd_hash}"
    )


def _get_stalker_vod_auth_failure_cooldown_key(relation, portal_url) -> str:
    account_id = getattr(getattr(relation, "m3u_account", None), "id", "unknown")
    portal_hash = hashlib.sha1(str(portal_url).encode("utf-8")).hexdigest()[:16]
    return f"stalker_vod_auth_failure:{account_id}:{portal_hash}"


def _ensure_stalker_vod_auth_cooldown_allows_request(relation, portal_url) -> None:
    redis_client = RedisClient.get_client()
    if redis_client is None:
        return

    cooldown_key = _get_stalker_vod_auth_failure_cooldown_key(relation, portal_url)
    try:
        cooldown_state = redis_client.get(cooldown_key)
    except Exception:
        logger.debug(
            "Failed to read Stalker VOD auth failure cooldown for account=%s key=%s",
            getattr(getattr(relation, "m3u_account", None), "id", None),
            cooldown_key,
            exc_info=True,
        )
        return

    if not cooldown_state:
        return

    logger.warning(
        "Skipping Stalker VOD playback resolution during auth failure cooldown for account=%s stream_id=%s key=%s",
        getattr(getattr(relation, "m3u_account", None), "id", None),
        getattr(relation, "stream_id", None),
        cooldown_key,
    )
    raise StalkerError("Recent Stalker authentication failure cooldown is active.")


def _mark_stalker_vod_auth_failure_cooldown(relation, portal_url, exc) -> None:
    redis_client = RedisClient.get_client()
    if redis_client is None:
        return

    cooldown_key = _get_stalker_vod_auth_failure_cooldown_key(relation, portal_url)
    try:
        redis_client.set(
            cooldown_key,
            str(exc),
            ex=STALKER_VOD_AUTH_FAILURE_COOLDOWN_SECONDS,
        )
        logger.warning(
            "Stored Stalker VOD auth failure cooldown for account=%s stream_id=%s key=%s ttl=%ss reason=%s",
            getattr(getattr(relation, "m3u_account", None), "id", None),
            getattr(relation, "stream_id", None),
            cooldown_key,
            STALKER_VOD_AUTH_FAILURE_COOLDOWN_SECONDS,
            exc,
        )
    except Exception:
        logger.debug(
            "Failed to store Stalker VOD auth failure cooldown for account=%s key=%s",
            getattr(getattr(relation, "m3u_account", None), "id", None),
            cooldown_key,
            exc_info=True,
        )


def _load_cached_stalker_vod_stream_context(
    relation,
    cmd,
    series_number,
) -> Optional[ResolvedVODStreamContext]:
    redis_client = RedisClient.get_client()
    if redis_client is None:
        return None

    cache_key = _get_stalker_vod_cache_key(relation, cmd, series_number)
    try:
        raw_payload = redis_client.get(cache_key)
    except Exception:
        logger.debug(
            "Failed to read Stalker VOD playback cache for account=%s stream_id=%s key=%s",
            getattr(getattr(relation, "m3u_account", None), "id", None),
            getattr(relation, "stream_id", None),
            cache_key,
            exc_info=True,
        )
        return None

    if not raw_payload:
        logger.info(
            "Stalker VOD playback cache miss for account=%s stream_id=%s key=%s",
            getattr(getattr(relation, "m3u_account", None), "id", None),
            getattr(relation, "stream_id", None),
            cache_key,
        )
        return None

    if isinstance(raw_payload, bytes):
        raw_payload = raw_payload.decode("utf-8", errors="ignore")

    try:
        payload = json.loads(raw_payload)
    except (TypeError, ValueError):
        return None

    url = payload.get("url")
    if not url:
        return None

    input_headers = payload.get("input_headers")
    if not isinstance(input_headers, dict):
        input_headers = None

    logger.info(
        "Stalker VOD playback cache hit for account=%s stream_id=%s key=%s",
        getattr(getattr(relation, "m3u_account", None), "id", None),
        getattr(relation, "stream_id", None),
        cache_key,
    )
    return ResolvedVODStreamContext(
        url=url,
        user_agent=payload.get("user_agent"),
        input_headers=input_headers,
    )


def _store_cached_stalker_vod_stream_context(
    relation,
    cmd,
    series_number,
    stream_context: ResolvedVODStreamContext,
) -> None:
    redis_client = RedisClient.get_client()
    if redis_client is None or not stream_context.url:
        return

    cache_key = _get_stalker_vod_cache_key(relation, cmd, series_number)
    payload = json.dumps(
        {
            "url": stream_context.url,
            "user_agent": stream_context.user_agent,
            "input_headers": stream_context.input_headers or {},
        }
    )
    try:
        redis_client.set(
            cache_key,
            payload,
            ex=STALKER_VOD_PLAYBACK_CACHE_TTL_SECONDS,
        )
        logger.info(
            "Stored Stalker VOD playback cache entry for account=%s stream_id=%s key=%s ttl=%ss",
            getattr(getattr(relation, "m3u_account", None), "id", None),
            getattr(relation, "stream_id", None),
            cache_key,
            STALKER_VOD_PLAYBACK_CACHE_TTL_SECONDS,
        )
    except Exception:
        return


def _get_stalker_vod_portal_url(relation, client, account_properties) -> str:
    relation_properties = dict(relation.custom_properties or {})
    basic_data = relation_properties.get("basic_data")
    if not isinstance(basic_data, dict):
        basic_data = {}
    info_data = relation_properties.get("info")
    if not isinstance(info_data, dict):
        info_data = {}

    portal_url = (
        str(account_properties.get("stalker_vod_portal_url") or "").strip()
        or str(account_properties.get("stalker_portal_url") or "").strip()
        or str(relation_properties.get("portal_url") or "").strip()
        or str(basic_data.get("portal_url") or "").strip()
        or str(info_data.get("portal_url") or "").strip()
    )
    if portal_url:
        return portal_url

    server_url = str(getattr(getattr(relation, "m3u_account", None), "server_url", "") or "").strip()
    if server_url.rstrip("/").endswith(("/server/load.php", "/portal.php")):
        return server_url

    discovery = client.discover_vod_categories()
    return discovery.normalized_portal_url


def _persist_stalker_runtime_state(
    m3u_account,
    existing_properties,
    client,
    portal_url=None,
):
    updated_properties = dict(existing_properties or {})
    changed = False

    normalized_portal_url = str(portal_url or "").strip()
    if (
        normalized_portal_url
        and updated_properties.get("stalker_vod_portal_url") != normalized_portal_url
    ):
        updated_properties["stalker_vod_portal_url"] = normalized_portal_url
        changed = True

    if client.token and updated_properties.get("token") != client.token:
        updated_properties["token"] = client.token
        changed = True

    if (
        client.last_auth_mode
        and updated_properties.get("stalker_auth_mode") != client.last_auth_mode
    ):
        updated_properties["stalker_auth_mode"] = client.last_auth_mode
        changed = True

    if changed:
        m3u_account.custom_properties = updated_properties
        m3u_account.save(update_fields=["custom_properties"])


def _extract_stalker_vod_cmd(relation_properties) -> str:
    payloads = [relation_properties]

    basic_data = relation_properties.get("basic_data")
    if isinstance(basic_data, dict):
        payloads.append(basic_data)

    info_data = relation_properties.get("info")
    if isinstance(info_data, dict):
        payloads.append(info_data)

    for payload in payloads:
        for key in ("cmd", "stream_cmd", "play_cmd", "play_url"):
            value = payload.get(key)
            if value not in (None, ""):
                text = str(value).strip()
                if text:
                    return text

    return ""


def _get_stalker_episode_series_selector(relation, relation_properties, cmd) -> Optional[int]:
    if _get_relation_content_type(relation) != "episode":
        return None

    info_data = relation_properties.get("info")
    if not isinstance(info_data, dict):
        info_data = {}

    should_use_series_selector = bool(
        info_data.get("_stalker_placeholder_episode")
        or _is_stalker_series_cmd(cmd)
    )
    if not should_use_series_selector:
        return None

    for payload in (relation_properties, info_data):
        value = _extract_int_candidate(
            payload.get("episode_num"),
            payload.get("episode_number"),
            payload.get("series_number"),
        )
        if value is not None:
            return value

    episode = getattr(relation, "episode", None)
    value = _extract_int_candidate(getattr(episode, "episode_number", None))
    if value is not None:
        return value

    return None


def _is_stalker_series_cmd(cmd) -> bool:
    text = str(cmd or "").strip()
    if not text:
        return False

    normalized = text
    missing_padding = len(normalized) % 4
    if missing_padding:
        normalized += "=" * (4 - missing_padding)

    try:
        decoded = base64.b64decode(normalized, validate=False).decode("utf-8")
        payload = json.loads(decoded)
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        return False

    return isinstance(payload, dict) and str(payload.get("type") or "").strip().lower() == "series"


def _extract_int_candidate(*values) -> Optional[int]:
    for value in values:
        if value in (None, ""):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _get_relation_content_type(relation) -> str:
    if hasattr(relation, "movie_id"):
        return "movie"
    if hasattr(relation, "episode_id"):
        return "episode"
    return ""
