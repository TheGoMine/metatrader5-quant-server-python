from flask import Blueprint, jsonify
import MetaTrader5 as mt5
from flasgger import swag_from
from concurrent.futures import ThreadPoolExecutor, TimeoutError

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
    initialized = False
    timed_out = False
    if mt5 is not None:
        # MT5 initialization can hang during terminal warm-up; cap the probe time.
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(mt5.initialize)
            try:
                initialized = bool(future.result(timeout=2))
            except TimeoutError:
                timed_out = True

    return jsonify({
        "status": "healthy",
        "mt5_connected": mt5 is not None,
        "mt5_initialized": initialized,
        "mt5_probe_timeout": timed_out
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
        if not mt5.initialize():
            print("initialize() failed, error code =",mt5.last_error())
        account_info = mt5.account_info()._asdict()

        return jsonify({
            'status': 'successful',
            'login': account_info['login'],
            'server': account_info['server'],
            'name': account_info['name']
        }), 200
    except Exception as e:
        return jsonify({
            'status': 'error',
            'reason': str(e)
        }), 500
