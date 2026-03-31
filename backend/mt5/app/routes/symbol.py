from flask import Blueprint, jsonify
import MetaTrader5 as mt5
from flasgger import swag_from
import logging
from lib import initialize_mt5_connection

symbol_bp = Blueprint('symbol', __name__)
logger = logging.getLogger(__name__)


def _has_live_prices(tick_obj) -> bool:
    try:
        return float(getattr(tick_obj, "bid", 0.0)) > 0 and float(getattr(tick_obj, "ask", 0.0)) > 0
    except Exception:
        return False


def _find_symbol_candidates(symbol: str):
    requested = symbol.upper().strip()
    candidates = [requested]
    try:
        all_symbols = mt5.symbols_get() or []
        for sym in all_symbols:
            name = getattr(sym, "name", "")
            upper_name = name.upper()
            if upper_name == requested:
                continue
            if upper_name.startswith(requested):
                candidates.append(name)
    except Exception:
        # Ignore lookup errors and keep exact symbol only.
        pass
    return candidates


def _get_live_tick_with_resolution(symbol: str):
    last_tick = None
    for candidate in _find_symbol_candidates(symbol):
        mt5.symbol_select(candidate, True)
        tick = mt5.symbol_info_tick(candidate)
        if tick is None:
            continue
        last_tick = (candidate, tick)
        if _has_live_prices(tick):
            return candidate, tick
    return last_tick

@symbol_bp.route('/symbol_info_tick/<symbol>', methods=['GET'])
@swag_from({
    'tags': ['Symbol'],
    'parameters': [
        {
            'name': 'symbol',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Symbol name to retrieve tick information.'
        }
    ],
    'responses': {
        200: {
            'description': 'Tick information retrieved successfully.',
            'schema': {
                'type': 'object',
                'properties': {
                    'bid': {'type': 'number'},
                    'ask': {'type': 'number'},
                    'last': {'type': 'number'},
                    'volume': {'type': 'integer'},
                    'time': {'type': 'integer'}
                }
            }
        },
        404: {
            'description': 'Failed to get symbol tick info.'
        }
    }
})
def get_symbol_info_tick_endpoint(symbol):
    """
    Get Symbol Tick Information
    ---
    description: Retrieve the latest tick information for a given symbol.
    """
    # Ensure MT5 session is initialized before reading symbol data.
    if not initialize_mt5_connection(retries=2, delay_seconds=1):
        error_code, error_str = mt5.last_error()
        return jsonify({
            "error": "MT5 not initialized",
            "last_error": {"code": error_code, "message": error_str},
        }), 503

    resolved = _get_live_tick_with_resolution(symbol)
    if not resolved:
        error_code, error_str = mt5.last_error()
        return jsonify({
            "error": "Failed to get symbol tick info",
            "symbol": symbol,
            "hint": "Symbol may be unavailable or named differently on this broker (e.g. suffix like m/pro).",
            "last_error": {"code": error_code, "message": error_str},
        }), 404

    resolved_symbol, tick = resolved
    tick_dict = tick._asdict()
    tick_dict["symbol"] = resolved_symbol
    tick_dict["requested_symbol"] = symbol
    return jsonify(tick_dict)

@symbol_bp.route('/symbol_info/<symbol>', methods=['GET'])
@swag_from({
    'tags': ['Symbol'],
    'parameters': [
        {
            'name': 'symbol',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Symbol name to retrieve information.'
        }
    ],
    'responses': {
        200: {
            'description': 'Symbol information retrieved successfully.',
            'schema': {
                'type': 'object',
                'properties': {
                    'name': {'type': 'string'},
                    'path': {'type': 'string'},
                    'description': {'type': 'string'},
                    'volume_min': {'type': 'number'},
                    'volume_max': {'type': 'number'},
                    'volume_step': {'type': 'number'},
                    'price_digits': {'type': 'integer'},
                    'spread': {'type': 'number'},
                    'points': {'type': 'integer'},
                    'trade_mode': {'type': 'integer'},
                    # Add other relevant fields as needed
                }
            }
        },
        404: {
            'description': 'Failed to get symbol info.'
        }
    }
})
def get_symbol_info(symbol):
    """
    Get Symbol Information
    ---
    description: Retrieve detailed information for a given symbol.
    """
    if not initialize_mt5_connection(retries=2, delay_seconds=1):
        error_code, error_str = mt5.last_error()
        return jsonify({
            "error": "MT5 not initialized",
            "last_error": {"code": error_code, "message": error_str},
        }), 503

    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        mt5.symbol_select(symbol, True)
        symbol_info = mt5.symbol_info(symbol)

    if symbol_info is None:
        error_code, error_str = mt5.last_error()
        return jsonify({
            "error": "Failed to get symbol info",
            "symbol": symbol,
            "hint": "Symbol may be unavailable or named differently on this broker (e.g. suffix like m/pro).",
            "last_error": {"code": error_code, "message": error_str},
        }), 404
    
    symbol_info_dict = symbol_info._asdict()
    return jsonify(symbol_info_dict)