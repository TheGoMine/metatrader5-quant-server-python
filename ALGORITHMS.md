# Algorithms Guide

## Purpose

This document explains:
- where algorithm code lives,
- how algorithm runtime starts,
- what was disabled in Step 1,
- how to re-enable it,
- how to use and add algorithms safely.

## Folder Structure

- `backend/django/app/quant/algorithms/arbitrage/`
  - `subscribe.py`: market/position subscriptions and Redis feed updates.
  - `price_diff.py`: spread/premium comparison loop and optional n8n webhook post.
  - `position_sync.py`: hedge reconciliation logic and MT5 order syncing.
  - `net_position.py`: net exposure checks and close-by behavior.
  - `grid_bot.py`: grid strategy execution using Redis channels/settings.
  - `entry.py`: arbitrage entry trigger handling.
  - `config.py`: pair mapping and algorithm constants.
- `backend/django/app/quant/algorithms/mean_reversion/`
  - `entry.py`, `trailing.py`, `config.py`.
- `backend/django/app/quant/algorithms/close/`
  - `close.py`.
- `backend/django/app/quant/indicators/`
  - strategy indicators used by quant logic.
- `backend/django/app/quant/tasks.py`
  - Celery task wrappers and task-level orchestration.

## How Runtime Starts

On Django startup, `QuantConfig.ready()` in `backend/django/app/quant/apps.py` is invoked.  
If quant bootstrap is enabled, it starts algorithm runners:
- `subscribe.start_subscriptions()`
- `position_sync.start_position_sync()`
- `net_position.start_net_position_check()`
- `price_diff.start_comparison()`
- `grid_bot.start_grid_bot_sync()`

These are mostly long-running loops/threads and Redis pubsub consumers.

## Step 1: What Was Disabled

A startup gate was added in `backend/django/app/quant/apps.py`:
- Env flag: `ENABLE_QUANT_BOOTSTRAP`
- Behavior:
  - `true`: startup hooks run and algorithms auto-start.
  - `false` (default): `ready()` returns early, so algorithm startup hooks do not run.

This disables automatic algorithm execution while keeping Django API endpoints alive.
As an extra safety layer, each arbitrage `start_*()` launcher also checks this flag and exits early when disabled.

Currently disabled startup hooks:
- `subscribe.start_subscriptions()`
- `position_sync.start_position_sync()`
- `net_position.start_net_position_check()`
- `price_diff.start_comparison()`
- `grid_bot.start_grid_bot_sync()`

## How To Reverse (Re-enable Algorithms)

1. Open your `.env`.
2. Set:
   - `ENABLE_QUANT_BOOTSTRAP=true`
3. Restart Django container:
   - `docker compose restart django`
4. Verify algorithm loops/logs appear again in Django logs.

To disable again:
- `ENABLE_QUANT_BOOTSTRAP=false`
- `docker compose restart django`

## Runtime Controls (Without Full Disable)

Control API supports partial runtime toggles:
- `POST /pause-position-sync`
- `POST /pause-grid-bot`

These pause specific behaviors but do not fully stop every algorithm loop.

## Using Algorithms

- Ensure required env vars are set (`PAIR_INDEX`, `CONTRACT_SIZE`, `MINIMUM_TRADE_AMOUNT`, `REDIS_URL`, `MT5_API_URL`, Binance keys).
- Keep Redis available; many algorithms rely on Redis keys/channels.
- Use Control API to inspect and tune runtime:
  - `GET /get-arbitrage-summary`
  - `POST /set-grid-channel`
  - `GET /stream/quants`

## Creating A New Algorithm

1. Create a new module under `backend/django/app/quant/algorithms/<strategy>/`.
2. Implement core function(s) and a non-blocking `start_*()` launcher.
3. Add error handling/retry logic in loops and network calls.
4. Add runtime guard support:
   - startup guard via `ENABLE_QUANT_BOOTSTRAP`, and/or
   - Redis pause key for strategy-level toggles.
5. Wire startup only if always-on behavior is intended:
   - add `start_*()` call in `QuantConfig.ready()`.
6. Route execution through existing adapters for execution:
   - MT5: `app.utils.api.order`
   - market/position data: existing connector modules.
7. Document required env vars and Redis keys in this file (or strategy README).

## Recommended Safety Pattern

- Keep new algorithms behind explicit flags.
- Start with dry-run/log-only mode in non-production.
- Verify MT5 request/response handling before live order placement.
