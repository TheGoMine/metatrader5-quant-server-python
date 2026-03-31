# API Reference

This document lists exposed APIs for Django, MT5 Flask API, and Control API, with usage examples.

## Base Domains

- Django API: `https://<DJANGO_DOMAIN>`
- MT5 Flask API: `https://<API_DOMAIN>`
- Control API: `https://<CONTROL_API_DOMAIN>`

Note: some Django endpoints require authentication (`IsAuthenticated`).

---

## Django API

### Root routes

- `GET /v1/`
- `GET /v1/trades/`
- `POST /v1/send_market_order/` (auth required)
- `POST /v1/modify_sl_tp/` (auth required)
- `POST /v1/price_alert/`
- `GET /displays/`
- `POST /displays/pause/`
- `GET /displays/summary/`

### `POST /v1/send_market_order/`

- Purpose:
  - Place a market order through Django -> MT5 API path.
- Required body:
  - `symbol` (string), `volume` (number), `order_type` (`BUY` or `SELL`)
- Optional body:
  - `sl`, `tp`, `deviation`, `comment`, `magic`, `type_filling`
- Example:

```bash
curl -k -X POST "https://<DJANGO_DOMAIN>/v1/send_market_order/" \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "symbol":"XAUUSD",
    "volume":0.01,
    "order_type":"BUY",
    "deviation":20,
    "comment":"smoke-test"
  }'
```

- Typical success response:
  - HTTP `201`
  - `{ "trade": { ... }, "mutations": [ ... ] }`

### `POST /v1/modify_sl_tp/`

- Purpose:
  - Modify stop loss / take profit on an existing trade/position.
- Required body:
  - `id`, `ticket`, `stop_loss`, `take_profit`
- Typical success response:
  - HTTP `201`
  - `{ "mutation": { ... } }`

### `POST /v1/price_alert/`

- Purpose:
  - Submit symbol alert payload to quant task handling.
- Required body:
  - includes `symbol`
- Typical response:
  - HTTP `202`
  - `{ "message": "Price alert received and queued for processing." }`

---

## MT5 Flask API

### Discovery

- Swagger UI:
  - `GET /apidocs/`

### Health/session

- `GET /alive`
  - Fast liveness check.
- `GET /health`
  - Service health (does not force MT5 init).
- `GET /mt5-health`
  - MT5 terminal/account snapshot + last error.
- `GET /account_info`
  - MT5 account info if session is available.

Example:

```bash
curl -k "https://<API_DOMAIN>/alive"
curl -k "https://<API_DOMAIN>/health"
curl -k "https://<API_DOMAIN>/mt5-health"
curl -k "https://<API_DOMAIN>/account_info"
```

### Trading/order

- `POST /order`
  - Execute market order / close-by action.

Example:

```bash
curl -k -X POST "https://<API_DOMAIN>/order" \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "XAUUSD",
    "volume": 0.01,
    "type": "BUY",
    "deviation": 20,
    "comment": "api-direct-test"
  }'
```

Typical response:
- Success: `{ "message": "Order executed successfully", "result": { ... } }`
- Failure: `{ "error": "...", "mt5_error": "...", "result": { ... } }`

### Positions

- `POST /close_position`
- `POST /close_all_positions`
- `POST /modify_sl_tp`
- `GET /get_positions`
- `GET /positions_total`

### Symbols/market data

- `GET /symbol_info_tick/<symbol>`
- `GET /symbol_info/<symbol>`
- `GET /fetch_data_pos`
- `GET /fetch_data_range`

### History

- `GET /get_deal_from_ticket`
- `GET /get_order_from_ticket`
- `GET /history_deals_get`
- `GET /history_orders_get`

### Errors

- `GET /last_error`
- `GET /last_error_str`

---

## Control API

### Root/ops

- `GET /`
  - Basic service response (`Hello, Arbitrage!`)
- `GET /get-django-status`
  - Django container status.
- `POST /stop-quant`
  - Stop Django container.
- `POST /restart`
  - Restart named container.

Example restart:

```bash
curl -k -X POST "https://<CONTROL_API_DOMAIN>/restart" \
  -H "Content-Type: application/json" \
  -d '{"container":"django"}'
```

### Runtime controls

- `POST /pause-position-sync`
  - Toggle `position_sync_paused_flag`.
- `POST /pause-grid-bot`
  - Toggle `grid_bot_paused_flag`.
- `POST /set-grid-channel`
  - Set grid parameters and publish to Redis.

Example `set-grid-channel`:

```bash
curl -k -X POST "https://<CONTROL_API_DOMAIN>/set-grid-channel" \
  -H "Content-Type: application/json" \
  -d '{
    "upper_diff": 10,
    "lower_diff": -10,
    "max_position_size": 0.05,
    "order_size": 0.01,
    "close_long": 3,
    "close_short": 3
  }'
```

### Monitoring/summary

- `GET /get-arbitrage-summary`
  - Aggregated spread/exposure/position summary.
- `GET /user-info`
  - Account/binance-holder metadata.
- `GET /stream/quants`
  - SSE stream for live UI updates.

Example SSE:

```bash
curl -k -N "https://<CONTROL_API_DOMAIN>/stream/quants"
```

---

## Typical API Call Paths

- Client -> Django `send_market_order` -> MT5 `/order`
- Frontend -> Control API (`pause`, `restart`, `summary`, `stream`) -> Redis and Docker/MT5 state
- Django quant modules -> Redis + Binance/MT5 connectors (plus optional `N8N_WEBHOOK_URL`)

