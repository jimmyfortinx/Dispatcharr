from dataclasses import dataclass
from typing import Dict, Optional

from apps.m3u.models import M3UAccount
from apps.m3u.stalker import StalkerClient, StalkerError


@dataclass
class ResolvedVODStreamContext:
    url: Optional[str]
    user_agent: Optional[str] = None
    input_headers: Optional[Dict[str, str]] = None


def resolve_vod_stream_context(relation) -> ResolvedVODStreamContext:
    """Resolve a playable upstream URL and request context for a VOD relation."""
    m3u_account = getattr(relation, "m3u_account", None)
    if not m3u_account:
        return ResolvedVODStreamContext(url=None)

    if m3u_account.account_type == M3UAccount.Types.XC:
        return ResolvedVODStreamContext(url=_build_xtream_vod_url(relation))

    if m3u_account.account_type != M3UAccount.Types.STALKER:
        return ResolvedVODStreamContext(url=None)

    if not _is_movie_relation(relation):
        return ResolvedVODStreamContext(url=None)

    return _resolve_stalker_movie_stream_context(relation)


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


def _resolve_stalker_movie_stream_context(relation) -> ResolvedVODStreamContext:
    m3u_account = relation.m3u_account
    account_properties = dict(m3u_account.custom_properties or {})
    relation_properties = dict(relation.custom_properties or {})
    cmd = _extract_stalker_vod_cmd(relation_properties)
    if not cmd:
        raise StalkerError(
            "Stalker movie is missing portal metadata required for playback."
        )

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
    resolved_url = client.resolve_vod_playback_url(portal_url, cmd)
    input_headers = client.build_media_headers(resolved_url)

    _persist_stalker_runtime_state(
        m3u_account,
        account_properties,
        client,
        portal_url=portal_url,
    )

    return ResolvedVODStreamContext(
        url=resolved_url,
        user_agent=input_headers.get("User-Agent") or client.user_agent,
        input_headers=input_headers,
    )


def _get_stalker_vod_portal_url(relation, client, account_properties) -> str:
    relation_properties = dict(relation.custom_properties or {})
    basic_data = relation_properties.get("basic_data")
    if not isinstance(basic_data, dict):
        basic_data = {}

    portal_url = (
        str(account_properties.get("stalker_vod_portal_url") or "").strip()
        or str(relation_properties.get("portal_url") or "").strip()
        or str(basic_data.get("portal_url") or "").strip()
    )
    if portal_url:
        return portal_url

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

    if changed:
        m3u_account.custom_properties = updated_properties
        m3u_account.save(update_fields=["custom_properties"])


def _extract_stalker_vod_cmd(relation_properties) -> str:
    payloads = [relation_properties]

    basic_data = relation_properties.get("basic_data")
    if isinstance(basic_data, dict):
        payloads.append(basic_data)

    for payload in payloads:
        for key in ("cmd", "stream_cmd", "play_cmd", "play_url"):
            value = payload.get(key)
            if value not in (None, ""):
                text = str(value).strip()
                if text:
                    return text

    return ""


def _get_relation_content_type(relation) -> str:
    if hasattr(relation, "movie_id"):
        return "movie"
    if hasattr(relation, "episode_id"):
        return "episode"
    return ""


def _is_movie_relation(relation) -> bool:
    return _get_relation_content_type(relation) == "movie"
