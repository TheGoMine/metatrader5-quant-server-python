import logging
import os
import re
import uuid
from typing import Any, Dict, Optional

from dotenv import load_dotenv
import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from mt5_client import MT5Client
from risk import calculate_lot_distribution
from signal_parser import parse_signal_text


load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
OWNER_ID_RAW = os.getenv("TELEGRAM_OWNER_ID", "").strip()
OWNER_ID = int(OWNER_ID_RAW) if OWNER_ID_RAW else None

mt5_client = MT5Client()
PENDING_EXECUTIONS_KEY = "pending_executions"
PENDING_CLOSEALL_KEY = "pending_closeall"
PENDING_TRIM_KEY = "pending_trim"
PENDING_EXDEFAULT_KEY = "pending_exdefault"
PENDING_EXNOW_MARKET_KEY = "pending_exnow_market"
PENDING_EDIT_KEY = "pending_edit"
LATEST_STRATEGY_KEY = "latest_strategy"

EXDEFAULT_ASSET_CONFIG: Dict[str, Dict[str, float]] = {
    "EURUSD": {"sl_points": 200, "tp1_rr": 1.5, "tp2_rr": 2.5},
    "XAUUSD": {"sl_points": 1000, "tp1_rr": 1.5, "tp2_rr": 2.5},
    "BTCUSD": {"sl_points": 5000, "tp1_rr": 1.5, "tp2_rr": 2.5},
}


def _is_owner(update: Update) -> bool:
    user = update.effective_user
    if not user or OWNER_ID is None:
        return False
    return user.id == OWNER_ID


def _normalize_symbol(raw_symbol: str) -> str:
    # Remove invisible/formatting characters that may appear in forwarded messages.
    return re.sub(r"[^A-Z0-9._-]", "", raw_symbol.upper())


def _is_live_tick(tick: Dict[str, Any]) -> bool:
    try:
        bid = float(tick.get("bid") or 0.0)
        ask = float(tick.get("ask") or 0.0)
        return bid > 0 and ask > 0
    except Exception:
        return False


def _fmt_price(value: Any, digits: int = 5) -> str:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "-"
    text = f"{num:.{digits}f}".rstrip("0").rstrip(".")
    return text if text else "0"


def _fmt_volume(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "-"


def _fmt_money(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return "-"


def _position_type_label(value: Any) -> str:
    try:
        position_type = int(value)
    except (TypeError, ValueError):
        return str(value)
    return "BUY" if position_type == 0 else "SELL" if position_type == 1 else str(position_type)


async def _reject_if_not_owner(update: Update) -> bool:
    if _is_owner(update):
        return False
    if update.message:
        await update.message.reply_text("Unauthorized user.")
    return True


def _pending_store(context: ContextTypes.DEFAULT_TYPE) -> Dict[str, Dict[str, Any]]:
    if PENDING_EXECUTIONS_KEY not in context.application.bot_data:
        context.application.bot_data[PENDING_EXECUTIONS_KEY] = {}
    return context.application.bot_data[PENDING_EXECUTIONS_KEY]


def _pending_closeall_store(context: ContextTypes.DEFAULT_TYPE) -> Dict[str, Dict[str, Any]]:
    if PENDING_CLOSEALL_KEY not in context.application.bot_data:
        context.application.bot_data[PENDING_CLOSEALL_KEY] = {}
    return context.application.bot_data[PENDING_CLOSEALL_KEY]


def _pending_trim_store(context: ContextTypes.DEFAULT_TYPE) -> Dict[str, Dict[str, Any]]:
    if PENDING_TRIM_KEY not in context.application.bot_data:
        context.application.bot_data[PENDING_TRIM_KEY] = {}
    return context.application.bot_data[PENDING_TRIM_KEY]


def _latest_strategy_store(context: ContextTypes.DEFAULT_TYPE) -> Dict[str, Any]:
    if LATEST_STRATEGY_KEY not in context.application.bot_data:
        context.application.bot_data[LATEST_STRATEGY_KEY] = {}
    return context.application.bot_data[LATEST_STRATEGY_KEY]


def _pending_exdefault_store(context: ContextTypes.DEFAULT_TYPE) -> Dict[str, Dict[str, Any]]:
    if PENDING_EXDEFAULT_KEY not in context.application.bot_data:
        context.application.bot_data[PENDING_EXDEFAULT_KEY] = {}
    return context.application.bot_data[PENDING_EXDEFAULT_KEY]


def _pending_exnow_market_store(context: ContextTypes.DEFAULT_TYPE) -> Dict[str, Dict[str, Any]]:
    if PENDING_EXNOW_MARKET_KEY not in context.application.bot_data:
        context.application.bot_data[PENDING_EXNOW_MARKET_KEY] = {}
    return context.application.bot_data[PENDING_EXNOW_MARKET_KEY]


def _pending_edit_store(context: ContextTypes.DEFAULT_TYPE) -> Dict[str, Dict[str, Any]]:
    if PENDING_EDIT_KEY not in context.application.bot_data:
        context.application.bot_data[PENDING_EDIT_KEY] = {}
    return context.application.bot_data[PENDING_EDIT_KEY]


def _is_bid_within_range(bid: Any, range_low: Any, range_high: Any) -> Optional[bool]:
    try:
        bid_num = float(bid)
        low = float(range_low)
        high = float(range_high)
    except (TypeError, ValueError):
        return None
    return low <= bid_num <= high


def _format_within_range_label(within_range: Optional[bool]) -> str:
    if within_range is True:
        return "YES"
    if within_range is False:
        return "NO"
    return "UNKNOWN"


def _format_positions_preview(positions: list[Dict[str, Any]], max_items: int = 20) -> str:
    if not positions:
        return "No open trades."

    lines = [f"Open trades ({len(positions)}):"]
    for pos in positions[:max_items]:
        ticket = pos.get("ticket", "-")
        symbol = pos.get("symbol", "-")
        side = _position_type_label(pos.get("type"))
        volume = _fmt_volume(pos.get("volume"))
        open_price = _fmt_price(pos.get("price_open"))
        sl = _fmt_price(pos.get("sl"))
        tp = _fmt_price(pos.get("tp"))
        lines.append(
            f"#{ticket} {symbol} {side} {volume} lot\n"
            f"  Open: {open_price} | SL: {sl} | TP: {tp}"
        )

    if len(positions) > max_items:
        lines.append(f"... and {len(positions) - max_items} more")
    return "\n\n".join(lines)


def _format_trim_preview(positions: list[Dict[str, Any]], max_items: int = 20) -> str:
    if not positions:
        return "No open trades to trim."

    lines = [f"Trim candidates ({len(positions)}):"]
    for pos in positions[:max_items]:
        lines.append(
            "\n".join(
                [
                    f"Ticket ID: {pos.get('ticket', '-')}",
                    f"Symbol: {pos.get('symbol', '-')}",
                    f"Volume: {_fmt_volume(pos.get('volume'))}",
                    f"TP: {_fmt_price(pos.get('tp'))}",
                    f"Open: {_fmt_price(pos.get('price_open'))}",
                    f"SL: {_fmt_price(pos.get('sl'))}",
                ]
            )
        )

    if len(positions) > max_items:
        lines.append(f"... and {len(positions) - max_items} more")
    return "\n\n".join(lines)


def _format_preview(
    symbol: str,
    action: str,
    range_low: float,
    range_high: float,
    stop_loss: float,
    take_profits: list[float],
    risk_usd: int,
    lot_size_per_order: float,
) -> str:
    tp_text = ",".join(str(int(tp)) if float(tp).is_integer() else f"{tp}" for tp in take_profits)
    return (
        "Please confirm execution:\n\n"
        f"Asset: {symbol}\n"
        f"Action: {action}\n"
        f"Market range: [{range_low:g}, {range_high:g}]\n"
        f"Stop Loss: {stop_loss:g}\n"
        f"Take Profits: [{tp_text}]\n"
        f"Provided USD Risk: {risk_usd}\n"
        f"Lot Size per Order: {lot_size_per_order:.2f}"
    )


def _parse_symbol_side_risk_args(args: list[str]) -> tuple[str, str, int]:
    normalized_args = [part.strip() for part in args if part and part.strip()]
    if len(normalized_args) < 3:
        raise ValueError("Usage: /exdefault or /exnow <symbol> <buy|sell> <usd_integer> (same format for /calculate)")

    # Be lenient with accidental extra words: only first 3 tokens are used.
    symbol_token, side_token, risk_token = normalized_args[:3]

    symbol = _normalize_symbol(symbol_token)
    if not symbol:
        raise ValueError("Invalid symbol.")
    side = side_token.strip().lower()
    if side not in ("buy", "sell"):
        raise ValueError("Side must be buy or sell.")
    try:
        # Accept accidental commas/underscores in numbers, e.g. 1,000 or 1_000.
        cleaned_risk = risk_token.replace(",", "").replace("_", "")
        risk_usd = int(cleaned_risk)
    except Exception as exc:
        raise ValueError("Risk must be a positive integer.") from exc
    if risk_usd <= 0:
        raise ValueError("Risk must be a positive integer.")
    return symbol, side, risk_usd


def _compute_exdefault_plan(symbol: str, side: str, risk_usd: int) -> Dict[str, Any]:
    cfg = EXDEFAULT_ASSET_CONFIG.get(symbol)
    if not cfg:
        raise ValueError(
            f"Unsupported symbol: {symbol}. Supported: {', '.join(sorted(EXDEFAULT_ASSET_CONFIG.keys()))}"
        )

    symbol_info = mt5_client.get_symbol_info(symbol)
    point_size = symbol_info.get("point")
    contract_size = symbol_info.get("trade_contract_size", 100000)
    try:
        point_size = float(point_size)
        contract_size = float(contract_size)
    except Exception as exc:
        raise ValueError("Invalid symbol info for point size/contract size.") from exc
    if point_size <= 0 or contract_size <= 0:
        raise ValueError("Point size and contract size must be positive.")

    tick = mt5_client.get_symbol_info_tick(symbol)
    if not _is_live_tick(tick):
        raise ValueError("No live bid/ask for this symbol.")
    bid = float(tick.get("bid"))
    ask = float(tick.get("ask"))
    entry = ask if side == "buy" else bid

    sl_points = float(cfg["sl_points"])
    tp1_rr = float(cfg["tp1_rr"])
    tp2_rr = float(cfg["tp2_rr"])
    sl_distance = sl_points * point_size
    if sl_distance <= 0:
        raise ValueError("Computed SL distance must be positive.")

    if side == "buy":
        sl = entry - sl_distance
        tp1 = entry + (sl_distance * tp1_rr)
        tp2 = entry + (sl_distance * tp2_rr)
    else:
        sl = entry + sl_distance
        tp1 = entry - (sl_distance * tp1_rr)
        tp2 = entry - (sl_distance * tp2_rr)

    risk_result = calculate_lot_distribution(
        risk_usd=risk_usd,
        range_low=entry,
        range_high=entry,
        stop_loss=sl,
        tp_count=2,
        contract_size=contract_size,
    )
    per_order_lot = float(risk_result.rounded_per_tp_lot)

    digits = 5
    try:
        digits = int(symbol_info.get("digits", 5))
    except Exception:
        pass

    return {
        "symbol": symbol,
        "side": side,
        "risk_usd": risk_usd,
        "entry": entry,
        "bid": bid,
        "ask": ask,
        "sl_points": sl_points,
        "point_size": point_size,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp_rr": [tp1_rr, tp2_rr],
        "lot_per_order": per_order_lot,
        "lots_per_tp": [per_order_lot, per_order_lot],
        "digits": digits,
    }


def _format_exdefault_plan(plan: Dict[str, Any], include_prompt: bool = False) -> str:
    digits = int(plan.get("digits", 5))
    symbol = str(plan["symbol"])
    side = str(plan["side"]).upper()
    lines = [
        "Execution plan (market now):",
        f"Asset: {symbol}",
        f"Action: {side}",
        f"Risk (USD): {plan['risk_usd']}",
        f"Current market (bid/ask): {_fmt_price(plan['bid'], digits)} / {_fmt_price(plan['ask'], digits)}",
        f"Entry used: {_fmt_price(plan['entry'], digits)}",
        f"SL points: {int(plan['sl_points']) if float(plan['sl_points']).is_integer() else plan['sl_points']}",
        f"Point size: {_fmt_price(plan['point_size'], digits)}",
        "",
        "Order 1:",
        f"- Lot: {_fmt_volume(plan['lot_per_order'])}",
        f"- SL: {_fmt_price(plan['sl'], digits)}",
        f"- TP: {_fmt_price(plan['tp1'], digits)} (RR {plan['tp_rr'][0]:g})",
        "",
        "Order 2:",
        f"- Lot: {_fmt_volume(plan['lot_per_order'])}",
        f"- SL: {_fmt_price(plan['sl'], digits)}",
        f"- TP: {_fmt_price(plan['tp2'], digits)} (RR {plan['tp_rr'][1]:g})",
    ]
    if include_prompt:
        lines.extend(["", "Press Execute to place both market orders, or Cancel to abort."])
    return "\n".join(lines)


def _compute_market_split_plan(symbol: str, side: str, risk_usd: int) -> Dict[str, Any]:
    symbol_info = mt5_client.get_symbol_info(symbol)
    contract_size = symbol_info.get("trade_contract_size", 100000)
    point_size = symbol_info.get("point", 0.0)
    try:
        contract_size = float(contract_size)
        point_size = float(point_size)
    except Exception as exc:
        raise ValueError("Invalid symbol info for contract size/point size.") from exc
    if contract_size <= 0:
        raise ValueError("Contract size must be positive.")

    tick = mt5_client.get_symbol_info_tick(symbol)
    if not _is_live_tick(tick):
        raise ValueError("No live bid/ask for this symbol.")
    bid = float(tick.get("bid"))
    ask = float(tick.get("ask"))
    entry = ask if side == "buy" else bid

    # Approximate risk-per-lot from one point move for parity with existing risk utility.
    synthetic_sl = entry - point_size if side == "buy" else entry + point_size
    risk_result = calculate_lot_distribution(
        risk_usd=risk_usd,
        range_low=entry,
        range_high=entry,
        stop_loss=synthetic_sl,
        tp_count=2,
        contract_size=contract_size,
    )

    digits = 5
    try:
        digits = int(symbol_info.get("digits", 5))
    except Exception:
        pass

    return {
        "symbol": symbol,
        "side": side,
        "risk_usd": risk_usd,
        "entry": entry,
        "bid": bid,
        "ask": ask,
        "digits": digits,
        "lot_total": float(risk_result.rounded_per_tp_lot) * 2.0,
        "lot_per_order": float(risk_result.rounded_per_tp_lot),
    }


def _format_market_split_plan(plan: Dict[str, Any]) -> str:
    digits = int(plan.get("digits", 5))
    return (
        "Please confirm execution (market without SL/TP):\n\n"
        f"Asset: {plan['symbol']}\n"
        f"Action: {str(plan['side']).upper()}\n"
        f"Risk (USD): {plan['risk_usd']}\n"
        f"Current market (bid/ask): {_fmt_price(plan['bid'], digits)} / {_fmt_price(plan['ask'], digits)}\n"
        f"Entry used: {_fmt_price(plan['entry'], digits)}\n"
        f"Total lot: {_fmt_volume(plan['lot_total'])}\n"
        f"Split orders: 2 x {_fmt_volume(plan['lot_per_order'])}\n\n"
        "Press Execute to place both market orders, or Cancel to abort."
    )


def _fmt_sltp_or_none(value: Any, digits: int = 5) -> str:
    try:
        num = float(value)
    except Exception:
        return "None"
    if num <= 0:
        return "None"
    return _fmt_price(num, digits)


def _select_tp_targets(signal_tps: list[float], current_price: float, action: str, count: int) -> list[float]:
    if count <= 0:
        return []
    tps = [float(v) for v in signal_tps]
    if action.upper() == "SELL":
        eligible = sorted([tp for tp in tps if tp < current_price], reverse=True)
        if not eligible:
            eligible = sorted(tps, reverse=True)
    else:
        eligible = sorted([tp for tp in tps if tp > current_price])
        if not eligible:
            eligible = sorted(tps)

    selected: list[float] = []
    for i in range(count):
        if i < len(eligible):
            selected.append(eligible[i])
        else:
            # Safer fallback: nearest achievable TP bucket.
            selected.append(eligible[0])
    return selected


def _extract_signal_text(update: Update) -> Optional[str]:
    if not update.message:
        return None

    if update.message.reply_to_message:
        src = update.message.reply_to_message
        return src.text or src.caption

    if update.message.text:
        parts = update.message.text.split(None, 2)
        if len(parts) >= 3:
            return parts[2]
    return None


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_if_not_owner(update):
        return
    await update.message.reply_text("Telegram MT5 bot is online.")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_if_not_owner(update):
        return
    try:
        health = mt5_client.get_mt5_health()
        reply = (
            f"status={health.get('status')}\n"
            f"mt5_initialized={health.get('mt5_initialized')}\n"
            f"terminal_connected={health.get('terminal_connected')}\n"
            f"trade_allowed={health.get('trade_allowed')}\n"
            f"last_error={health.get('last_error')}"
        )
        await update.message.reply_text(reply)
    except Exception as exc:
        logger.exception("Status command failed")
        await update.message.reply_text(f"Failed to fetch status: {exc}")


async def account_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_if_not_owner(update):
        return
    try:
        account = mt5_client.get_account_info()
        if not isinstance(account, dict):
            await update.message.reply_text("Failed to fetch account info: invalid API response.")
            return

        if account.get("status") == "error":
            await update.message.reply_text(
                f"Failed to fetch account info: {account.get('reason', 'unknown error')}"
            )
            return

        lines = [
            "MT5 Account",
            f"ID: {account.get('login', '-')}",
            f"Name: {account.get('name', '-')}",
            f"Server: {account.get('server', '-')}",
        ]

        currency = account.get("currency")
        if currency:
            lines.append(f"Currency: {currency}")

        # These fields may not be returned by older MT5 API versions.
        lines.extend(
            [
                f"Balance: {_fmt_money(account.get('balance'))}",
                f"Equity: {_fmt_money(account.get('equity'))}",
                f"Free Margin: {_fmt_money(account.get('margin_free'))}",
                f"Margin: {_fmt_money(account.get('margin'))}",
                f"Profit: {_fmt_money(account.get('profit'))}",
            ]
        )

        await update.message.reply_text("\n".join(lines))
    except Exception as exc:
        logger.exception("Account command failed")
        await update.message.reply_text(f"Failed to fetch account info: {exc}")


async def check_strategy_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_if_not_owner(update):
        return
    try:
        strategy = _latest_strategy_store(context)
        if not strategy:
            await update.message.reply_text("No stored strategy yet. Forward a signal first.")
            return

        symbol = strategy.get("symbol")
        range_low = strategy.get("range_low")
        range_high = strategy.get("range_high")
        if not symbol:
            await update.message.reply_text("Stored strategy is incomplete. Forward a new signal.")
            return

        tick = mt5_client.get_symbol_info_tick(symbol)
        bid = tick.get("bid")
        ask = tick.get("ask")
        last = tick.get("last")
        within_range = _is_bid_within_range(bid, range_low, range_high)

        await update.message.reply_text(
            f"Signal for asset: {symbol}\n"
            f"Within range: {_format_within_range_label(within_range)}\n"
            f"Market range: [{range_low:g}, {range_high:g}]\n"
            f"Current market price (bid/ask/last): {bid} / {ask} / {last}"
        )
    except Exception as exc:
        logger.exception("Check strategy command failed")
        await update.message.reply_text(f"Failed to check strategy: {exc}")


async def trades_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_if_not_owner(update):
        return
    try:
        positions = mt5_client.get_positions()
        if not positions:
            await update.message.reply_text("No open trades.")
            return

        lines = []
        for pos in positions:
            ticket = pos.get("ticket", "-")
            symbol = pos.get("symbol", "-")
            side = _position_type_label(pos.get("type"))
            volume = _fmt_volume(pos.get("volume"))
            open_price = _fmt_price(pos.get("price_open"))
            sl = _fmt_price(pos.get("sl"))
            tp = _fmt_price(pos.get("tp"))
            lines.append(
                f"#{ticket} {symbol} {side} {volume} lot\n"
                f"  Open: {open_price} | SL: {sl} | TP: {tp}"
            )
        await update.message.reply_text("\n\n".join(lines[:30]))
    except Exception as exc:
        logger.exception("Trades command failed")
        await update.message.reply_text(f"Failed to fetch trades: {exc}")


async def trim_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_if_not_owner(update):
        return
    try:
        positions = mt5_client.get_positions()
        if not positions:
            await update.message.reply_text("No open trades to trim.")
            return

        request_id = uuid.uuid4().hex[:10]
        preview_text = (
            f"{_format_trim_preview(positions)}\n\n"
            "Execute trim for all listed trades?\n"
            "Press Execute to continue or Cancel to abort."
        )
        store = _pending_trim_store(context)
        store[request_id] = {"preview_text": preview_text}

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Execute", callback_data=f"trim_confirm:{request_id}"),
                    InlineKeyboardButton("Cancel", callback_data=f"trim_cancel:{request_id}"),
                ]
            ]
        )
        await update.message.reply_text(preview_text, reply_markup=keyboard)
    except Exception as exc:
        logger.exception("Trim command failed")
        await update.message.reply_text(f"Failed to trim trades: {exc}")


async def closeall_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_if_not_owner(update):
        return
    try:
        positions = mt5_client.get_positions()
        if not positions:
            await update.message.reply_text("No open trades to close.")
            return

        request_id = uuid.uuid4().hex[:10]
        preview_text = (
            f"{_format_positions_preview(positions)}\n\n"
            "Close all open trades?\n"
            "Press Confirm to execute or Cancel to abort."
        )
        store = _pending_closeall_store(context)
        store[request_id] = {"preview_text": preview_text}

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Confirm", callback_data=f"closeall_confirm:{request_id}"),
                    InlineKeyboardButton("Cancel", callback_data=f"closeall_cancel:{request_id}"),
                ]
            ]
        )
        await update.message.reply_text(preview_text, reply_markup=keyboard)
    except Exception as exc:
        logger.exception("Closeall command failed")
        await update.message.reply_text(f"Failed to close all trades: {exc}")


async def execute_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_if_not_owner(update):
        return

    if not update.message:
        return

    if not context.args:
        await update.message.reply_text("Usage: /execute <usd_integer> and reply to a signal message.")
        return

    try:
        risk_usd = int(context.args[0])
        if risk_usd <= 0:
            raise ValueError("risk must be > 0")
    except Exception:
        await update.message.reply_text("`/execute` requires a positive integer risk amount.")
        return

    signal_text = _extract_signal_text(update)
    if not signal_text:
        await update.message.reply_text("No signal text found. Reply to a signal message or provide text inline.")
        return

    try:
        signal = parse_signal_text(signal_text)
        symbol_info = mt5_client.get_symbol_info(signal.symbol)
        contract_size = float(symbol_info.get("trade_contract_size", 100000))

        risk_result = calculate_lot_distribution(
            risk_usd=risk_usd,
            range_low=signal.range_low,
            range_high=signal.range_high,
            stop_loss=signal.stop_loss,
            tp_count=len(signal.take_profits),
            contract_size=contract_size,
        )

        request_id = uuid.uuid4().hex[:10]
        store = _pending_store(context)
        store[request_id] = {
            "symbol": signal.symbol,
            "action": signal.action,
            "stop_loss": signal.stop_loss,
            "take_profits": signal.take_profits,
            "lots_per_tp": risk_result.lots_per_tp,
            "risk_usd": risk_usd,
            "range_low": signal.range_low,
            "range_high": signal.range_high,
            "preview_text": _format_preview(
                symbol=signal.symbol,
                action=signal.action,
                range_low=signal.range_low,
                range_high=signal.range_high,
                stop_loss=signal.stop_loss,
                take_profits=signal.take_profits,
                risk_usd=risk_usd,
                lot_size_per_order=risk_result.rounded_per_tp_lot,
            ),
        }

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Execute", callback_data=f"exec_confirm:{request_id}"),
                    InlineKeyboardButton("Cancel", callback_data=f"exec_cancel:{request_id}"),
                ]
            ]
        )
        await update.message.reply_text(
            store[request_id]["preview_text"],
            reply_markup=keyboard,
        )
    except Exception as exc:
        logger.exception("Execute command failed")
        await update.message.reply_text(f"Execute failed: {exc}")


async def exdefault_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_if_not_owner(update):
        return
    if not update.message:
        return
    try:
        symbol, side, risk_usd = _parse_symbol_side_risk_args(context.args)
        plan = _compute_exdefault_plan(symbol=symbol, side=side, risk_usd=risk_usd)

        request_id = uuid.uuid4().hex[:10]
        store = _pending_exdefault_store(context)
        store[request_id] = {
            "symbol": plan["symbol"],
            "action": plan["side"],
            "stop_loss": float(plan["sl"]),
            "take_profits": [float(plan["tp1"]), float(plan["tp2"])],
            "lots_per_tp": [float(v) for v in plan["lots_per_tp"]],
            "preview_text": _format_exdefault_plan(plan, include_prompt=True),
        }

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Execute", callback_data=f"exdefault_confirm:{request_id}"),
                    InlineKeyboardButton("Cancel", callback_data=f"exdefault_cancel:{request_id}"),
                ]
            ]
        )
        await update.message.reply_text(store[request_id]["preview_text"], reply_markup=keyboard)
    except Exception as exc:
        logger.exception("Exdefault command failed")
        await update.message.reply_text(f"Exdefault failed: {exc}")


async def calculate_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_if_not_owner(update):
        return
    if not update.message:
        return
    try:
        symbol, side, risk_usd = _parse_symbol_side_risk_args(context.args)
        plan = _compute_exdefault_plan(symbol=symbol, side=side, risk_usd=risk_usd)
        await update.message.reply_text(_format_exdefault_plan(plan, include_prompt=False))
    except Exception as exc:
        logger.exception("Calculate command failed")
        await update.message.reply_text(f"Calculate failed: {exc}")


async def exnow_market_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_if_not_owner(update):
        return
    if not update.message:
        return
    try:
        symbol, side, risk_usd = _parse_symbol_side_risk_args(context.args)
        plan = _compute_market_split_plan(symbol=symbol, side=side, risk_usd=risk_usd)

        request_id = uuid.uuid4().hex[:10]
        store = _pending_exnow_market_store(context)
        store[request_id] = {
            "symbol": plan["symbol"],
            "action": plan["side"],
            "lots": [float(plan["lot_per_order"]), float(plan["lot_per_order"])],
            "preview_text": _format_market_split_plan(plan),
        }

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Execute", callback_data=f"exnowm_confirm:{request_id}"),
                    InlineKeyboardButton("Cancel", callback_data=f"exnowm_cancel:{request_id}"),
                ]
            ]
        )
        await update.message.reply_text(store[request_id]["preview_text"], reply_markup=keyboard)
    except Exception as exc:
        logger.exception("Exnow market command failed")
        await update.message.reply_text(f"Exnow failed: {exc}")


async def edit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_if_not_owner(update):
        return
    if not update.message:
        return

    signal_text = _extract_signal_text(update)
    if not signal_text:
        await update.message.reply_text("No signal text found. Reply to a signal message containing SL/TP.")
        return

    try:
        signal = parse_signal_text(signal_text)
        symbol = _normalize_symbol(signal.symbol)
        positions = mt5_client.get_positions()
        symbol_positions = [p for p in positions if _normalize_symbol(str(p.get("symbol", ""))) == symbol]
        if not symbol_positions:
            await update.message.reply_text(f"No open trades found for {symbol}.")
            return

        tick = mt5_client.get_symbol_info_tick(symbol)
        if not _is_live_tick(tick):
            raise ValueError("No live bid/ask for this symbol.")
        current_price = float(tick.get("bid")) if signal.action.upper() == "SELL" else float(tick.get("ask"))
        tp_targets = _select_tp_targets(signal.take_profits, current_price, signal.action, len(symbol_positions))

        symbol_info = mt5_client.get_symbol_info(symbol)
        digits = int(symbol_info.get("digits", 5))

        lines = [
            f"Edit plan for {symbol} ({signal.action.upper()}):",
            f"Signal SL: {_fmt_price(signal.stop_loss, digits)}",
            f"Signal TPs: {', '.join(_fmt_price(tp, digits) for tp in signal.take_profits)}",
            f"Current market ref: {_fmt_price(current_price, digits)}",
            "",
        ]
        updates: list[Dict[str, Any]] = []
        for idx, pos in enumerate(symbol_positions, start=1):
            new_tp = float(tp_targets[idx - 1])
            update_item = {
                "ticket": int(pos["ticket"]),
                "symbol": symbol,
                "orig_sl": pos.get("sl"),
                "orig_tp": pos.get("tp"),
                "new_sl": float(signal.stop_loss),
                "new_tp": new_tp,
                "digits": digits,
            }
            updates.append(update_item)
            lines.extend(
                [
                    f"Order {idx} (#{update_item['ticket']}):",
                    f"SL: {_fmt_sltp_or_none(update_item['orig_sl'], digits)} -> {_fmt_price(update_item['new_sl'], digits)}",
                    f"TP: {_fmt_sltp_or_none(update_item['orig_tp'], digits)} -> {_fmt_price(update_item['new_tp'], digits)}",
                    "",
                ]
            )

        lines.append("Press Execute to apply these SL/TP edits, or Cancel to abort.")
        preview_text = "\n".join(lines)

        request_id = uuid.uuid4().hex[:10]
        store = _pending_edit_store(context)
        store[request_id] = {"preview_text": preview_text, "updates": updates}

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Execute", callback_data=f"edit_confirm:{request_id}"),
                    InlineKeyboardButton("Cancel", callback_data=f"edit_cancel:{request_id}"),
                ]
            ]
        )
        await update.message.reply_text(preview_text, reply_markup=keyboard)
    except Exception as exc:
        logger.exception("Edit command failed")
        await update.message.reply_text(f"Edit failed: {exc}")


async def execute_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return

    await query.answer()

    user = update.effective_user
    if not user or OWNER_ID is None or user.id != OWNER_ID:
        await query.edit_message_text("Unauthorized user.")
        return

    callback_data = query.data or ""
    if ":" not in callback_data:
        await query.edit_message_text("Invalid action.")
        return

    action, request_id = callback_data.split(":", 1)
    store = _pending_store(context)
    pending = store.get(request_id)
    if action == "exec_cancel":
        # Keep the existing details visible and just remove buttons + append cancellation note.
        base_text = (query.message.text if query.message else "") or (
            pending.get("preview_text") if pending else "Please confirm execution:"
        )
        if "Execution cancelled." not in base_text:
            base_text = f"{base_text}\n\nExecution cancelled."
        store.pop(request_id, None)
        await query.edit_message_text(base_text)
        return

    if not pending:
        await query.edit_message_text("This request is no longer pending.")
        return

    if action != "exec_confirm":
        await query.edit_message_text("Invalid action.")
        return

    try:
        results = []
        tps = pending["take_profits"]
        lots = pending["lots_per_tp"]
        for i, tp in enumerate(tps):
            lot = float(lots[i])
            result = mt5_client.place_order(
                symbol=pending["symbol"],
                volume=lot,
                side=pending["action"],
                sl=float(pending["stop_loss"]),
                tp=float(tp),
                comment=f"telegram-execute-tp{i+1}",
            )
            results.append({"tp": tp, "lot": lot, "response": result})

        success = sum(1 for r in results if isinstance(r.get("response"), dict) and not r["response"].get("error"))
        failed = len(results) - success

        await query.edit_message_text(
            "Execute complete.\n"
            f"symbol={pending['symbol']} action={pending['action']}\n"
            f"orders_success={success} orders_failed={failed}"
        )
    except requests.exceptions.HTTPError as exc:
        response_text = ""
        if exc.response is not None:
            try:
                response_text = exc.response.text
            except Exception:
                response_text = ""
        logger.exception("Execute confirmation failed with HTTP error")
        details = f"\nDetails: {response_text}" if response_text else ""
        await query.edit_message_text(f"Execute failed: {exc}{details}")
    except Exception as exc:
        logger.exception("Execute confirmation failed")
        await query.edit_message_text(f"Execute failed: {exc}")
    finally:
        store.pop(request_id, None)


async def exdefault_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return

    await query.answer()

    user = update.effective_user
    if not user or OWNER_ID is None or user.id != OWNER_ID:
        await query.edit_message_text("Unauthorized user.")
        return

    callback_data = query.data or ""
    if ":" not in callback_data:
        await query.edit_message_text("Invalid action.")
        return

    action, request_id = callback_data.split(":", 1)
    store = _pending_exdefault_store(context)
    pending = store.get(request_id)
    if action == "exdefault_cancel":
        base_text = (query.message.text if query.message else "") or (
            pending.get("preview_text") if pending else "Execution plan (market now):"
        )
        if "Execution cancelled." not in base_text:
            base_text = f"{base_text}\n\nExecution cancelled."
        store.pop(request_id, None)
        await query.edit_message_text(base_text)
        return

    if not pending:
        await query.edit_message_text("This request is no longer pending.")
        return

    if action != "exdefault_confirm":
        await query.edit_message_text("Invalid action.")
        return

    try:
        results = []
        tps = pending["take_profits"]
        lots = pending["lots_per_tp"]
        for i, tp in enumerate(tps):
            lot = float(lots[i])
            result = mt5_client.place_order(
                symbol=pending["symbol"],
                volume=lot,
                side=pending["action"],
                sl=float(pending["stop_loss"]),
                tp=float(tp),
                comment=f"telegram-exdefault-tp{i+1}",
            )
            results.append({"tp": tp, "lot": lot, "response": result})

        success = sum(1 for r in results if isinstance(r.get("response"), dict) and not r["response"].get("error"))
        failed = len(results) - success

        await query.edit_message_text(
            "Exdefault complete.\n"
            f"symbol={pending['symbol']} action={pending['action']}\n"
            f"orders_success={success} orders_failed={failed}"
        )
    except requests.exceptions.HTTPError as exc:
        response_text = ""
        if exc.response is not None:
            try:
                response_text = exc.response.text
            except Exception:
                response_text = ""
        logger.exception("Exdefault confirmation failed with HTTP error")
        details = f"\nDetails: {response_text}" if response_text else ""
        await query.edit_message_text(f"Exdefault failed: {exc}{details}")
    except Exception as exc:
        logger.exception("Exdefault confirmation failed")
        await query.edit_message_text(f"Exdefault failed: {exc}")
    finally:
        store.pop(request_id, None)


async def exnow_market_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return

    await query.answer()

    user = update.effective_user
    if not user or OWNER_ID is None or user.id != OWNER_ID:
        await query.edit_message_text("Unauthorized user.")
        return

    callback_data = query.data or ""
    if ":" not in callback_data:
        await query.edit_message_text("Invalid action.")
        return

    action, request_id = callback_data.split(":", 1)
    store = _pending_exnow_market_store(context)
    pending = store.get(request_id)
    if action == "exnowm_cancel":
        base_text = (query.message.text if query.message else "") or (
            pending.get("preview_text") if pending else "Please confirm execution (market without SL/TP):"
        )
        if "Execution cancelled." not in base_text:
            base_text = f"{base_text}\n\nExecution cancelled."
        store.pop(request_id, None)
        await query.edit_message_text(base_text)
        return

    if not pending:
        await query.edit_message_text("This request is no longer pending.")
        return

    if action != "exnowm_confirm":
        await query.edit_message_text("Invalid action.")
        return

    try:
        results = []
        for i, lot in enumerate(pending["lots"]):
            result = mt5_client.place_order(
                symbol=pending["symbol"],
                volume=float(lot),
                side=pending["action"],
                comment=f"telegram-exnow-market-{i+1}",
            )
            results.append(result)

        success = sum(1 for r in results if isinstance(r, dict) and not r.get("error"))
        failed = len(results) - success
        await query.edit_message_text(
            "Exnow complete.\n"
            f"symbol={pending['symbol']} action={pending['action']}\n"
            "mode=market_no_sltp\n"
            f"orders_success={success} orders_failed={failed}"
        )
    except requests.exceptions.HTTPError as exc:
        response_text = ""
        if exc.response is not None:
            try:
                response_text = exc.response.text
            except Exception:
                response_text = ""
        details = f"\nDetails: {response_text}" if response_text else ""
        await query.edit_message_text(f"Exnow failed: {exc}{details}")
    except Exception as exc:
        await query.edit_message_text(f"Exnow failed: {exc}")
    finally:
        store.pop(request_id, None)


async def edit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return

    await query.answer()

    user = update.effective_user
    if not user or OWNER_ID is None or user.id != OWNER_ID:
        await query.edit_message_text("Unauthorized user.")
        return

    callback_data = query.data or ""
    if ":" not in callback_data:
        await query.edit_message_text("Invalid action.")
        return

    action, request_id = callback_data.split(":", 1)
    store = _pending_edit_store(context)
    pending = store.get(request_id)
    if action == "edit_cancel":
        base_text = (query.message.text if query.message else "") or (
            pending.get("preview_text") if pending else "Edit plan"
        )
        if "Execution cancelled." not in base_text:
            base_text = f"{base_text}\n\nExecution cancelled."
        store.pop(request_id, None)
        await query.edit_message_text(base_text)
        return

    if not pending:
        await query.edit_message_text("This request is no longer pending.")
        return

    if action != "edit_confirm":
        await query.edit_message_text("Invalid action.")
        return

    try:
        updates = pending.get("updates", [])
        success = 0
        failed = 0
        for item in updates:
            try:
                mt5_client.modify_sl_tp(
                    ticket=int(item["ticket"]),
                    sl=float(item["new_sl"]),
                    tp=float(item["new_tp"]),
                )
                success += 1
            except Exception:
                failed += 1
        await query.edit_message_text(f"{pending.get('preview_text', 'Edit plan')}\n\nEdit complete. updated={success} failed={failed}")
    except Exception as exc:
        await query.edit_message_text(f"Edit failed: {exc}")
    finally:
        store.pop(request_id, None)


async def closeall_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return

    await query.answer()

    user = update.effective_user
    if not user or OWNER_ID is None or user.id != OWNER_ID:
        await query.edit_message_text("Unauthorized user.")
        return

    callback_data = query.data or ""
    if ":" not in callback_data:
        await query.edit_message_text("Invalid action.")
        return

    action, request_id = callback_data.split(":", 1)
    store = _pending_closeall_store(context)
    pending = store.get(request_id)
    if not pending:
        await query.edit_message_text("This request is no longer pending.")
        return

    base_text = (query.message.text if query.message else "") or pending.get("preview_text", "Close all open trades?")

    if action == "closeall_cancel":
        if "Execution cancelled." not in base_text:
            base_text = f"{base_text}\n\nExecution cancelled."
        store.pop(request_id, None)
        await query.edit_message_text(base_text)
        return

    if action != "closeall_confirm":
        await query.edit_message_text("Invalid action.")
        return

    try:
        resp = mt5_client.close_all_positions()
        if not isinstance(resp, dict):
            result_text = "Close all failed: invalid API response."
        else:
            results = resp.get("results", [])
            closed_count = len(results) if isinstance(results, list) else 0
            message = resp.get("message", "Close all complete.")
            result_text = f"{message}\nclosed={closed_count}"
        await query.edit_message_text(f"{base_text}\n\n{result_text}")
    except Exception as exc:
        logger.exception("Closeall confirmation failed")
        await query.edit_message_text(f"{base_text}\n\nClose all failed: {exc}")
    finally:
        store.pop(request_id, None)


async def trim_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return

    await query.answer()

    user = update.effective_user
    if not user or OWNER_ID is None or user.id != OWNER_ID:
        await query.edit_message_text("Unauthorized user.")
        return

    callback_data = query.data or ""
    if ":" not in callback_data:
        await query.edit_message_text("Invalid action.")
        return

    action, request_id = callback_data.split(":", 1)
    store = _pending_trim_store(context)
    pending = store.get(request_id)
    if not pending:
        await query.edit_message_text("This request is no longer pending.")
        return

    base_text = (query.message.text if query.message else "") or pending.get("preview_text", "Trim execution request.")

    if action == "trim_cancel":
        if "Execution cancelled." not in base_text:
            base_text = f"{base_text}\n\nExecution cancelled."
        store.pop(request_id, None)
        await query.edit_message_text(base_text)
        return

    if action != "trim_confirm":
        await query.edit_message_text("Invalid action.")
        return

    try:
        positions = mt5_client.get_positions()
        if not positions:
            await query.edit_message_text(f"{base_text}\n\nNo open trades to trim.")
            return

        success = 0
        failed = 0
        for pos in positions:
            try:
                ticket = int(pos["ticket"])
                entry_price = float(pos["price_open"])
                current_tp = float(pos.get("tp") or 0.0)
                mt5_client.modify_sl_tp(ticket=ticket, sl=entry_price, tp=current_tp)
                success += 1
            except Exception:
                failed += 1

        await query.edit_message_text(f"{base_text}\n\nTrim complete. updated={success} failed={failed}")
    except Exception as exc:
        logger.exception("Trim confirmation failed")
        await query.edit_message_text(f"{base_text}\n\nTrim failed: {exc}")
    finally:
        store.pop(request_id, None)


async def forwarded_signal_probe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_if_not_owner(update):
        logger.info("forwarded_signal_probe rejected: non-owner user")
        return

    if not update.message:
        logger.info("forwarded_signal_probe skipped: no message object")
        return

    # Telegram forwarding metadata varies by client/origin type.
    # Keep this check permissive so forwarded text is still processed reliably.
    has_forward_marker = bool(
        getattr(update.message, "forward_origin", None)
        or getattr(update.message, "forward_date", None)
        or getattr(update.message, "forward_from", None)
        or getattr(update.message, "forward_from_chat", None)
    )
    if not has_forward_marker:
        return

    message_text = update.message.text or update.message.caption or ""
    if not message_text.strip():
        logger.info("forwarded_signal_probe received forwarded message without text/caption")
        await update.message.reply_text("Forwarded message has no text to parse.")
        return

    logger.info("forwarded_signal_probe received text: %s", message_text[:200])
    try:
        signal = parse_signal_text(message_text)
    except Exception:
        logger.exception("forwarded_signal_probe failed to parse signal")
        await update.message.reply_text("Could not parse forwarded signal format.")
        return

    try:
        symbol = _normalize_symbol(signal.symbol)
        tick = mt5_client.get_symbol_info_tick(symbol)
        if not _is_live_tick(tick):
            logger.warning("forwarded_signal_probe non-live tick on first attempt symbol=%s tick=%s", symbol, tick)
            # Retry once after ensuring symbol subscription on MT5 side.
            mt5_client.get_symbol_info(symbol)
            tick = mt5_client.get_symbol_info_tick(symbol)

        bid = tick.get("bid")
        ask = tick.get("ask")
        last = tick.get("last")
        within_range = _is_bid_within_range(bid, signal.range_low, signal.range_high)
        strategy_store = _latest_strategy_store(context)
        strategy_store.clear()
        strategy_store.update(
            {
                "symbol": symbol,
                "range_low": signal.range_low,
                "range_high": signal.range_high,
                "action": signal.action,
                "stop_loss": signal.stop_loss,
                "take_profits": signal.take_profits,
            }
        )
        logger.info(
            "forwarded_signal_probe tick success symbol=%s bid=%s ask=%s last=%s",
            symbol,
            bid,
            ask,
            last,
        )
        await update.message.reply_text(
            f"Signal for asset: {symbol}\n"
            f"Within range: {_format_within_range_label(within_range)}\n"
            f"Market range: [{signal.range_low:g}, {signal.range_high:g}]\n"
            f"Current market price (bid/ask/last): {bid} / {ask} / {last}\n\n"
            "Reply to this forwarded message with /execute <usd_integer> to continue."
        )
    except Exception as exc:
        logger.exception("Forwarded signal probe failed")
        await update.message.reply_text(
            f"Received signal for asset: {signal.symbol}\n"
            f"Failed to fetch current market price: {exc}"
        )


def _validate_env_or_exit() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required.")
    if OWNER_ID is None:
        raise RuntimeError("TELEGRAM_OWNER_ID is required.")


def main() -> None:
    _validate_env_or_exit()
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("account", account_command))
    app.add_handler(CommandHandler("check_strategy", check_strategy_command))
    app.add_handler(CommandHandler("execute", execute_command))
    app.add_handler(CommandHandler("exdefault", exdefault_command))
    app.add_handler(CommandHandler("exnow", exnow_market_command))
    app.add_handler(CommandHandler("calculate", calculate_command))
    app.add_handler(CommandHandler("edit", edit_command))
    app.add_handler(CommandHandler("trades", trades_command))
    app.add_handler(CommandHandler("trim", trim_command))
    app.add_handler(CommandHandler("closeall", closeall_command))
    app.add_handler(CallbackQueryHandler(execute_callback, pattern=r"^exec_(confirm|cancel):"))
    app.add_handler(CallbackQueryHandler(exdefault_callback, pattern=r"^exdefault_(confirm|cancel):"))
    app.add_handler(CallbackQueryHandler(exnow_market_callback, pattern=r"^exnowm_(confirm|cancel):"))
    app.add_handler(CallbackQueryHandler(edit_callback, pattern=r"^edit_(confirm|cancel):"))
    app.add_handler(CallbackQueryHandler(trim_callback, pattern=r"^trim_(confirm|cancel):"))
    app.add_handler(CallbackQueryHandler(closeall_callback, pattern=r"^closeall_(confirm|cancel):"))
    app.add_handler(MessageHandler((~filters.COMMAND), forwarded_signal_probe))

    logger.info("Starting telegram bot polling loop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
