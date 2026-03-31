import logging
import os
import uuid
from typing import Any, Dict, Optional

from dotenv import load_dotenv
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


def _is_owner(update: Update) -> bool:
    user = update.effective_user
    if not user or OWNER_ID is None:
        return False
    return user.id == OWNER_ID


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
            lines.append(
                f"ticket={pos.get('ticket')} symbol={pos.get('symbol')} "
                f"type={pos.get('type')} volume={pos.get('volume')} "
                f"open={pos.get('price_open')} sl={pos.get('sl')} tp={pos.get('tp')}"
            )
        await update.message.reply_text("\n".join(lines[:50]))
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

        await update.message.reply_text(f"Trim complete. updated={success} failed={failed}")
    except Exception as exc:
        logger.exception("Trim command failed")
        await update.message.reply_text(f"Failed to trim trades: {exc}")


async def closeall_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_if_not_owner(update):
        return
    try:
        resp = mt5_client.close_all_positions()
        results = resp.get("results", []) if isinstance(resp, dict) else []
        await update.message.reply_text(f"Close all complete. closed={len(results)}")
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
    if not pending:
        await query.edit_message_text("This request is no longer pending.")
        return

    if action == "exec_cancel":
        preview_text = pending.get("preview_text", "Please confirm execution:")
        store.pop(request_id, None)
        await query.edit_message_text(f"{preview_text}\n\nExecution cancelled.")
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
    except Exception as exc:
        logger.exception("Execute confirmation failed")
        await query.edit_message_text(f"Execute failed: {exc}")
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
        tick = mt5_client.get_symbol_info_tick(signal.symbol)
        bid = tick.get("bid")
        ask = tick.get("ask")
        last = tick.get("last")
        logger.info(
            "forwarded_signal_probe tick success symbol=%s bid=%s ask=%s last=%s",
            signal.symbol,
            bid,
            ask,
            last,
        )
        await update.message.reply_text(
            f"Received signal for asset: {signal.symbol}\n"
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
    app.add_handler(CommandHandler("execute", execute_command))
    app.add_handler(CommandHandler("trades", trades_command))
    app.add_handler(CommandHandler("trim", trim_command))
    app.add_handler(CommandHandler("closeall", closeall_command))
    app.add_handler(CallbackQueryHandler(execute_callback, pattern=r"^exec_(confirm|cancel):"))
    app.add_handler(MessageHandler((~filters.COMMAND), forwarded_signal_probe))

    logger.info("Starting telegram bot polling loop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
