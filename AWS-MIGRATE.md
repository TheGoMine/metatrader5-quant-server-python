# AWS Migration Runbook

## Scope

This runbook assumes:
- EC2 instance is created,
- SSH access works,
- inbound `80` and `443` are allowed,
- Cloudflare can point DNS records to your EC2 Elastic IP.

## 1) Preflight On EC2

- Install Docker and Docker Compose plugin.
- Clone repository onto the instance.
- Ensure external Docker network exists:
  - `docker network create traefik-public || true`
- Ensure volume/log mount path exists if you use `LOG_VOLUME`.

## 2) Configure Environment

1. Copy env template:
   - `cp .env.example .env`
2. Set all required values in `.env`.

Minimum required domains and purpose:
- `TRAEFIK_DOMAIN`: Traefik dashboard.
- `FRONTEND_DOMAIN`: React frontend.
- `CONTROL_API_DOMAIN`: control API.
- `DJANGO_DOMAIN`: Django API.
- `API_DOMAIN`: MT5 API (execution bridge).
- `VNC_DOMAIN`: MT5 VNC/noVNC UI.
- `N8N_DOMAIN`: n8n UI/webhook host (if used).
- `GRAFANA_DOMAIN`: Grafana host (if monitor profile enabled).

Critical runtime values:
- `TRAEFIK_USERNAME`, `TRAEFIK_HASHED_PASSWORD`, `ACME_EMAIL`
- `POSTGRES_*`
- `REDIS_URL`
- `MT5_API_URL` (normally `http://mt5:5001` inside compose network)
- `API_KEY_BINANCE`, `API_SECRET_BINANCE` (if quant features are used)
- `ENABLE_QUANT_BOOTSTRAP=false` for API-only mode

## 3) DNS Records Needed

Create Cloudflare DNS records (usually `A` records) that point to the EC2 Elastic IP:
- `TRAEFIK_DOMAIN`
- `FRONTEND_DOMAIN`
- `CONTROL_API_DOMAIN`
- `DJANGO_DOMAIN`
- `API_DOMAIN`
- `VNC_DOMAIN`
- `N8N_DOMAIN` (if running n8n)
- `GRAFANA_DOMAIN` (if running monitoring profile)

If a domain is configured in `.env` but does not resolve publicly, Traefik routing and certificate issuance for that host can fail.

## 4) Startup

If you already ran `make setup`, proceed with compose startup.

Recommended:
- `docker compose up -d --build`

Optional profiles:
- Workers: `docker compose --profile worker up -d`
- Monitoring + n8n: `docker compose --profile monitor up -d`

## 5) Suggested API-only Mode

To run only API execution without auto-running algorithms:
- Set `ENABLE_QUANT_BOOTSTRAP=false` in `.env`
- Restart Django:
  - `docker compose restart django`

Keep these services running:
- `traefik`
- `django`
- `mt5`
- `redis`
- `postgres`
- `control-api` (recommended for ops UI/actions)

## 6) Validation Checklist

Container checks:
- `docker compose ps`
- `docker compose logs -f django`
- `docker compose logs -f mt5`
- `docker compose logs -f control-api`

Endpoint checks:
- Traefik dashboard: `https://<TRAEFIK_DOMAIN>`
- Frontend: `https://<FRONTEND_DOMAIN>`
- Control API status: `https://<CONTROL_API_DOMAIN>/get-django-status`
- Control API summary: `https://<CONTROL_API_DOMAIN>/get-arbitrage-summary`
- Control API SSE: `https://<CONTROL_API_DOMAIN>/stream/quants`
- Django API root: `https://<DJANGO_DOMAIN>/v1/`
- Django display: `https://<DJANGO_DOMAIN>/displays/`
- MT5 health: `https://<API_DOMAIN>/health`
- MT5 account info: `https://<API_DOMAIN>/account_info`
- n8n (if enabled): `https://<N8N_DOMAIN>`
- Grafana (if enabled): `https://<GRAFANA_DOMAIN>`

Trade execution smoke test:
- Call Django `POST /v1/send_market_order/` with valid auth/payload and confirm MT5 execution response.

## 7) Troubleshooting

### Certificates Not Issuing
- Verify hostnames resolve to EC2 Elastic IP.
- Verify ports `80/443` are open in security group and host firewall.
- Confirm `ACME_EMAIL` is set.

### Control API CORS/UI Access Problems
- Verify frontend is calling the expected `CONTROL_API_DOMAIN`.
- Check control API CORS config if you changed frontend domain.

### Control API Cannot Stop/Restart Containers
- Ensure `/var/run/docker.sock` is mounted in `control-api`.
- Confirm container name references are valid (`django` by default).

### n8n Errors
- n8n is optional unless your workflow depends on `N8N_WEBHOOK_URL`.
- If webhook URL is configured but n8n is down, arbitrage webhook posts can error in logs.
- API execution endpoints can still function without n8n.

### Algorithm Threads Still Running In API-only Mode
- Confirm `.env` has `ENABLE_QUANT_BOOTSTRAP=false`.
- Restart `django` container.
- Verify startup logs do not show algorithm boot actions.
