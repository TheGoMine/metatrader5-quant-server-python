from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import List


@dataclass
class RiskResult:
    entry_anchor: float
    sl_distance: float
    risk_per_lot: float
    total_lots: float
    per_tp_lot: float
    rounded_per_tp_lot: float
    lots_per_tp: List[float]


def _round_lot_half_up(value: float) -> float:
    if value < 0.01:
        return 0.01
    rounded = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return float(rounded)


def calculate_lot_distribution(
    risk_usd: int,
    range_low: float,
    range_high: float,
    stop_loss: float,
    tp_count: int,
    contract_size: float,
) -> RiskResult:
    if risk_usd <= 0:
        raise ValueError("Risk USD must be greater than zero.")
    if tp_count <= 0:
        raise ValueError("TP count must be greater than zero.")
    if contract_size <= 0:
        raise ValueError("Contract size must be greater than zero.")

    entry_anchor = (range_low + range_high) / 2.0
    sl_distance = abs(stop_loss - entry_anchor)
    if sl_distance == 0:
        raise ValueError("Stop loss distance cannot be zero.")

    risk_per_lot = sl_distance * contract_size
    if risk_per_lot <= 0:
        raise ValueError("Risk per lot must be positive.")

    total_lots = float(risk_usd) / risk_per_lot
    per_tp_lot = total_lots / tp_count
    rounded_per_tp_lot = _round_lot_half_up(per_tp_lot)
    lots_per_tp = [rounded_per_tp_lot for _ in range(tp_count)]

    return RiskResult(
        entry_anchor=entry_anchor,
        sl_distance=sl_distance,
        risk_per_lot=risk_per_lot,
        total_lots=total_lots,
        per_tp_lot=per_tp_lot,
        rounded_per_tp_lot=rounded_per_tp_lot,
        lots_per_tp=lots_per_tp,
    )
