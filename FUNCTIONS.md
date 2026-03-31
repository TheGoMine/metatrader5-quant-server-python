# Service Functions

This document explains what each sub-service does, who calls it, and what it connects to.

## Traefik

- Purpose:
  - Public ingress and TLS termination for all exposed domains.
  - Host-based routing to internal services (`mt5`, `django`, `control-api`, `frontend`, optional `n8n`, `grafana`).
- Main inputs:
  - HTTP/HTTPS traffic on ports `80` and `443`.
- Main outputs:
  - Proxied requests to target containers on `traefik-public` network.
- Depends on:
  - Docker labels on each service.
  - `/var/run/docker.sock` to discover containers.

## MT5 Flask API (`backend/mt5`)

- Purpose:
  - Trading execution bridge and market/account/position/history API on top of MetaTrader5 Python package running in Wine.
  - Exposes health/debug routes and Swagger docs.
- Main inputs:
  - Requests from Django and external callers (through Traefik host `API_DOMAIN`).
- Main outputs:
  - Order execution, position changes, symbol ticks/info, account info, history lookups.
- Connects to:
  - MetaTrader terminal process in Wine (`terminal64.exe`).
  - Returns JSON API responses.
- Important notes:
  - MT5 session must be initialized successfully (`/mt5-health`) for trading/account endpoints to work.

## Django API (`backend/django`)

- Purpose:
  - Main application API for clients.
  - Exposes `/v1` trading endpoints and `/displays` operational pages.
  - Persists and queries trade records in PostgreSQL.
- Main inputs:
  - Client/API requests to `DJANGO_DOMAIN`.
  - Internal data from algorithms/tasks.
- Main outputs:
  - Trading actions proxied to MT5 API.
  - Trade and mutation records.
- Connects to:
  - MT5 API (`MT5_API_URL`, typically `http://mt5:5001`).
  - PostgreSQL for data persistence.
  - Redis for runtime state/pubsub used by quant components.

## Control API (`backend/control-api`)

- Purpose:
  - Operational control plane for bot/runtime operations.
  - Provides stop/restart/status controls and live SSE stream.
- Main inputs:
  - Frontend UI calls.
  - Operator/API requests.
- Main outputs:
  - Container control actions (stop/restart django).
  - Redis pause flags and grid setting updates.
  - Arbitrage summary and user info.
- Connects to:
  - Docker socket (`/var/run/docker.sock`) for container actions.
  - Redis for state and pubsub.
  - MT5 API domain for account info (`/account_info`).

## Frontend (`frontend`)

- Purpose:
  - Monitoring and control dashboard for arbitrage/quant operations.
- Main inputs:
  - Control API endpoints and SSE stream.
  - Up to 3 configured API base URLs.
- Main outputs:
  - Pause/restart/grid-setting actions to Control API.
  - Visualization for user/account, spread, positions, exposure summary.
- Connects to:
  - Control API only (not directly to MT5 or Django in current wiring).

## Redis

- Purpose:
  - Shared cache/state and pubsub backbone.
- Used for:
  - Position/ticker snapshots.
  - Pause flags (`position_sync_paused_flag`, `grid_bot_paused_flag`).
  - Grid settings channels and state.
  - Celery broker/backend (from env configuration).
- Connects to:
  - Django quant modules.
  - Control API.
  - Optional workers.

## PostgreSQL

- Purpose:
  - Primary persistent datastore.
- Used by:
  - Django models (trades, mutations, etc.).
  - n8n database backend (when monitor profile is enabled).

## Celery Worker / Beat (optional profile `worker`)

- Purpose:
  - Background task execution and scheduling.
- Status in this stack:
  - Available in compose profile; run when needed.
  - Redis-backed broker/result.
- Usefulness:
  - Useful when you need asynchronous/background processing (Celery tasks) and periodic schedules.
  - Not required for a minimal API-only flow where you only call Django endpoints for direct MT5 execution.
  - If your current objective is "Django API -> MT5 execution only", you can keep `worker` profile off.

## n8n (optional profile `monitor`)

- Purpose:
  - Workflow automation target for webhooks and integrations.
- In this repo:
  - Arbitrage price diff logic can post to `N8N_WEBHOOK_URL`.
  - n8n can trigger notifications, custom logic, and external integrations.
- Impact:
  - Optional for core MT5 trade execution path.
  - If unavailable, webhook-dependent automation may fail/log errors.

## Monitoring Stack (optional profile `monitor`)

### Grafana

- Purpose:
  - Visualization dashboard for metrics/log data.
- Connects to:
  - Prometheus (metrics) and Loki (logs) data sources.
- Access:
  - Exposed by Traefik on `GRAFANA_DOMAIN`.

### Prometheus

- Purpose:
  - Metrics scraping and time-series storage for container/node/service telemetry.
- Connects to:
  - Exporters (`cadvisor`, `node-exporter`) and configured scrape targets.
- Role in stack:
  - Primary metrics backend for Grafana.

### Loki

- Purpose:
  - Log aggregation backend for container logs.
- Connects to:
  - `promtail` shipping pipeline.
- Role in stack:
  - Searchable logs in Grafana.

### Promtail

- Purpose:
  - Collects Docker container logs and ships to Loki.
- Connects to:
  - Docker container log paths and Docker socket metadata.

### cAdvisor

- Purpose:
  - Container-level resource metrics exporter.
- Provides:
  - CPU, memory, filesystem, and container runtime metrics.

### node-exporter

- Purpose:
  - Host-level metrics exporter.
- Provides:
  - CPU, memory, disk, network, and system metrics.

### Alertmanager

- Purpose:
  - Alert routing and notification dispatch for Prometheus alerts.

### Uncomplicated Alert Receiver

- Purpose:
  - Simple alert sink/receiver target used by alerting pipeline.

