# Stalker Portal Integration Plan

## Purpose

This file is the working plan and handoff document for adding Stalker portal support to Dispatcharr.

Future sessions should use this file as the source of truth instead of rediscovering the architecture from scratch.

## Current Decision

- Add Stalker as a new provider type inside the existing `M3UAccount` flow.
- Treat live TV support as the completed baseline and extend the same provider model into the existing VOD stack.
- Build Stalker VOD support inside the current `apps/vod` ingestion, relation, and proxy flow instead of inventing a parallel subsystem.
- Do not build a Stalker-compatible output proxy in the MVP.
- Reuse ideas from `stalkerhek`, but keep Dispatcharr's own proxy/output stack.

## Architecture Snapshot

### Dispatcharr integration seams

- Source model:
  - `apps/m3u/models.py`
  - `M3UAccount` currently supports `STD`, `XC`, and `STALKER`
- Group discovery:
  - `apps/m3u/tasks.py`
  - `refresh_m3u_groups()`
  - `process_groups()`
- Stream import:
  - `apps/m3u/tasks.py`
  - `_refresh_single_m3u_account_impl()`
  - `process_m3u_batch_direct()`
  - `collect_xc_streams()`
  - `process_xc_category_direct()`
- Live playback URL generation:
  - `apps/proxy/ts_proxy/url_utils.py`
  - `generate_stream_url()`
  - `get_stream_info_for_switch()`
  - `transform_url()`
- Live playback entry points:
  - `apps/proxy/ts_proxy/views.py`
  - `core/views.py`
- Group relation metadata:
  - `apps/channels/models.py`
  - `ChannelGroupM3UAccount.custom_properties`
- M3U account UI:
  - `frontend/src/components/forms/M3U.jsx`
  - `frontend/src/components/tables/M3UsTable.jsx`
- VOD category discovery and settings:
  - `apps/vod/tasks.py`
  - `apps/vod/api_views.py`
  - `refresh_vod_content()`
  - `refresh_categories()`
  - `batch_create_categories()`
  - `frontend/src/components/forms/M3UGroupFilter.jsx`
  - `frontend/src/components/forms/VODCategoryFilter.jsx`
- VOD content storage:
  - `apps/vod/models.py`
  - `VODCategory`
  - `Movie`
  - `Series`
  - `Episode`
  - `M3UVODCategoryRelation`
  - `M3UMovieRelation`
  - `M3USeriesRelation`
  - `M3UEpisodeRelation`
- VOD content refresh:
  - `apps/vod/tasks.py`
  - `refresh_movies()`
  - `refresh_series()`
  - `refresh_series_episodes()`
- VOD playback URL generation and proxy:
  - `apps/proxy/vod_proxy/views.py`
  - `VODStreamView`
  - `_get_stream_url_from_relation()`
  - `_transform_url()`

### Stalker behaviors we need

- Handshake and session token:
  - `/Users/jimmyfortin/workspaces/stalkerhek/stalker/authentication.go`
- Channel and genre discovery:
  - `/Users/jimmyfortin/workspaces/stalkerhek/stalker/channels.go`
- Short-lived playback URL creation via `create_link`:
  - `/Users/jimmyfortin/workspaces/stalkerhek/stalker/channels.go`
- VOD catalog/category discovery:
  - endpoint and response-shape discovery still required against real Stalker portal payloads
- Series detail and episode discovery:
  - endpoint and response-shape discovery still required against real Stalker portal payloads
- VOD playback URL creation:
  - confirm whether Stalker VOD also requires `create_link` or returns a directly playable URL
- Optional auth/device fields:
  - `/Users/jimmyfortin/workspaces/stalkerhek/stalker/fs.go`
  - `/Users/jimmyfortin/workspaces/stalkerhek/webui/profiles.go`

## Key Constraint

Stalker is not just another static playlist source.

The main difference from `STD` and `XC` is that playback must resolve a fresh upstream URL from the portal at play time. The current code assumes `Stream.url` is already usable. That assumption must be isolated behind a provider-aware resolver before Stalker playback is reliable.

That same problem exists in the VOD path:

- `apps/vod/tasks.py` only refreshes VOD for `XC`
- `frontend/src/components/forms/M3UGroupFilter.jsx` only loads VOD categories for `XC`
- `apps/vod/models.py` only knows how to build XC movie and episode URLs
- `apps/proxy/vod_proxy/views.py` assumes each relation can already produce a usable URL

Stalker VOD support must remove those XC-only assumptions without regressing existing XC behavior.

## Storage Strategy

Use `M3UAccount` with a new `account_type` of `STALKER`.

- Reuse existing fields:
  - `name`
  - `server_url`
  - `username`
  - `password`
  - `user_agent`
  - `max_streams`
  - `refresh_interval`
- Store Stalker-specific fields in `custom_properties`:
  - `mac`
  - `model`
  - `serial_number`
  - `device_id`
  - `device_id2`
  - `signature`
  - `timezone`
  - `enable_vod`
  - `token` if needed for refresh/playback lifecycle
- Store group mapping in `ChannelGroupM3UAccount.custom_properties`:
  - `stalker_genre_id`
- Store VOD category mapping in `M3UVODCategoryRelation.custom_properties`:
  - `stalker_category_id`
  - `stalker_category_type`
- Store stream metadata in `Stream.custom_properties`:
  - `cmd`
  - `cmd_id`
  - `cmd_ch_id`
  - `genre_id`
  - any portal fields useful for diagnostics
- Store movie relation metadata in `M3UMovieRelation.custom_properties`:
  - stable Stalker movie identifier
  - source command or playback token inputs if required
  - raw portal fields needed for refresh diagnostics
- Store series relation metadata in `M3USeriesRelation.custom_properties`:
  - stable Stalker series identifier
  - provider payload needed to refresh episodes later
  - flags indicating whether detailed metadata and episodes were fetched
- Store episode relation metadata in `M3UEpisodeRelation.custom_properties`:
  - stable Stalker episode identifier
  - source command or playback token inputs if required
  - raw portal fields needed for playback retries

For live and VOD alike, do not key identity off a `create_link` result. Persist the stable provider identifiers and resolve playback URLs at request time.

## Non-Goals For MVP

- Stalker-compatible downstream proxy endpoints
- Replacing Dispatcharr proxy/HLS code with `stalkerhek`
- Building a second standalone Stalker VOD library outside the current `apps/vod` models/tasks
- Metadata enrichment beyond the fields the current VOD stack already stores
- Large refactors of unrelated `STD` / `XC` code paths

## Phase Rules

- Every phase must end with a visible UI path that the user can test in the running app.
- No backend-only phase.
- Every phase must be mergeable without breaking `STD` or `XC`.
- Do not start a later phase until the earlier phase has a working UI test path.

## Status

- [x] Planning complete
- [x] Phase 0 implemented
- [x] Phase 1 implemented
- [x] Phase 2 implemented
- [x] Phase 3 implemented
- [x] Phase 4 implemented
- [x] Phase 5 implemented
- [ ] Phase 6 implemented
- [ ] Phase 7 implemented
- [ ] Phase 8 implemented
- [ ] Phase 9 implemented
- [x] Phase 10 implemented
- [ ] Phase 11 implemented
- [ ] Phase 12 implemented
- [ ] Phase 13 implemented
- [ ] Phase 14 implemented
- [ ] Phase 15 implemented

## Phases

### Phase 0: Account Type Shell

#### Goal

Add `STALKER` as a first-class account type and expose its fields in the UI.

#### Backend work

- Add `STALKER` to `M3UAccount.Types`
- Extend serializer validation for Stalker fields
- Preserve Stalker-specific values in `custom_properties`

#### Frontend work

- Update `frontend/src/components/forms/M3U.jsx`
- Add `Stalker` to account type selector
- Show:
  - portal URL
  - MAC
  - optional username/password
  - optional advanced device fields
- Hide:
  - file upload
  - VOD toggles until the VOD track begins
  - XC-only helper text

#### UI test

- Open `M3U & EPG Manager`
- Create a Stalker account
- Edit it
- Reload the page and confirm fields persist

#### Done when

- A Stalker account can be created, edited, listed, and deleted from the UI
- No refresh or connection logic is required yet

### Phase 1: Connection Test

#### Goal

Validate Stalker portal credentials from the UI without importing groups or streams yet.

#### Backend work

- Add a Stalker client module under `core/` or `apps/m3u/`
- Implement:
  - portal URL normalization
  - handshake
  - optional auth
  - a small validation call such as genre or profile fetch
- Add an API action for `test-connection`

#### Frontend work

- Add a `Test Connection` button to the Stalker form
- Show success/error in the form and persist summary in `status` / `last_message`

#### UI test

- Test with:
  - valid portal + MAC
  - wrong endpoint
  - wrong MAC
  - auth-required portal with bad credentials

#### Done when

- The M3U table shows usable Stalker-specific connection errors
- No shell access is needed to validate the portal

### Phase 2: Group Discovery

#### Goal

Fetch and persist Stalker genres/categories so the existing Groups modal becomes usable.

#### Backend work

- Extend `refresh_m3u_groups()` for `STALKER`
- Fetch genres/categories from the Stalker client
- Map them into `process_groups()`
- Persist `stalker_genre_id` in `ChannelGroupM3UAccount.custom_properties`
- Stop after group discovery and set `pending_setup`

#### Frontend work

- Reuse existing refresh flow and Groups modal
- Ensure labels/messages make sense for Stalker

#### UI test

- Click refresh on a Stalker account
- Open `Groups`
- Confirm real categories appear

#### Done when

- Group discovery works end-to-end from the UI
- No stream rows are required yet

### Phase 3: Group Settings Persistence

#### Goal

Make Stalker groups fully manageable with existing enable/disable and auto-sync controls.

#### Backend work

- Ensure group update endpoints work with Stalker groups
- Preserve `stalker_genre_id` when group settings are edited

#### Frontend work

- No major new UI expected
- Reuse existing group management UI

#### UI test

- Disable some groups
- Enable auto-sync on one group
- Save
- Reload
- Confirm values persist

#### Done when

- Stalker group configuration survives reloads and later refreshes

### Phase 4: Stream Import

#### Goal

Import live Stalker channels into `Stream` rows and display them in the Streams UI.

#### Backend work

- Add Stalker channel fetch logic to refresh
- Convert portal channels into Dispatcharr stream records
- Use stable identity from Stalker metadata, not from `create_link`
- Persist:
  - `cmd`
  - `cmd_id`
  - `cmd_ch_id`
  - `genre_id`
  - logo
  - epg/tvg identifiers if available

#### Frontend work

- Reuse `Streams` table

#### UI test

- Refresh a Stalker account
- Open `Streams`
- Confirm channels exist with correct groups and metadata
- Refresh again and confirm no duplicate explosion

#### Done when

- Stream import is visible and idempotent in the UI

### Phase 5: Single Stream Preview

#### Goal

Play one imported Stalker stream from the existing preview path.

#### Backend work

- Add provider-aware live URL resolution
- Resolve fresh Stalker playback URLs using `create_link`
- Make preview path use the resolver before applying profile transforms

#### Frontend work

- Reuse existing preview/play controls

#### UI test

- Play a Stalker stream from `Streams`
- Confirm live playback starts

#### Done when

- A Stalker stream can be previewed from the UI without manual URL work

### Phase 6: Fresh-Link Resolver Hardening

#### Goal

Make Stalker playback resilient to short-lived links and expired portal sessions.

#### Backend work

- Centralize Stalker URL resolution
- Retry once on expired link or auth/session failure
- Re-handshake and re-authenticate when needed

#### Frontend work

- No major new UI expected

#### UI test

- Let an old link expire
- Start playback again
- Confirm resolver fetches a fresh URL and succeeds

#### Done when

- Playback does not depend on stale stored URLs

### Phase 7: Channel Auto-Sync

#### Goal

Use existing group auto-sync to create real `Channel` rows from imported Stalker streams.

#### Backend work

- Reuse current auto-sync flow
- Confirm Stalker streams provide enough stable metadata for channel creation

#### Frontend work

- Reuse `Channels` page

#### UI test

- Enable auto-sync for a Stalker group
- Refresh
- Open `Channels`
- Confirm channels are created in the expected group

#### Done when

- Channels can be created and maintained from Stalker-backed streams

### Phase 8: Channel Playback And Stream Switching

#### Goal

Make normal channel playback use the Stalker resolver, including manual stream switching.

#### Backend work

- Ensure:
  - `generate_stream_url()`
  - `get_stream_info_for_switch()`
  - TS proxy switching paths
    use the provider-aware resolver for Stalker

#### Frontend work

- Reuse existing channel playback and switch controls

#### UI test

- Play from `Channels`
- Switch streams on a multi-stream channel if available
- Confirm playback survives stale session recovery

#### Done when

- Regular live TV usage works from the main UI

### Phase 9: DVR Support

#### Goal

Allow recordings from Stalker-backed channels.

#### Backend work

- Ensure recording startup uses the same Stalker resolver path
- Confirm refresh/session rules are compatible with longer-running capture

#### Frontend work

- Reuse existing DVR UI

#### UI test

- Start a recording from a Stalker-backed channel
- Stop it
- Confirm resulting file is valid

#### Done when

- DVR works from the standard UI without Stalker-specific manual steps

## VOD Track

The VOD phases depend on live phases 0 through 5 being in place because they reuse the same account type, Stalker auth fields, and session-handling primitives.

They do not need to wait for live phases 7 through 9. VOD can proceed in parallel with later live polish as long as shared auth/retry helpers stay centralized.

### Phase 10: VOD Enablement And Protocol Discovery

#### Goal

Turn on the Stalker account/UI switches needed for VOD and confirm the real portal endpoints and payload shapes we will use for movies, series, episodes, and categories.

#### Backend work

- Allow `enable_vod` for `STALKER` accounts
- Extend the Stalker client with a clearly separated VOD surface area, even if some methods initially raise `NotImplementedError` until endpoint validation is complete
- Capture example portal payloads for:
  - movie categories
  - series categories
  - movie list
  - series list
  - series detail / episodes
  - VOD playback link creation
- Document the normalized field mapping we will store in Dispatcharr

#### Frontend work

- Update `frontend/src/components/forms/M3U.jsx` so Stalker accounts can enable VOD scanning
- Update `frontend/src/components/forms/M3UGroupFilter.jsx` so Stalker accounts can open the VOD tabs and request category data

#### UI test

- Edit a saved Stalker account
- Enable VOD scanning
- Save and reload
- Confirm the flag persists and the VOD tabs are visible in the Groups modal

#### Done when

- The account model and UI can represent Stalker VOD support
- We have enough confirmed portal samples to implement the remaining VOD phases without guessing at response shapes

### Phase 11: VOD Category Discovery

#### Goal

Fetch Stalker movie and series categories and persist them into the existing VOD category relation model.

#### Backend work

- Extend `apps/vod/tasks.py` so `refresh_vod_content()` can run for `STALKER`
- Add Stalker category fetch logic for both movie and series content types
- Map provider category IDs into `VODCategory` and `M3UVODCategoryRelation`
- Store `stalker_category_id` and `stalker_category_type` in relation `custom_properties`
- Update `apps/vod/api_views.py` so helper logic that currently assumes XC also includes Stalker accounts with VOD enabled

#### Frontend work

- Reuse the existing VOD tabs in the Groups modal
- Ensure helper text and errors do not imply Xtream-only behavior

#### UI test

- Refresh a Stalker account with VOD enabled
- Open `Groups`
- Confirm movie and series categories appear under the VOD tabs
- Disable one category, save, reload, and confirm the setting persists

#### Done when

- Stalker movie and series categories flow through the existing VOD category UI end-to-end

### Phase 12: Movie And Series Import

#### Goal

Import Stalker movies and top-level series rows into the existing VOD library.

#### Backend work

- Extend `refresh_movies()` and `refresh_series()` for Stalker provider payloads
- Create or update:
  - `Movie`
  - `Series`
  - `M3UMovieRelation`
  - `M3USeriesRelation`
- Use stable Stalker identifiers for relation uniqueness, not playback links
- Persist enough metadata for logos, descriptions, year, rating, genre, category, and later episode refresh
- Keep orphan cleanup compatible with mixed XC and Stalker providers

#### Frontend work

- Reuse the existing `VODs` and series pages

#### UI test

- Refresh a Stalker account with VOD enabled
- Open `VODs`
- Confirm movies and series from the portal appear with categories and provider info
- Refresh again and confirm no duplicate explosion

#### Done when

- Movies and series import idempotently into the current VOD library

### Phase 13: Series Detail And Episode Import

#### Goal

Populate Stalker-backed series with episode rows using the same lazy refresh path already used by the VOD API.

#### Backend work

- Extend `refresh_series_episodes()` to support Stalker relations
- Fetch per-series detail and episode data from the Stalker client
- Create or update:
  - `Episode`
  - `M3UEpisodeRelation`
- Persist stable Stalker episode identifiers and any playback command metadata required later
- Mark `episodes_fetched` / `detailed_fetched` consistently in `M3USeriesRelation.custom_properties`

#### Frontend work

- Reuse the existing series details modal / provider info path

#### UI test

- Open a Stalker-backed series
- Trigger provider info / episode loading
- Confirm seasons and episodes populate and remain stable across refreshes

#### Done when

- Stalker series details and episodes are available through the standard series UI

### Phase 14: Movie Playback Resolver

#### Goal

Make Stalker-backed movies playable through the existing VOD proxy path.

#### Backend work

- Introduce a provider-aware VOD URL resolver instead of encoding provider logic directly in relation models
- Make `apps/proxy/vod_proxy/views.py` use that resolver before profile transforms
- Support XC and Stalker without changing the existing VOD route structure
- If Stalker requires short-lived links for movies, resolve them at play time and avoid storing them as durable relation state

#### Frontend work

- Reuse existing movie playback controls

#### UI test

- Play a Stalker-backed movie from the VOD UI
- Confirm playback starts through the normal `/vod/...` route

#### Done when

- A Stalker movie can be played from the UI without manual URL work

### Phase 15: Episode Playback And VOD Hardening

#### Goal

Make Stalker-backed episode playback reliable and align Stalker VOD retries with the existing live-session recovery approach.

#### Backend work

- Extend the VOD resolver to episodes and any series-first playback paths
- Reuse shared Stalker auth/session retry helpers instead of duplicating logic in `vod_proxy`
- Retry once on expired session or stale playback link when the portal indicates re-auth is required
- Verify seeking, range requests, and HEAD preflight still behave correctly when the source URL is freshly resolved

#### Frontend work

- Reuse existing episode playback controls

#### UI test

- Play a Stalker-backed episode
- Seek within the episode
- Retry after the original portal session has gone stale
- Confirm playback recovers without changing the client-facing VOD URL pattern

#### Done when

- Movies and episodes from Stalker-backed VOD providers play reliably through the existing proxy flow

## Implementation Notes

### Recommended module addition

Continue using a dedicated client module instead of embedding protocol logic in task functions.

Suggested location:

- `apps/m3u/stalker.py`

Suggested responsibilities:

- portal URL normalization
- handshake
- auth
- get genres
- get channels
- get VOD categories
- get movies
- get series
- get series details / episodes
- create playback link
- retry/re-auth helpers

### Recommended resolver seam

Introduce a provider-aware live URL resolver instead of teaching every caller about Stalker.

Suggested responsibilities:

- accept `Stream` + `M3UAccountProfile`
- return a usable upstream URL
- for `STD`: return transformed `stream.url`
- for `XC`: return transformed `stream.url`
- for `STALKER`: call the Stalker client to resolve a fresh `create_link` URL, then apply profile transforms only if still required

Likely touchpoints:

- `apps/proxy/ts_proxy/url_utils.py`
- `apps/proxy/ts_proxy/views.py`
- `core/views.py`

### Recommended VOD resolver seam

Introduce a provider-aware VOD URL resolver instead of keeping provider logic inside `M3UMovieRelation.get_stream_url()` and `M3UEpisodeRelation.get_stream_url()`.

Suggested responsibilities:

- accept a movie or episode relation plus `M3UAccountProfile`
- return a usable upstream URL
- for `XC`: keep building the existing direct URL
- for `STALKER`: resolve a fresh playback URL from stored provider metadata, then apply profile transforms only if still required

Likely touchpoints:

- `apps/vod/models.py`
- `apps/vod/tasks.py`
- `apps/proxy/vod_proxy/views.py`

## Risks To Watch

- Hashing on ephemeral playback URLs will break identity and duplicate streams
- Hashing on ephemeral VOD playback URLs will break relation identity and create duplicate movies or episodes
- Profile regex transforms may accidentally mutate Stalker links in unsafe ways
- Re-auth/retry loops can cause provider bans if not bounded
- Existing XC-only assumptions may exist in status, profile, or refresh code paths
- The current VOD cleanup logic may delete Stalker relations incorrectly if category or content identity mapping is not stable
- Some portals may expose incomplete movie or series metadata, so fallback mapping rules must be explicit

## Session Resume Instructions

When resuming later, the agent should:

1. Read this file first.
2. Inspect only the files relevant to the target phase.
3. Update the `Status` checklist in this file when a phase is completed.
4. Keep the scope limited to one phase unless the user explicitly asks to continue.

## What The User Should Say To Start A Phase

Use this format:

`Implement Phase N from docs/stalker-portal-integration-plan.md`

Optional add-ons:

- `Only do that phase and stop`
- `Include tests`
- `I will manually test the UI after you finish`
- `I want you to also update the plan file status checkbox`

Examples:

- `Implement Phase 0 from docs/stalker-portal-integration-plan.md. Only do that phase and stop.`
- `Implement Phase 2 from docs/stalker-portal-integration-plan.md and include tests.`
- `Implement Phase 5 from docs/stalker-portal-integration-plan.md. I will manually test playback afterwards.`
- `Implement Phase 10 from docs/stalker-portal-integration-plan.md and include tests.`

## If Portal Testing Needs Portal Data

If a phase requires real portal verification, the user may additionally provide:

- a Stalker portal URL
- a MAC address
- optional username/password
- whether the portal uses non-default device fields

Those are not required to start coding the phase, but they are useful if the user wants live end-to-end verification beyond unit tests and code wiring.
