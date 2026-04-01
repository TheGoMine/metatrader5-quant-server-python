import re
from dataclasses import dataclass
from typing import List


@dataclass
class ParsedSignal:
    symbol: str
    action: str
    range_low: float
    range_high: float
    stop_loss: float
    take_profits: List[float]


def _extract_float(value: str) -> float:
    return float(value.strip().replace(",", ""))


def parse_signal_text(text: str) -> ParsedSignal:
    if not text or not text.strip():
        raise ValueError("Signal text is empty.")

    cleaned = text.replace("\r", "")

    symbol_match = re.search(r"\b([A-Z]{3,10})\b", cleaned)
    if not symbol_match:
        raise ValueError("Could not extract symbol from signal.")
    symbol = symbol_match.group(1)

    action_match = re.search(r"\b(BUY|SELL)\b", cleaned, flags=re.IGNORECASE)
    if not action_match:
        raise ValueError("Could not extract action (BUY/SELL).")
    action = action_match.group(1).upper()

    range_match = re.search(
        r"(?:RANGE\s*:\s*)?(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)",
        cleaned,
        flags=re.IGNORECASE,
    )
    if not range_match:
        raise ValueError("Could not extract range.")
    range_low = _extract_float(range_match.group(1))
    range_high = _extract_float(range_match.group(2))

    sl_match = re.search(r"\bSL\s*[: ]\s*(\d+(?:\.\d+)?)", cleaned, flags=re.IGNORECASE)
    if not sl_match:
        raise ValueError("Could not extract stop loss.")
    stop_loss = _extract_float(sl_match.group(1))

    tp_match = re.search(r"\bTP\s*[: ]\s*([0-9./\s]+)", cleaned, flags=re.IGNORECASE)
    if not tp_match:
        raise ValueError("Could not extract take profit levels.")

    tp_raw = tp_match.group(1).strip()
    tp_values = [part.strip() for part in tp_raw.split("/") if part.strip()]
    if not tp_values:
        raise ValueError("No take profit levels found.")

    take_profits = [_extract_float(v) for v in tp_values]
    return ParsedSignal(
        symbol=symbol,
        action=action,
        range_low=range_low,
        range_high=range_high,
        stop_loss=stop_loss,
        take_profits=take_profits,
    )
