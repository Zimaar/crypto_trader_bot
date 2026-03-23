"""Trade level helpers for alerts and analysis."""


def parse_price(value):
    """Parse a numeric price from strings, ints, floats, or nested values."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace(",", "").replace("$", "").strip()
        if not cleaned or cleaned == "-":
            return 0.0
        try:
            return float(cleaned)
        except ValueError:
            return 0.0
    return 0.0


def format_price(value):
    """Format a price for chat output."""
    if not value:
        return "N/A"
    if value >= 1000:
        return f"${value:,.2f}"
    if value >= 1:
        return f"${value:,.4f}"
    return f"${value:,.6f}"


def format_percent(value):
    """Format a percent with sign."""
    if value is None:
        return "N/A"
    return f"{value:+.2f}%"


def _extract_levels(raw_levels):
    if not raw_levels:
        return []
    if isinstance(raw_levels, dict):
        extracted = []
        for key in sorted(raw_levels):
            value = parse_price(raw_levels[key])
            if value > 0:
                extracted.append(value)
        return extracted
    if isinstance(raw_levels, (list, tuple)):
        return [value for value in (parse_price(item) for item in raw_levels) if value > 0]
    single = parse_price(raw_levels)
    return [single] if single > 0 else []


def build_trade_plan(signal=None, screener_data=None):
    """Build breakout price, TP, and risk data for a signal or snapshot."""
    signal = signal or {}
    screener_data = screener_data or {}
    add = screener_data.get("additionalData", screener_data)

    signal_price = parse_price(signal.get("lastPrice"))
    screener_price = parse_price(screener_data.get("lastPrice"))
    breakout_price = signal_price or screener_price

    supports = _extract_levels(add.get("SUPPORT"))
    resistances = _extract_levels(add.get("RESISTANCE"))

    tp_price = next((level for level in resistances if level > breakout_price), 0.0)
    if breakout_price > 0 and tp_price <= 0:
        tp_price = breakout_price * 1.08

    stop_price = 0.0
    if supports:
        below_breakout = [level for level in supports if level < breakout_price]
        if below_breakout:
            stop_price = max(below_breakout)
    if breakout_price > 0 and stop_price <= 0:
        stop_price = breakout_price * 0.95

    profit_pct = None
    loss_pct = None
    rr_ratio = None
    if breakout_price > 0 and tp_price > 0:
        profit_pct = ((tp_price - breakout_price) / breakout_price) * 100
    if breakout_price > 0 and stop_price > 0:
        loss_pct = ((breakout_price - stop_price) / breakout_price) * 100
    if profit_pct is not None and loss_pct and loss_pct > 0:
        rr_ratio = profit_pct / loss_pct

    return {
        "breakout_price": breakout_price,
        "tp_price": tp_price,
        "stop_price": stop_price,
        "profit_pct": profit_pct,
        "loss_pct": loss_pct,
        "rr_ratio": rr_ratio,
    }
