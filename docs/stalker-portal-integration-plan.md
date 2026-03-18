# Stalker Portal Integration Plan

## Purpose

This file is the working plan and handoff document for adding Stalker portal support to Dispatcharr.

Future sessions should use this file as the source of truth instead of rediscovering the architecture from scratch.

## Current Decision

- Add Stalker as a new provider type inside the existing `M3UAccount` flow.
- MVP scope is live TV only.
- Do not build Stalker VOD support in the MVP.
- Do not build a Stalker-compatible output proxy in the MVP.
- Reuse ideas from `stalkerhek`, but keep Dispatcharr's own proxy/output stack.

## Architecture Snapshot

### Dispatcharr integration seams

- Source model:
  - `apps/m3u/models.py`
  - `M3UAccount` currently supports `STD` and `XC`
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

### Stalker behaviors we need

- Handshake and session token:
  - `/Users/jimmyfortin/workspaces/stalkerhek/stalker/authentication.go`
- Channel and genre discovery:
  - `/Users/jimmyfortin/workspaces/stalkerhek/stalker/channels.go`
- Short-lived playback URL creation via `create_link`:
  - `/Users/jimmyfortin/workspaces/stalkerhek/stalker/channels.go`
- Optional auth/device fields:
  - `/Users/jimmyfortin/workspaces/stalkerhek/stalker/fs.go`
  - `/Users/jimmyfortin/workspaces/stalkerhek/webui/profiles.go`

## Key Constraint

Stalker is not just another static playlist source.

The main difference from `STD` and `XC` is that playback must resolve a fresh upstream URL from the portal at play time. The current code assumes `Stream.url` is already usable. That assumption must be isolated behind a provider-aware resolver before Stalker playback is reliable.

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
  - `token` if needed for refresh/playback lifecycle
- Store group mapping in `ChannelGroupM3UAccount.custom_properties`:
  - `stalker_genre_id`
- Store stream metadata in `Stream.custom_properties`:
  - `cmd`
  - `cmd_id`
  - `cmd_ch_id`
  - `genre_id`
  - any portal fields useful for diagnostics

## Non-Goals For MVP

- Stalker VOD ingestion
- Stalker-compatible downstream proxy endpoints
- Replacing Dispatcharr proxy/HLS code with `stalkerhek`
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
- [ ] Phase 3 implemented
- [ ] Phase 4 implemented
- [ ] Phase 5 implemented
- [ ] Phase 6 implemented
- [ ] Phase 7 implemented
- [ ] Phase 8 implemented
- [ ] Phase 9 implemented

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
  - XC-only VOD toggles
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

## Implementation Notes

### Recommended module addition

Create a dedicated client module instead of embedding protocol logic in task functions.

Suggested location:

- `core/stalker.py`

Suggested responsibilities:

- portal URL normalization
- handshake
- auth
- get genres
- get channels
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

## Risks To Watch

- Hashing on ephemeral playback URLs will break identity and duplicate streams
- Profile regex transforms may accidentally mutate Stalker links in unsafe ways
- Re-auth/retry loops can cause provider bans if not bounded
- Existing XC-only assumptions may exist in status, profile, or refresh code paths

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

## If Live Testing Needs Portal Data

If a phase requires real portal verification, the user may additionally provide:

- a Stalker portal URL
- a MAC address
- optional username/password
- whether the portal uses non-default device fields

Those are not required to start coding the phase, but they are useful if the user wants live end-to-end verification beyond unit tests and code wiring.
