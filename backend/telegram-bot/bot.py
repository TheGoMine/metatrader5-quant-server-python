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
LATEST_STRATEGY_KEY = "latest_strategy"


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
    app.add_handler(CommandHandler("trades", trades_command))
    app.add_handler(CommandHandler("trim", trim_command))
    app.add_handler(CommandHandler("closeall", closeall_command))
    app.add_handler(CallbackQueryHandler(execute_callback, pattern=r"^exec_(confirm|cancel):"))
    app.add_handler(CallbackQueryHandler(trim_callback, pattern=r"^trim_(confirm|cancel):"))
    app.add_handler(CallbackQueryHandler(closeall_callback, pattern=r"^closeall_(confirm|cancel):"))
    app.add_handler(MessageHandler((~filters.COMMAND), forwarded_signal_probe))

    logger.info("Starting telegram bot polling loop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
