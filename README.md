# MetaTrader 5 Quant Server (Docker)

Dockerized stack for:
- MT5 execution API,
- Django trade API and quant orchestration,
- Control API + frontend dashboard,
- optional worker/monitoring services (including n8n).

## Documentation

- [ALGORITHMS.md](ALGORITHMS.md): algorithm runtime, disable/rollback, and extension guide.
- [FEATURES.md](FEATURES.md): capability breakdown for frontend, control API, Redis, n8n.
- [ARCHITECTURE.md](ARCHITECTURE.md): component map and data-flow walkthrough.
- [AWS-MIGRATE.md](AWS-MIGRATE.md): EC2 + DNS + startup runbook.

## Quick Start

1. Copy environment variables:
   - `cp .env.example .env`
2. Set required domains/secrets in `.env`.
3. Ensure Traefik external network exists:
   - `docker network create traefik-public || true`
4. Start stack:
   - `docker compose up -d --build`

Stop stack:
- `docker compose down`

View logs:
- `docker compose logs -f`

## API-only Mode (Disable Auto Algorithm Startup)

If you only want API trade execution and no auto-running algorithms:

1. Set in `.env`:
   - `ENABLE_QUANT_BOOTSTRAP=false`
2. Restart Django:
   - `docker compose restart django`

Effect:
- Django API endpoints remain available.
- MT5 execution path remains available.
- Quant startup hooks in `QuantConfig.ready()` do not auto-run.

To re-enable algorithms:
- set `ENABLE_QUANT_BOOTSTRAP=true`
- restart `django`

## Services

Default services:
- `traefik`, `mt5`, `postgres`, `frontend`, `control-api`, `django`, `redis`

Optional profiles:
- `worker`: `celery`, `celery-beat`
- `monitor`: `grafana`, `prometheus`, exporters/log stack, `n8n`

Start with profiles:
- `docker compose --profile worker up -d`
- `docker compose --profile monitor up -d`

## Endpoint Validation

Replace placeholders with your configured domains.

- Traefik dashboard:
  - `https://<TRAEFIK_DOMAIN>`
- Frontend:
  - `https://<FRONTEND_DOMAIN>`
- Django API root:
  - `https://<DJANGO_DOMAIN>/v1/`
- Django display page:
  - `https://<DJANGO_DOMAIN>/displays/`
- Control API status:
  - `https://<CONTROL_API_DOMAIN>/get-django-status`
- Control API summary:
  - `https://<CONTROL_API_DOMAIN>/get-arbitrage-summary`
- Control API SSE:
  - `https://<CONTROL_API_DOMAIN>/stream/quants`
- MT5 health:
  - `https://<API_DOMAIN>/health`
- MT5 account:
  - `https://<API_DOMAIN>/account_info`
- n8n (monitor profile):
  - `https://<N8N_DOMAIN>`
- Grafana (monitor profile):
  - `https://<GRAFANA_DOMAIN>`

## Trade Execution Validation (Django -> MT5)

Call:
- `POST https://<DJANGO_DOMAIN>/v1/send_market_order/`

Expected:
- Request is validated in Django.
- Django forwards execution to MT5 API (`MT5_API_URL`).
- Response returns trade metadata or a validation/execution error.

## Notes

- `control-api` has pause toggles (`/pause-position-sync`, `/pause-grid-bot`) that are partial controls; they do not replace full bootstrap disable.
- n8n is optional and mainly used as a webhook automation sink when `N8N_WEBHOOK_URL` is configured.
