from flask import Blueprint, jsonify
import MetaTrader5 as mt5
from flasgger import swag_from

health_bp = Blueprint('health', __name__)


@health_bp.route('/alive')
def alive_check():
    """Fast liveness endpoint (does not touch MT5)."""
    return jsonify({"status": "alive"}), 200

@health_bp.route('/health')
@swag_from({
    'tags': ['Health'],
    'responses': {
        200: {
            'description': 'Health check successful',
            'schema': {
                'type': 'object',
                'properties': {
                    'status': {'type': 'string'},
                    'mt5_connected': {'type': 'boolean'},
                    'mt5_initialized': {'type': 'boolean'}
                }
            }
        }
    }
})
def health_check():
    """
    Health Check Endpoint
    ---
    description: Check the health status of the application and MT5 connection.
    responses:
      200:
        description: Health check successful
    """
    return jsonify({
        "status": "healthy",
        "mt5_connected": mt5 is not None,
        "mt5_initialized": None,
        "note": "Use /mt5-health for MT5 initialization probe."
    }), 200


@health_bp.route('/mt5-health')
def mt5_health_check():
    """
    MT5 status endpoint without re-initializing each request.
    Uses terminal/account snapshot + last_error for diagnosis.
    """
    terminal_info = mt5.terminal_info() if mt5 is not None else None
    account_info = mt5.account_info() if mt5 is not None else None
    error_code, error_str = mt5.last_error() if mt5 is not None else (None, "mt5 module unavailable")

    return jsonify({
        "status": "healthy",
        "mt5_connected": mt5 is not None,
        "mt5_initialized": terminal_info is not None,
        "terminal_connected": bool(getattr(terminal_info, "connected", False)) if terminal_info else False,
        "trade_allowed": bool(getattr(terminal_info, "trade_allowed", False)) if terminal_info else False,
        "account_login": getattr(account_info, "login", None) if account_info else None,
        "last_error": {
            "code": error_code,
            "message": error_str,
        },
    }), 200

@health_bp.route('/account_info')
@swag_from({
    'tags': ['Health'],
    'responses': {
        200: {
            'description': 'Get account information successful',
            'schema': {
                'type': 'object',
                'properties': {
                    'login': {'type': 'string'},
                    'name': {'type': 'string'},
                    'server': {'type': 'string'},
                    'status': {'type': 'string'},
                }
            }
        }
    }
})
def get_account_info():
    try:
        account_info_raw = mt5.account_info()
        if account_info_raw is None:
            error_code, error_str = mt5.last_error()
            return jsonify({
                'status': 'error',
                'reason': f"MT5 account unavailable: {error_code} {error_str}"
            }), 503

        account_info = account_info_raw._asdict()

        return jsonify({
            'status': 'successful',
            'login': account_info['login'],
            'server': account_info['server'],
            'name': account_info['name'],
            'currency': account_info.get('currency'),
            'leverage': account_info.get('leverage'),
            'balance': account_info.get('balance'),
            'equity': account_info.get('equity'),
            'margin': account_info.get('margin'),
            'margin_free': account_info.get('margin_free'),
            'margin_level': account_info.get('margin_level'),
            'profit': account_info.get('profit'),
        }), 200
    except Exception as e:
        return jsonify({
            'status': 'error',
            'reason': str(e)
        }), 500
