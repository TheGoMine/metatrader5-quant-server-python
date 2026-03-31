import logging
import os
import sys
import threading
from flask import Flask, jsonify, request
from dotenv import load_dotenv
import MetaTrader5 as mt5
from flasgger import Swagger
from werkzeug.middleware.proxy_fix import ProxyFix
from swagger import swagger_config
from lib import initialize_mt5_connection

# Import routes
from routes.health import health_bp
from routes.symbol import symbol_bp
from routes.data import data_bp
from routes.position import position_bp
from routes.order import order_bp
from routes.history import history_bp
from routes.error import error_bp

load_dotenv()

LOG_LEVEL = logging.INFO 
LOG_FORMAT = '%(asctime)s [%(levelname)s] (%(name)s): %(message)s'

# 3. Apply the configuration
logging.basicConfig(
    level=LOG_LEVEL,
    format=LOG_FORMAT,
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['PREFERRED_URL_SCHEME'] = 'https'

swagger = Swagger(app, config=swagger_config)


def _is_auth_exempt(path: str) -> bool:
    # Keep basic health/docs reachable for infra checks.
    exempt_prefixes = (
        "/alive",
        "/health",
        "/mt5-health",
        "/apidocs",
        "/flasgger_static",
    )
    return path.startswith(exempt_prefixes)


@app.before_request
def require_api_key():
    configured_key = os.getenv("MT5_API_KEY", "").strip()
    if not configured_key or _is_auth_exempt(request.path):
        return None

    provided_key = request.headers.get("X-API-Key", "").strip()
    if provided_key != configured_key:
        return jsonify({"error": "Unauthorized"}), 401
    return None

# Register blueprints
app.register_blueprint(health_bp)
app.register_blueprint(symbol_bp)
app.register_blueprint(data_bp)
app.register_blueprint(position_bp)
app.register_blueprint(order_bp)
app.register_blueprint(history_bp)
app.register_blueprint(error_bp)

app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

if __name__ == '__main__':
    # Do not block web server startup on MT5 initialization.
    # MT5 IPC can hang during terminal warm-up; initialize in background.
    def warmup_mt5():
        if not initialize_mt5_connection():
            error_code, error_str = mt5.last_error()
            logger.error(f"Failed to initialize MT5 in warmup: {error_code} {error_str}")

    threading.Thread(target=warmup_mt5, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get('MT5_API_PORT')))