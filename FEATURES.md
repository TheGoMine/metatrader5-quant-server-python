# Features Overview

## Frontend

Frontend is a React + Vite dashboard focused on operations and monitoring.

Capabilities:
- Call control actions:
  - pause/resume position sync (`/pause-position-sync`)
  - pause/resume grid bot (`/pause-grid-bot`)
  - apply grid settings (`/set-grid-channel`)
  - stop/restart Django bot container (`/stop-quant`, `/restart`)
- Query status and summary:
  - Django container status (`/get-django-status`)
  - arbitrage summary (`/get-arbitrage-summary`)
  - active user/account metadata (`/user-info`)
- Live updates:
  - SSE stream subscription to `/stream/quants`
  - caches streamed arbitrage/grid data in React Query
- Multi-endpoint setup:
  - supports up to 3 configured base URLs (`VITE_API_BASE_URL`, `_2`, `_3`)

Primary files:
- `frontend/src/query/apis.ts`
- `frontend/src/hooks/stream-master-data.ts`

## Control API

Control API is an operations gateway for runtime control and summarized state.

Capabilities:
- Container lifecycle control (via Docker socket):
  - stop Django container
  - restart target container
  - get Django container status
- Runtime strategy toggles:
  - pause/resume position sync through Redis key `position_sync_paused_flag`
  - pause/resume grid bot through Redis key `grid_bot_paused_flag`
- Grid channel management:
  - validate and write grid parameters to Redis
  - publish updated grid settings via Redis pubsub
- Aggregated status APIs:
  - arbitrage summary using Redis position/mark data
  - MT5 user/account info lookup
- Server-sent events:
  - `/stream/quants` for live state pushes to frontend

Primary file:
- `backend/control-api/app/routes/control.py`

## Redis

Redis is the shared runtime state and messaging backbone.

Capabilities in this stack:
- Shared key/value state for latest market/position data.
- Pause flags and operational switches consumed by strategy components.
- Grid strategy parameter storage.
- Pubsub channels for strategy reactions and control signaling.
- Celery broker/result backend (as configured in env).

Typical key patterns:
- `position:*`
- `setting_grid_channel:*`
- `position_sync_paused_flag`
- `grid_bot_paused_flag`

## n8n

n8n is an optional workflow automation endpoint.

Capabilities and purpose:
- Receives webhook payloads from Django arbitrage comparison logic when `N8N_WEBHOOK_URL` is configured.
- Can branch to notifications, persistence, external APIs, or alerting workflows without changing algorithm code.
- Runs as an independent service behind Traefik on `N8N_DOMAIN`.

Operational notes:
- n8n is in the `monitor` compose profile, so it is not started unless that profile is enabled.
- If n8n is not running and `N8N_WEBHOOK_URL` is unset/unused, core API execution still works.
- If `N8N_WEBHOOK_URL` points to an unavailable endpoint, webhook attempts may log errors in arbitrage flow, but MT5 API trade execution endpoints remain available.
