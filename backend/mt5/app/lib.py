import MetaTrader5 as mt5
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import pandas as pd
from constants import MT5Timeframe
import logging
import os
import time

logger = logging.getLogger(__name__)


def _get_optional_login() -> Optional[int]:
    raw_login = os.getenv("MT5_LOGIN") or os.getenv("MT5_ACCOUNT")
    if not raw_login:
        return None
    try:
        return int(str(raw_login).strip())
    except ValueError:
        logger.error("Invalid MT5_LOGIN/MT5_ACCOUNT value; expected integer login id.")
        return None


def initialize_mt5_connection(retries: int = 5, delay_seconds: int = 2) -> bool:
    """
    Initialize MT5 session with explicit terminal path and retries.
    """
    terminal_path = os.getenv(
        "MT5_TERMINAL_PATH",
        r"C:\Program Files\MetaTrader 5\terminal64.exe",
    )
    login = _get_optional_login()
    password = os.getenv("MT5_PASSWORD")
    server = os.getenv("MT5_SERVER")

    for attempt in range(1, retries + 1):
        try:
            if mt5.initialize(path=terminal_path):
                # Explicit login avoids silently reusing terminal's last session
                # (often demo) when a real account is intended.
                if login is not None:
                    login_kwargs = {"login": login}
                    if password:
                        login_kwargs["password"] = password
                    if server:
                        login_kwargs["server"] = server

                    if not mt5.login(**login_kwargs):
                        error_code, error_str = mt5.last_error()
                        logger.error(
                            f"MT5 login failed (attempt={attempt}/{retries}, login={login}, "
                            f"server={server or '<default>'}, error_code={error_code}, error={error_str})"
                        )
                        mt5.shutdown()
                        if attempt < retries:
                            time.sleep(delay_seconds)
                        continue

                account_info = mt5.account_info()
                active_login = getattr(account_info, "login", None) if account_info else None
                active_server = getattr(account_info, "server", None) if account_info else None
                logger.info(
                    f"MT5 initialized successfully (attempt={attempt}, path={terminal_path}, "
                    f"active_login={active_login}, active_server={active_server})"
                )
                return True

            error_code, error_str = mt5.last_error()
            logger.error(
                f"MT5 initialize failed (attempt={attempt}/{retries}, path={terminal_path}, "
                f"error_code={error_code}, error={error_str})"
            )
        except Exception as exc:
            logger.error(f"MT5 initialize exception (attempt={attempt}/{retries}): {exc}")

        if attempt < retries:
            time.sleep(delay_seconds)

    return False

def get_timeframe(timeframe_str: str) -> MT5Timeframe:
    try:
        return MT5Timeframe[timeframe_str.upper()].value
    except KeyError:
        valid_timeframes = ', '.join([t.name for t in MT5Timeframe])
        raise ValueError(
            f"Invalid timeframe: '{timeframe_str}'. Valid options are: {valid_timeframes}."
        )


def close_position(position, deviation=20, magic=0, comment='', type_filling=mt5.ORDER_FILLING_IOC):
    if 'type' not in position or 'ticket' not in position:
        logger.error("Position dictionary missing 'type' or 'ticket' keys.")
        return None

    # To close a position, send an opposite-side market order.
    order_type_dict = {
        mt5.POSITION_TYPE_BUY: mt5.ORDER_TYPE_SELL,
        mt5.POSITION_TYPE_SELL: mt5.ORDER_TYPE_BUY,
    }

    position_type = position['type']
    if position_type not in order_type_dict:
        logger.error(f"Unknown position type: {position_type}")
        return None

    mt5.symbol_select(position['symbol'], True)
    tick = mt5.symbol_info_tick(position['symbol'])
    if tick is None:
        logger.error(f"Failed to get tick for symbol: {position['symbol']}")
        return None

    price_dict = {
        # Closing a BUY position sends a SELL order -> use Bid.
        mt5.POSITION_TYPE_BUY: tick.bid,
        # Closing a SELL position sends a BUY order -> use Ask.
        mt5.POSITION_TYPE_SELL: tick.ask,
    }

    price = price_dict[position_type]
    if price == 0.0:
        logger.error(f"Invalid price retrieved for symbol: {position['symbol']}")
        return None

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "position": position['ticket'],  # select the position you want to close
        "symbol": position['symbol'],
        "volume": position['volume'],  # FLOAT
        "type": order_type_dict[position_type],
        "price": price,
        "deviation": deviation,  # INTEGER
        "magic": magic,          # INTEGER
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": type_filling,
    }

    filling_candidates = [type_filling, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN, mt5.ORDER_FILLING_FOK]
    deduped_fillings = []
    for filling_mode in filling_candidates:
        if filling_mode not in deduped_fillings:
            deduped_fillings.append(filling_mode)

    order_result = None
    for filling_mode in deduped_fillings:
        request["type_filling"] = filling_mode
        order_result = mt5.order_send(request)
        if order_result.retcode == mt5.TRADE_RETCODE_DONE:
            break
        if order_result.retcode != mt5.TRADE_RETCODE_INVALID_FILL:
            break

    if order_result.retcode != mt5.TRADE_RETCODE_DONE:
        logger.error(f"Failed to close position {position['ticket']}: {order_result.comment}")
        return None

    logger.info(f"Position {position['ticket']} closed successfully.")
    return order_result


def close_all_positions(order_type='all', magic=None, type_filling=mt5.ORDER_FILLING_IOC):
    order_type_dict = {
        'BUY': mt5.ORDER_TYPE_BUY,
        'SELL': mt5.ORDER_TYPE_SELL
    }

    if mt5.positions_total() > 0:
        positions = mt5.positions_get()
        if positions is None:
            logger.error("Failed to retrieve positions.")
            return []

        positions_data = [pos._asdict() for pos in positions]
        positions_df = pd.DataFrame(positions_data)

        # Filtering by magic if specified
        if magic is not None:
            positions_df = positions_df[positions_df['magic'] == magic]

        # Filtering by order_type if not 'all'
        if order_type != 'all':
            if order_type not in order_type_dict:
                logger.error(f"Invalid order_type: {order_type}. Must be 'BUY', 'SELL', or 'all'.")
                return []
            positions_df = positions_df[positions_df['type'] == order_type_dict[order_type]]

        if positions_df.empty:
            logger.error('No open positions matching the criteria.')
            return []

        results = []
        for _, position in positions_df.iterrows():
            order_result = close_position(position, type_filling=type_filling)
            if order_result:
                results.append(order_result)
            else:
                logger.error(f"Failed to close position {position['ticket']}.")
        
        return results
    else:
        logger.error("No open positions to close.")
        return []

def get_positions(magic=None):
    # First check if MT5 is initialized
    if not initialize_mt5_connection(retries=2, delay_seconds=1):
        error_code, error_str = mt5.last_error()
        logger.error(f"Failed to initialize MT5 before get_positions: {error_code} {error_str}")
        return pd.DataFrame()

    total_positions = mt5.positions_total()
    if total_positions is None:
        logger.error("Failed to get positions total.")
        return pd.DataFrame()

    if total_positions > 0:
        positions = mt5.positions_get()
        if positions is None:
            logger.error("Failed to retrieve positions.")
            return pd.DataFrame()

        positions_data = [pos._asdict() for pos in positions]
        positions_df = pd.DataFrame(positions_data)

        if magic is not None:
            positions_df = positions_df[positions_df['magic'] == magic]

        return positions_df
    else:
        return pd.DataFrame(columns=['ticket', 'time', 'time_msc', 'time_update', 'time_update_msc', 'type',
                                   'magic', 'identifier', 'reason', 'volume', 'price_open', 'sl', 'tp',
                                   'price_current', 'swap', 'profit', 'symbol', 'comment', 'external_id'])
    

def get_deal_from_ticket(ticket, from_date=None, to_date=None):
    if not isinstance(ticket, int):
        logger.error("Ticket must be an integer.")
        return None

    # Define default date range if not provided
    if from_date is None or to_date is None:
        to_date = datetime.now(mt5.TIMEZONE)
        from_date = to_date - timedelta(minutes=15)  # Adjust based on polling interval

    # Convert datetime to MT5 time (integer)
    from_timestamp = int(from_date.timestamp())
    to_timestamp = int(to_date.timestamp())

    # Retrieve deals using the specified date range and position
    deals = mt5.history_deals_get(from_timestamp, to_timestamp, position=ticket)
    if not deals:
        logger.error(f"No deal history found for position ticket {ticket} between {from_date} and {to_date}.")
        return None

    # Convert deals to a DataFrame for easier processing
    deals_df = pd.DataFrame([deal._asdict() for deal in deals])

    # Optional: Verify that all deals belong to the same symbol
    if not deals_df.empty and not all(deal == deals_df['symbol'].iloc[0] for deal in deals_df['symbol']):
        logger.error(f"Inconsistent symbols found in deals for position ticket {ticket}.")
        return None

    # Extract relevant information
    if not deals_df.empty:
        deal_details = {
            'ticket': ticket,
            'symbol': deals_df['symbol'].iloc[0],
            'type': 'BUY' if deals_df['type'].iloc[0] == 'DEAL_TYPE_BUY' else 'SELL',
            'volume': deals_df['volume'].sum(),
            'open_time': datetime.fromtimestamp(deals_df['time'].min(), tz=mt5.TIMEZONE),
            'close_time': datetime.fromtimestamp(deals_df['time'].max(), tz=mt5.TIMEZONE),
            'open_price': deals_df['price'].iloc[0],
            'close_price': deals_df['price'].iloc[-1],
            'profit': deals_df['profit'].sum(),
            'commission': deals_df['commission'].sum(),
            'swap': deals_df['swap'].sum(),
            'comment': deals_df['comment'].iloc[-1]  # Use the last comment if multiple
        }
        return deal_details
    else:
        return None


def get_order_from_ticket(ticket):
    if not isinstance(ticket, int):
        logger.error("Ticket must be an integer.")
        return None

    # Get the order history
    order = mt5.history_orders_get(ticket=ticket)
    if order is None or len(order) == 0:
        logger.error(f"No order history found for ticket {ticket}")
        return None

    # Convert order to a dictionary
    order_dict = order[0]._asdict()

    return order_dict
