# Stalker VOD Protocol Notes

This document is the Phase 10 working note for Stalker VOD enablement. It defines the VOD surface area we added to `apps/m3u/stalker.py`, the sample payloads we now capture on demand, and the normalized fields later phases should consume.

## Runtime Capture

Use `POST /api/m3u/accounts/<id>/discover-vod-protocol/` for a saved `STALKER` account.

The response payload and `account.custom_properties["stalker_vod_protocol_samples"]` store:

- `movie_categories`
- `series_categories`
- `movie_list`
- `series_list`
- `series_detail`
- `episodes`
- `vod_link`

These are raw sample slices from the portal, not imported Dispatcharr models.

## Current Endpoint Shape

The client now keeps a dedicated VOD surface, separate from live TV methods.

- Movie categories: `GET <portal>?type=vod&action=get_categories&JsHttpRequest=1-xml`
- Series categories: same request for now; some portals expose both trees through the shared VOD surface
- Movie list: `GET <portal>?type=vod&action=get_ordered_list&category=<category_id>&p=<page>&JsHttpRequest=1-xml`
- Series list: same ordered-list surface, with a series category
- Series detail / seasons: `GET <portal>?type=vod&action=get_ordered_list&movie_id=<series_id>&season_id=0&episode_id=0&p=<page>&JsHttpRequest=1-xml`
- Episodes for a season: `GET <portal>?type=vod&action=get_ordered_list&movie_id=<series_id>&season_id=<season_id>&episode_id=0&p=<page>&JsHttpRequest=1-xml`
- VOD playback link: `GET <portal>?action=create_link&type=vod&cmd=<escaped_cmd>&JsHttpRequest=1-xml`

## Normalized Mapping

Later phases should normalize provider payloads into Dispatcharr fields using these rules.

### Categories

- Provider ID: `id` or `category_id`
- Dispatcharr category name: `title`, else `name`, else `alias`
- Dispatcharr category type: inferred by which VOD surface fetched it, not by a provider enum

### Movies

- Provider relation key: `id` or `movie_id`
- Category key: `category_id` or the requested category context
- Display name: `name`, else `title`
- Description: `description`, else `plot`
- Year: `year`
- Rating: `rating`
- Genre: `genre`
- Cover/logo candidate: `screenshot_uri`, `cover`, or `logo`
- Playback command candidate: `cmd`, `stream_cmd`, `play_cmd`, `play_url`

### Series

- Provider relation key: `id` or `movie_id`
- Category key: `category_id` or the requested category context
- Display name: `name`, else `title`
- Description: `description`, else `plot`
- Year: `year`
- Rating: `rating`
- Genre: `genre`
- Cover/logo candidate: `screenshot_uri`, `cover`, or `logo`

### Seasons / Episodes

- Season relation key: `id`, `season_id`, or `movie_id`
- Episode relation key: `id` or `episode_id`
- Episode display name: `name`, else `title`
- Episode description: `description`, else `plot`
- Episode number: `series_number`, `episode_number`, or portal-specific episode metadata
- Duration: `time`, `runtime`, or later detailed metadata
- Playback command candidate: `cmd`, `stream_cmd`, `play_cmd`, `play_url`

## Sources Used For Phase 10

- [stalkerhek proxy.go](https://raw.githubusercontent.com/kidpoleon/stalkerhek/refs/heads/main/proxy/proxy.go)
- [Cyogenus IPTV MAC/Stalker player](https://github.com/Cyogenus/IPTV-MAC-STALKER-PLAYER-BY-MY-1/blob/main/stalker.py)
