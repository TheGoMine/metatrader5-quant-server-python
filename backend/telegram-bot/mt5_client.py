import os
from typing import Any, Dict, List, Optional

import requests


class MT5Client:
    def __init__(self, base_url: Optional[str] = None, timeout: int = 15) -> None:
        self.base_url = (base_url or os.getenv("MT5_API_URL", "http://mt5:5001")).rstrip("/")
        self.timeout = timeout

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        response = requests.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def _post(self, path: str, payload: Optional[Dict[str, Any]] = None) -> Any:
        response = requests.post(f"{self.base_url}{path}", json=payload or {}, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def get_mt5_health(self) -> Dict[str, Any]:
        return self._get("/mt5-health")

    def get_positions(self) -> List[Dict[str, Any]]:
        data = self._get("/get_positions")
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "positions" in data:
            return data.get("positions") or []
        return []

    def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        return self._get(f"/symbol_info/{symbol}")

    def place_order(
        self,
        symbol: str,
        volume: float,
        side: str,
        sl: float,
        tp: float,
        deviation: int = 20,
        comment: str = "telegram-bot",
    ) -> Dict[str, Any]:
        payload = {
            "symbol": symbol,
            "volume": volume,
            "type": side,
            "sl": sl,
            "tp": tp,
            "deviation": deviation,
            "comment": comment,
        }
        return self._post("/order", payload)

    def modify_sl_tp(self, ticket: int, sl: float, tp: float) -> Dict[str, Any]:
        payload = {
            "position": int(ticket),
            "sl": float(sl),
            "tp": float(tp),
        }
        return self._post("/modify_sl_tp", payload)

    def close_all_positions(self) -> Dict[str, Any]:
        return self._post("/close_all_positions", {"order_type": "all"})
