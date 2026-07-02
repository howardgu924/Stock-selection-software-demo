#coding:gbk
# QMT thermostat adapter.
# Scope:
# 1. Do not modify the original Stock-selection-software-demo thermostat logic.
# 2. Port the same thermostat decision rules into one QMT-compatible file.
# 3. Use QMT only for data access, order placement, and operation logs.
# 4. Historical backtest first. Confirm order parameters before live use.

import math


# =========================
# Parameters
# =========================

STRATEGY_NAME = "QMT_THERMOSTAT_ADAPTER"

SELF_SELECT_SECTORS = [
    u"\u6211\u7684\u81ea\u9009", u"\u81ea\u9009", u"\u81ea\u9009\u80a1",
    u"\u81ea\u9009\u4e00", u"\u5168\u90e8\u81ea\u9009\u80a1",
    u"\u4e2d\u8bc11000", u"\u4e2d\u8bc11000\u6210\u4efd\u80a1",
    u"\u4e2d\u8bc11000\u6210\u5206\u80a1", u"\u4e2d\u8bc11000\u6307\u6570",
    u"000852", u"000852.SH",
]

FALLBACK_STOCKS = [
    "300497.SZ", "601969.SH", "002797.SZ", "605369.SH", "002597.SZ",
    "002250.SZ", "603309.SH", "003520.SZ", "300636.SZ", "000739.SZ",
    "688488.SH", "002940.SZ", "003020.SZ", "300759.SZ", "603276.SH",
]

MARKET_BENCHMARKS = [
    ("000852.SH", 0.50, "CSI1000"),
    ("399006.SZ", 0.30, "ChiNext"),
    ("000688.SH", 0.20, "STAR50"),
]
RISK_ANCHOR_COMPONENTS = ["000300.SH", "000852.SH", "399006.SZ"]
QMT_FIELDS = ["open", "close", "high", "low", "volume", "amount"]

TOTAL_CAPITAL = 100000.0
MAX_BUY_PER_DAY = 2
MAX_HOLDINGS = 10
COOLDOWN_BARS = 3
MIN_HISTORY_BARS = 60
HISTORY_BARS = 260

SIGNAL_ONLY_MODE = False
BACKTEST_TRADE_MODE = True
ACCOUNT_ID = "56804072"
BUY_ORDER_TYPE = 1101
SELL_ORDER_TYPE = 1102
PRICE_TYPE = 5

WRITE_TRADE_CSV = True
TRADE_CSV_PATH = "qmt_thermostat_trades_20260701.csv"

DEBUG_BACKTEST_CONTEXT = True
DEBUG_FILTER_REASON = True
DEBUG_GRID_CANDIDATES = True


# =========================
# Global state
# =========================

g = {
    "stock_pool": [],
    "positions": {},
    "cooldown": {},
    "last_date": None,
    "buy_count_today": 0,
    "cash": TOTAL_CAPITAL,
    "trade_log": [],
    "csv_header_written": False,
}


# =========================
# QMT entry points
# =========================

def init(ContextInfo):
    g["cash"] = TOTAL_CAPITAL
    g["positions"] = {}
    g["cooldown"] = {}
    g["trade_log"] = []
    g["csv_header_written"] = False

    stock_pool = get_stock_pool(ContextInfo)
    g["stock_pool"] = stock_pool

    universe = list(stock_pool)
    for code, _weight, _name in MARKET_BENCHMARKS:
        add_unique(universe, code)
    for code in RISK_ANCHOR_COMPONENTS:
        add_unique(universe, code)
    safe_set_universe(ContextInfo, universe)

    log("init pool=%s capital=%.2f trade_mode=%s signal_only=%s account=%s"
        % (len(stock_pool), TOTAL_CAPITAL, BACKTEST_TRADE_MODE, SIGNAL_ONLY_MODE, ACCOUNT_ID))
    if BACKTEST_TRADE_MODE and not ACCOUNT_ID:
        log("warning ACCOUNT_ID is empty; QMT order may fail")


def handlebar(ContextInfo):
    current_date = get_current_date(ContextInfo)
    current_barpos = get_context_barpos(ContextInfo)
    reset_daily_counter_if_needed(current_date)
    reduce_cooldown()

    if not g["stock_pool"]:
        log("empty stock pool; check QMT sector names or FALLBACK_STOCKS")
        return

    if DEBUG_BACKTEST_CONTEXT:
        log_backtest_context(ContextInfo, current_barpos, current_date)

    histories = load_stock_histories(ContextInfo, g["stock_pool"], HISTORY_BARS)
    market_history = load_market_history(ContextInfo, histories, HISTORY_BARS)
    market = classify_market_regime(market_history)
    stock_states = classify_stock_pool(histories)
    pool = calc_pool_strength(histories, stock_states)

    if defensive_anchor_down(ContextInfo):
        market["regime"] = "market_downtrend"
        market["evidence"] = market.get("evidence", "") + "; defensive_anchor_down"

    log("market=%s confidence=%s pool=%s above_ma20=%.2f up=%s down=%s cash=%.2f"
        % (
            market.get("regime"),
            market.get("confidence"),
            pool.get("pool_regime"),
            pool.get("pool_above_ma20_ratio", 0.0),
            pool.get("pool_uptrend_count", 0),
            pool.get("pool_downtrend_count", 0),
            g["cash"],
        ))

    process_sells(ContextInfo, histories, stock_states, market, current_date)

    if market.get("regime") == "market_downtrend":
        log("market_downtrend: no new buy; sell/risk-control only")
        return
    if len(g["positions"]) >= MAX_HOLDINGS:
        log("max holdings reached: %s" % len(g["positions"]))
        return

    rows, grid_rows, reason_count, reason_examples = build_advice_rows(
        histories, stock_states, market, pool, current_date
    )

    if DEBUG_GRID_CANDIDATES and grid_rows:
        log_grid_candidates(grid_rows)
    if DEBUG_FILTER_REASON and not executable_buy_rows(rows):
        log_filter_summary(reason_count, reason_examples)

    buy_rows = executable_buy_rows(rows)
    buy_rows.sort(key=lambda row: (float(row.get("score") or 0.0), -int(row.get("priority") or 99)), reverse=True)
    slots = min(MAX_HOLDINGS - len(g["positions"]), MAX_BUY_PER_DAY - g["buy_count_today"])
    if slots <= 0:
        log("daily buy limit reached")
        return

    for row in buy_rows[:slots]:
        send_buy_order(ContextInfo, row, current_date, market)


# =========================
# Strategy flow
# =========================

def process_sells(ContextInfo, histories, stock_states, market, current_date):
    for stock in list(g["positions"].keys()):
        bars = histories.get(stock, empty_bars())
        state = stock_states.get(stock, insufficient_state())
        sell, reason = calc_sell_signal(stock, bars, state, market)
        if sell:
            send_sell_order(ContextInfo, stock, bars, state, market, current_date, reason)


def build_advice_rows(histories, stock_states, market, pool, current_date):
    rows = []
    grid_rows = []
    reason_count = {}
    reason_examples = {}

    for stock in g["stock_pool"]:
        if stock in g["positions"]:
            add_reason(reason_count, reason_examples, "already_held", stock)
            continue
        if g["cooldown"].get(stock, 0) > 0:
            add_reason(reason_count, reason_examples, "cooldown", stock)
            continue
        bars = histories.get(stock, empty_bars())
        state = stock_states.get(stock, insufficient_state())
        row = advice_for_stock(stock, bars, state, market, pool, current_date, False)
        rows.append(row)

    rows = apply_grid_limits(rows, market, pool)
    for row in rows:
        if row.get("strategy_family") == "grid":
            grid_rows.append(row)
        if not (row.get("executable") and row.get("action") in ("buy", "add")):
            add_reason(reason_count, reason_examples, row.get("reason_code", row.get("stock_regime", "observe")), row.get("symbol", ""))
    return rows, grid_rows, reason_count, reason_examples


def advice_for_stock(stock, bars, state, market, pool, current_date, is_holding):
    close = last_value(bars.get("close", []))
    stock_regime = state.get("regime", "insufficient_data")
    market_regime = market.get("regime", "insufficient_data")
    row = {
        "symbol": stock,
        "date": current_date,
        "market_regime": market_regime,
        "stock_regime": stock_regime,
        "strategy": "thermostat",
        "strategy_family": "observe",
        "action": "observe",
        "strength": "normal",
        "score": 0.0,
        "priority": 99,
        "suggested_position_pct": 0.0,
        "suggested_shares": 0,
        "entry_price": close,
        "stop_price": None,
        "target_price": None,
        "reference_price": close,
        "grid_upper": None,
        "grid_lower": None,
        "grid_mid": None,
        "grid_unit_pct": None,
        "grid_max_layers": None,
        "grid_stop_condition": None,
        "reason": state.get("evidence", ""),
        "risk_note": "",
        "executable": False,
        "data_sufficient": bool(state.get("data_sufficient")),
        "reason_code": stock_regime,
    }

    if not state.get("data_sufficient"):
        row.update({
            "action": "wait_confirm",
            "strength": "reduced",
            "risk_note": "insufficient_data_no_trade",
            "reason_code": "insufficient_data",
        })
        return row

    if stock_regime == "downtrend":
        row.update({
            "strategy_family": "risk_control",
            "action": "sell" if is_holding else "blocked",
            "score": 0.2,
            "priority": 1 if is_holding else 90,
            "stop_price": close,
            "risk_note": "stock_downtrend",
            "executable": bool(is_holding),
            "reason_code": "stock_downtrend",
        })
        return row

    if stock_regime == "range":
        grid = grid_prices(bars)
        row.update({
            "strategy_family": "grid",
            "action": "hold" if is_holding else "observe",
            "strength": "reduced" if market_regime in ("market_transition", "market_downtrend") else "normal",
            "score": grid_score(state, grid),
            "priority": 20,
            "grid_upper": grid.get("upper"),
            "grid_lower": grid.get("lower"),
            "grid_mid": grid.get("mid"),
            "grid_unit_pct": 0.08,
            "grid_max_layers": 4,
            "grid_stop_condition": "break_grid_range_or_trend_change",
            "reason_code": "grid_candidate",
        })
        return row

    if stock_regime in ("strong_uptrend", "uptrend"):
        return trend_row(row, bars, state, market, pool, is_holding)

    row.update({
        "strategy_family": "transition",
        "action": "observe",
        "strength": "reduced",
        "score": 0.3,
        "priority": 50,
        "risk_note": "stock_transition_observe",
        "reason_code": "stock_transition",
    })
    return row


def trend_row(row, bars, state, market, pool, is_holding):
    stock_regime = state.get("regime")
    market_regime = market.get("regime")
    pct = 0.0
    action = "observe"
    strength = "normal"
    executable = False
    suffix = ""

    if market_regime == "market_downtrend":
        action = "hold" if is_holding else "observe"
        strength = "reduced"
        suffix = "; market_downtrend_no_new_buy"
    elif market_regime == "market_transition":
        if stock_regime == "strong_uptrend" and not is_holding:
            pct = 0.04
            action = "buy"
            strength = "reduced"
            executable = True
            suffix = "; transition_probe"
        else:
            action = "hold" if is_holding else "observe"
            strength = "reduced"
            suffix = "; transition_observe"
    elif market_regime == "market_range":
        if stock_regime in ("strong_uptrend", "uptrend"):
            pct = 0.04
            action = "add" if is_holding else "buy"
            strength = "reduced"
            executable = True
            suffix = "; range_probe"
    elif market_regime == "market_uptrend":
        pct = 0.11 if stock_regime == "strong_uptrend" else 0.09
        action = "add" if is_holding else "buy"
        executable = True
        suffix = "; uptrend_following"

    if pool.get("pool_regime") in ("pool_weak", "pool_chaotic") and pct > 0:
        pct = min(pct, 0.04)
        strength = "reduced"
        suffix += "; weak_pool_reduce_position"

    last = row.get("entry_price")
    shares, final_pct, cash_note = position_from_cash(g["cash"], last, pct)
    stop, target = stop_target(bars, last)
    row.update({
        "strategy_family": "trend_following",
        "action": action,
        "strength": strength,
        "score": 0.9 if stock_regime == "strong_uptrend" else 0.75,
        "priority": 2 if is_holding else (3 if stock_regime == "strong_uptrend" else 6),
        "suggested_position_pct": final_pct,
        "suggested_shares": shares,
        "stop_price": stop,
        "target_price": target,
        "reason": (row.get("reason") or "") + suffix + cash_note,
        "risk_note": cash_note.strip("; ") if cash_note else "",
        "executable": bool(executable and shares > 0),
        "reason_code": "trend_%s_%s" % (market_regime, stock_regime),
    })
    if shares == 0 and pct > 0:
        row["action"] = "observe"
        row["executable"] = False
        row["reason_code"] = "cash_not_enough"
    return row


def apply_grid_limits(rows, market, pool):
    grid_rows = [row for row in rows if row.get("strategy_family") == "grid"]
    if not grid_rows:
        return rows
    market_regime = market.get("regime")
    if market_regime in ("market_downtrend", "market_transition", "insufficient_data"):
        limit = 0
    elif market_regime == "market_range":
        stable = (
            float(market.get("range60") or 1.0) <= 0.10
            and float(market.get("vol20") or 1.0) <= 0.015
            and abs(float(market.get("ma60_slope") or 1.0)) <= 0.01
        )
        limit = 3 if stable else 2
    elif market_regime == "market_uptrend":
        has_trend = any(row.get("strategy_family") == "trend_following" and row.get("executable") for row in rows)
        limit = 1 if has_trend else 2
    else:
        limit = 0

    sorted_grid = sorted(grid_rows, key=lambda row: float(row.get("score") or 0.0), reverse=True)
    enabled = set([row.get("symbol") for row in sorted_grid[:limit]])
    for row in grid_rows:
        if row.get("symbol") in enabled:
            row["action"] = "hold" if row.get("action") == "hold" else "buy"
            row["executable"] = True
            row["suggested_position_pct"] = 0.04
            shares, final_pct, cash_note = position_from_cash(g["cash"], row.get("entry_price"), 0.04)
            row["suggested_shares"] = shares
            row["suggested_position_pct"] = final_pct
            row["reason_code"] = "grid_enabled"
            if shares <= 0:
                row["action"] = "observe"
                row["executable"] = False
                row["reason_code"] = "cash_not_enough"
                row["risk_note"] = cash_note.strip("; ")
        else:
            row["action"] = "observe"
            row["executable"] = False
            row["suggested_position_pct"] = 0.0
            row["suggested_shares"] = 0
            row["reason_code"] = "grid_low_priority"
    return rows


def calc_sell_signal(stock, bars, state, market):
    position = g["positions"].get(stock)
    if not position:
        return False, "no_position"
    if not valid_bars(bars, 2):
        return False, "insufficient_bars"

    close = last_value(bars.get("close", []))
    if close is None or close <= 0:
        return False, "invalid_close"

    position["bars"] = int(position.get("bars", 0)) + 1
    buy_price = float(position.get("buy_price") or close)
    profit = close / buy_price - 1 if buy_price else 0.0
    position["high_profit"] = max(float(position.get("high_profit") or 0.0), profit)

    stop_price = position.get("stop_price")
    target_price = position.get("target_price")

    if state.get("regime") == "downtrend":
        return True, "thermostat_stock_downtrend"
    if stop_price and close <= float(stop_price):
        return True, "thermostat_stop_price close=%.3f stop=%.3f" % (close, float(stop_price))
    if target_price and close >= float(target_price):
        return True, "thermostat_target_price close=%.3f target=%.3f" % (close, float(target_price))
    return False, "hold"


# =========================
# Orders and operation log
# =========================

def send_buy_order(ContextInfo, row, current_date, market):
    stock = row.get("symbol")
    price = row.get("entry_price")
    volume = int(row.get("suggested_shares") or 0)
    if not price or price <= 0 or volume < 100:
        log("buy skipped stock=%s price=%s volume=%s" % (stock, price, volume))
        record_operation(current_date, stock, "buy_skipped", price, volume, row, market, False, "invalid_price_or_volume")
        return
    if stock in g["positions"]:
        return

    order_sent = False
    if BACKTEST_TRADE_MODE:
        order_sent = place_order(ContextInfo, stock, volume, BUY_ORDER_TYPE, "BUY")
    elif SIGNAL_ONLY_MODE:
        log("signal only buy stock=%s volume=%s" % (stock, volume))

    g["positions"][stock] = {
        "buy_price": price,
        "volume": volume,
        "bars": 0,
        "high_profit": 0.0,
        "stop_price": row.get("stop_price"),
        "target_price": row.get("target_price"),
        "strategy_family": row.get("strategy_family"),
    }
    g["cash"] = max(0.0, g["cash"] - price * volume)
    g["buy_count_today"] += 1

    log("[BUY] stock=%s price=%.3f volume=%s pct=%.2f%% regime=%s/%s reason=%s"
        % (
            stock,
            price,
            volume,
            float(row.get("suggested_position_pct") or 0.0) * 100,
            row.get("market_regime"),
            row.get("stock_regime"),
            row.get("reason_code", ""),
        ))
    record_operation(current_date, stock, "buy", price, volume, row, market, order_sent, row.get("reason_code", ""))


def send_sell_order(ContextInfo, stock, bars, state, market, current_date, reason):
    position = g["positions"].get(stock)
    if not position:
        return
    price = last_value(bars.get("close", []))
    volume = int(position.get("volume") or 0)
    if not price or price <= 0 or volume <= 0:
        log("sell skipped stock=%s price=%s volume=%s" % (stock, price, volume))
        return

    row = {
        "symbol": stock,
        "market_regime": market.get("regime"),
        "stock_regime": state.get("regime"),
        "strategy_family": "risk_control",
        "action": "sell",
        "entry_price": price,
        "suggested_shares": volume,
        "suggested_position_pct": 0.0,
        "stop_price": position.get("stop_price"),
        "target_price": position.get("target_price"),
        "reason_code": reason,
        "reason": state.get("evidence", ""),
    }
    order_sent = False
    if BACKTEST_TRADE_MODE:
        order_sent = place_order(ContextInfo, stock, volume, SELL_ORDER_TYPE, "SELL")
    elif SIGNAL_ONLY_MODE:
        log("signal only sell stock=%s volume=%s" % (stock, volume))

    g["cash"] += price * volume
    g["positions"].pop(stock, None)
    g["cooldown"][stock] = COOLDOWN_BARS
    log("[SELL] stock=%s price=%.3f volume=%s reason=%s" % (stock, price, volume, reason))
    record_operation(current_date, stock, "sell", price, volume, row, market, order_sent, reason)


def place_order(ContextInfo, stock, volume, order_type, side):
    if not ACCOUNT_ID:
        log("order skipped empty ACCOUNT_ID side=%s stock=%s" % (side, stock))
        return False
    try:
        passorder(23, order_type, ACCOUNT_ID, stock, PRICE_TYPE, -1, volume,
                  STRATEGY_NAME, 1, "", ContextInfo)
        log("order sent side=%s stock=%s volume=%s" % (side, stock, volume))
        return True
    except Exception as e:
        log("order failed side=%s stock=%s error=%s" % (side, stock, e))
        return False


def record_operation(current_date, stock, action, price, shares, row, market, order_sent, note):
    item = {
        "date": current_date,
        "symbol": stock,
        "action": action,
        "price": price,
        "shares": shares,
        "market_regime": row.get("market_regime", market.get("regime") if market else ""),
        "stock_regime": row.get("stock_regime", ""),
        "strategy_family": row.get("strategy_family", ""),
        "reason": row.get("reason_code") or note,
        "stop_price": row.get("stop_price"),
        "target_price": row.get("target_price"),
        "order_sent": bool(order_sent),
    }
    g["trade_log"].append(item)
    log("TRADE_LOG|date=%s|symbol=%s|action=%s|price=%s|shares=%s|market=%s|stock=%s|family=%s|reason=%s|order_sent=%s"
        % (
            item["date"], item["symbol"], item["action"], item["price"], item["shares"],
            item["market_regime"], item["stock_regime"], item["strategy_family"],
            item["reason"], item["order_sent"],
        ))
    if WRITE_TRADE_CSV:
        write_trade_csv(item)


def write_trade_csv(item):
    fields = [
        "date", "symbol", "action", "price", "shares", "market_regime",
        "stock_regime", "strategy_family", "reason", "stop_price",
        "target_price", "order_sent",
    ]
    try:
        mode = "a"
        f = open(TRADE_CSV_PATH, mode)
        if not g.get("csv_header_written"):
            f.write(",".join(fields) + "\n")
            g["csv_header_written"] = True
        values = []
        for field in fields:
            value = item.get(field)
            text = "" if value is None else str(value)
            values.append(text.replace(",", " "))
        f.write(",".join(values) + "\n")
        f.close()
    except Exception as e:
        log("write trade csv failed error=%s" % e)


# =========================
# Regime classification
# =========================

def classify_stock_pool(histories):
    result = {}
    for stock, bars in histories.items():
        result[stock] = classify_stock_regime(bars)
    return result


def classify_market_regime(bars):
    metrics = calculate_regime_metrics(bars)
    if not metrics.get("data_sufficient"):
        return regime_result("insufficient_data", "low", "need_60_bars", metrics)
    ret60 = float(metrics.get("ret60") or 0.0)
    close = metrics.get("close")
    ma60 = float(metrics.get("ma60") or 0.0)
    ma20_slope = float(metrics.get("ma20_slope") or 0.0)
    ma60_slope = float(metrics.get("ma60_slope") or 0.0)
    range20 = float(metrics.get("range20") or 0.0)
    range60 = float(metrics.get("range60") or 0.0)
    vol20 = float(metrics.get("vol20") or 0.0)
    conflict = (ret60 > 0 and close is not None and close < ma60) or (ret60 < 0 and close is not None and close > ma60)
    if ret60 <= -0.06 and close is not None and close < ma60 and ma20_slope < 0:
        return regime_result("market_downtrend", "medium", "market_60d_down_below_ma60", metrics)
    if vol20 > 0.035 or range20 > 0.12 or conflict:
        return regime_result("market_transition", "low", "market_high_vol_or_conflict", metrics)
    if ret60 >= 0.05 and close is not None and close > ma60 and ma20_slope > 0:
        return regime_result("market_uptrend", "medium", "market_60d_up_ma_slope_up", metrics)
    if abs(ret60) <= 0.05 and range60 <= 0.15 and abs(ma60_slope) <= 0.02:
        return regime_result("market_range", "medium", "market_range_bound", metrics)
    return regime_result("market_transition", "low", "market_unstable", metrics)


def classify_stock_regime(bars):
    metrics = calculate_regime_metrics(bars)
    if not metrics.get("data_sufficient"):
        return regime_result("insufficient_data", "low", "need_60_bars", metrics)
    ret20 = float(metrics.get("ret20") or 0.0)
    ret60 = float(metrics.get("ret60") or 0.0)
    close = metrics.get("close")
    ma20 = float(metrics.get("ma20") or 0.0)
    ma60 = float(metrics.get("ma60") or 0.0)
    ma20_slope = float(metrics.get("ma20_slope") or 0.0)
    range20 = float(metrics.get("range20") or 0.0)
    close_ma20_distance = float(metrics.get("close_ma20_distance") or 0.0)
    trend_strength = float(metrics.get("trend_strength") or 0.0)
    vol_pct = metrics.get("vol20_percentile_252")
    range_pct = metrics.get("range20_percentile_252")
    conflict = (ret60 > 0 and close is not None and close < ma60) or (ret60 < 0 and close is not None and close > ma60)
    extreme_transition = (
        (vol_pct is not None and vol_pct >= 80)
        or (range_pct is not None and range_pct >= 80)
        or range20 > 0.30
        or conflict
    )
    if ret60 <= -0.08 and close is not None and close < ma60 and ma20_slope < 0:
        return regime_result("downtrend", "medium", "stock_60d_down_below_ma60", metrics)
    if extreme_transition:
        return regime_result("transition", "low", "stock_high_vol_or_conflict", metrics)
    if ret60 >= 0.12 and close is not None and close > ma20 and ma20 > ma60 and trend_strength >= 1.2:
        return regime_result("strong_uptrend", "high", "stock_strong_uptrend", metrics)
    if ret60 >= 0.08 and close is not None and close > ma60 and ma20 > ma60 and ma20_slope > 0:
        return regime_result("uptrend", "medium", "stock_uptrend", metrics)
    if abs(ret20) <= 0.05 and 0.06 <= range20 <= 0.20 and abs(ma20_slope) <= 0.02 and abs(close_ma20_distance) <= 0.03:
        return regime_result("range", "medium", "stock_range", metrics)
    return regime_result("transition", "low", "stock_unstable", metrics)


def calculate_regime_metrics(bars):
    close = clean_values(bars.get("close", []))
    high = clean_values(bars.get("high", []))
    low = clean_values(bars.get("low", []))
    count = len(close)
    last = close[-1] if count else None
    ma20 = mean_tail(close, 20)
    ma60 = mean_tail(close, 60)
    ret20 = tail_return(close, 20)
    ret60 = tail_return(close, 60)
    range20 = tail_range(close, 20)
    range60 = tail_range(close, 60)
    daily = pct_changes(close)
    vol20 = stddev(daily[-20:]) if len(daily[-20:]) >= 2 else 0.0
    ma20_slope = ma_slope(close, 20, 5)
    ma60_slope = ma_slope(close, 60, 10)
    close_ma20_distance = last / ma20 - 1 if last is not None and ma20 else 0.0
    close_ma60_distance = last / ma60 - 1 if last is not None and ma60 else 0.0
    trend_strength = ret60 / (vol20 * math.sqrt(60)) if vol20 else 0.0
    vol20_percentile = None
    range20_percentile = None
    if count >= 252:
        rolling_vol = rolling_std(pct_changes(close), 20)[-252:]
        rolling_rng = rolling_range(close, 20)[-252:]
        if rolling_vol:
            current = rolling_vol[-1]
            vol20_percentile = count_less_equal(rolling_vol, current) * 100.0 / len(rolling_vol)
        if rolling_rng:
            current = rolling_rng[-1]
            range20_percentile = count_less_equal(rolling_rng, current) * 100.0 / len(rolling_rng)
    if count < 60:
        bucket = "insufficient"
    elif count < 120:
        bucket = "reduced"
    elif count < 252:
        bucket = "normal"
    else:
        bucket = "full"
    return {
        "close": last,
        "ret20": ret20,
        "ret60": ret60,
        "ma20": ma20,
        "ma60": ma60,
        "range20": range20,
        "range60": range60,
        "vol20": vol20,
        "ma20_slope": ma20_slope,
        "ma60_slope": ma60_slope,
        "close_ma20_distance": close_ma20_distance,
        "close_ma60_distance": close_ma60_distance,
        "vol20_percentile_252": vol20_percentile,
        "range20_percentile_252": range20_percentile,
        "trend_strength": trend_strength,
        "atr20": calc_atr20(high, low, close),
        "data_sufficient": count >= MIN_HISTORY_BARS,
        "length_bucket": bucket,
        "count": count,
    }


def regime_result(regime, confidence, label, metrics):
    evidence = (
        "ret20=%.2f%% ret60=%.2f%% ma20=%.3f ma60=%.3f range20=%.2f%% vol20=%.2f%% label=%s"
        % (
            float(metrics.get("ret20") or 0.0) * 100,
            float(metrics.get("ret60") or 0.0) * 100,
            float(metrics.get("ma20") or 0.0),
            float(metrics.get("ma60") or 0.0),
            float(metrics.get("range20") or 0.0) * 100,
            float(metrics.get("vol20") or 0.0) * 100,
            label,
        )
    )
    result = dict(metrics)
    result.update({
        "regime": regime,
        "confidence": confidence,
        "data_sufficient": bool(metrics.get("data_sufficient")),
        "evidence": evidence,
    })
    return result


def insufficient_state():
    return {
        "regime": "insufficient_data",
        "confidence": "low",
        "data_sufficient": False,
        "evidence": "insufficient_data",
    }


def calc_pool_strength(histories, states):
    usable = []
    for bars in histories.values():
        metrics = calculate_regime_metrics(bars)
        if metrics.get("data_sufficient") and metrics.get("ma20") and metrics.get("close") is not None:
            usable.append(metrics)
    if not usable:
        return {
            "pool_regime": "pool_neutral",
            "pool_above_ma20_ratio": 0.0,
            "pool_uptrend_count": 0,
            "pool_downtrend_count": 0,
            "pool_ret20": 0.0,
            "pool_avg_vol20": 0.0,
        }
    above_ratio = len([m for m in usable if float(m.get("close")) > float(m.get("ma20"))]) * 1.0 / len(usable)
    ret20_values = [float(m.get("ret20") or 0.0) for m in usable]
    avg_vol20 = sum([float(m.get("vol20") or 0.0) for m in usable]) / len(usable)
    ret20_std = stddev(ret20_values)
    regimes = [value.get("regime") for value in states.values()]
    if ret20_std >= 0.08 and avg_vol20 >= 0.04:
        pool_regime = "pool_chaotic"
    elif above_ratio >= 0.60:
        pool_regime = "pool_strong"
    elif above_ratio >= 0.40:
        pool_regime = "pool_neutral"
    else:
        pool_regime = "pool_weak"
    return {
        "pool_regime": pool_regime,
        "pool_above_ma20_ratio": above_ratio,
        "pool_uptrend_count": len([r for r in regimes if r in ("strong_uptrend", "uptrend")]),
        "pool_downtrend_count": len([r for r in regimes if r == "downtrend"]),
        "pool_ret20": sum(ret20_values) / len(ret20_values),
        "pool_avg_vol20": avg_vol20,
    }


# =========================
# QMT data access
# =========================

def get_stock_pool(ContextInfo):
    stock_pool = []
    for sector_name in SELF_SELECT_SECTORS:
        for func_name in ["get_stock_list_in_sector", "get_sector"]:
            try:
                func = getattr(ContextInfo, func_name)
                result = func(sector_name)
                if result:
                    stock_pool.extend(list(result))
                    log("sector loaded name=%s count=%s" % (sector_name, len(result)))
                    break
            except Exception:
                pass
    for sector_name in SELF_SELECT_SECTORS:
        try:
            result = list(get_stock_list_in_sector(sector_name))
            if result:
                stock_pool.extend(result)
                log("global sector loaded name=%s count=%s" % (sector_name, len(result)))
        except Exception:
            pass
    stock_pool = normalize_stock_list(stock_pool)
    if len(stock_pool) < 5:
        before = len(stock_pool)
        stock_pool = normalize_stock_list(stock_pool + list(FALLBACK_STOCKS))
        log("fallback stocks appended before=%s after=%s" % (before, len(stock_pool)))
    return stock_pool


def load_stock_histories(ContextInfo, symbols, count):
    histories = {}
    for stock in symbols:
        histories[stock] = get_bars(ContextInfo, stock, "1d", count)
    return histories


def load_market_history(ContextInfo, histories, count):
    loaded = []
    for code, weight, _name in MARKET_BENCHMARKS:
        bars = get_bars(ContextInfo, code, "1d", count)
        if valid_bars(bars, MIN_HISTORY_BARS):
            loaded.append((bars, weight))
    if loaded:
        return composite_close_history(loaded)
    log("market index unavailable; fallback to candidate aggregate")
    return aggregate_candidate_history(histories)


def defensive_anchor_down(ContextInfo):
    states = []
    for code in RISK_ANCHOR_COMPONENTS:
        bars = get_bars(ContextInfo, code, "1d", HISTORY_BARS)
        if valid_bars(bars, MIN_HISTORY_BARS):
            states.append(classify_market_regime(bars).get("regime"))
    return len(states) == len(RISK_ANCHOR_COMPONENTS) and all([state == "market_downtrend" for state in states])


def get_bars(ContextInfo, stock, period, count):
    fields = QMT_FIELDS
    request_count = get_history_request_count(ContextInfo, count, period)
    for method_name in ["get_market_data_ex", "get_market_data"]:
        try:
            func = getattr(ContextInfo, method_name)
            data = func(fields, [stock], period=period, count=request_count)
            bars = parse_market_data(data, stock, fields)
            bars = slice_bars_for_context(ContextInfo, bars, period, count)
            if valid_bars(bars, min(1, count)):
                return bars
        except Exception:
            pass
    return empty_bars()


def parse_market_data(data, stock, fields):
    bars = {field: [] for field in fields}
    if data is None:
        return bars
    try:
        if isinstance(data, dict) and stock in data:
            stock_data = data[stock]
            for field in fields:
                bars[field] = to_float_list(stock_data[field])
            return bars
    except Exception:
        pass
    try:
        if isinstance(data, dict):
            for field in fields:
                bars[field] = to_float_list(data.get(field, []))
            return bars
    except Exception:
        pass
    try:
        for field in fields:
            bars[field] = to_float_list(data[field])
    except Exception:
        pass
    return bars


def get_history_request_count(ContextInfo, count, period):
    barpos = get_context_barpos(ContextInfo)
    if period != "1d":
        return count
    if barpos is None or barpos < 0:
        return count
    return max(count, barpos + 1)


def slice_bars_for_context(ContextInfo, bars, period, count):
    if not bars:
        return bars
    result = {}
    for field in ["open", "close", "high", "low", "volume", "amount"]:
        result[field] = list(bars.get(field, []))
    if period == "1d":
        barpos = get_context_barpos(ContextInfo)
        length = len(result.get("close", []))
        if barpos is not None and barpos >= 0 and length > barpos + 1:
            for field in result:
                result[field] = result[field][:barpos + 1]
    for field in result:
        if len(result[field]) > count:
            result[field] = result[field][-count:]
    return result


def composite_close_history(loaded):
    min_len = min([len(item[0].get("close", [])) for item in loaded])
    if min_len <= 0:
        return empty_bars()
    total_weight = sum([item[1] for item in loaded])
    close = []
    for i in range(min_len):
        value = 0.0
        for bars, weight in loaded:
            series = bars.get("close", [])[-min_len:]
            first = series[0] if series else 0.0
            value += (series[i] / first * 1000 if first else 0.0) * (weight / total_weight)
        close.append(value)
    return {
        "open": list(close),
        "close": close,
        "high": list(close),
        "low": list(close),
        "volume": [0.0] * len(close),
        "amount": [0.0] * len(close),
    }


def aggregate_candidate_history(histories):
    frames = [bars for bars in histories.values() if valid_bars(bars, MIN_HISTORY_BARS)]
    if not frames:
        return empty_bars()
    min_len = min([len(bars.get("close", [])) for bars in frames])
    close = []
    for i in range(min_len):
        values = [bars.get("close", [])[-min_len:][i] for bars in frames if len(bars.get("close", [])) >= min_len]
        close.append(sum(values) / len(values) if values else 0.0)
    return {
        "open": list(close),
        "close": close,
        "high": list(close),
        "low": list(close),
        "volume": [0.0] * len(close),
        "amount": [0.0] * len(close),
    }


# =========================
# Math helpers
# =========================

def clean_values(values):
    result = []
    for value in values:
        try:
            number = float(value)
            if not math.isnan(number):
                result.append(number)
        except Exception:
            pass
    return result


def to_float_list(values):
    if values is None:
        return []
    try:
        if hasattr(values, "tolist"):
            values = values.tolist()
    except Exception:
        pass
    try:
        if hasattr(values, "values"):
            values = values.values
    except Exception:
        pass
    try:
        return clean_values(list(values))
    except Exception:
        try:
            return [float(values)]
        except Exception:
            return []


def mean_tail(values, window):
    values = clean_values(values)
    if not values:
        return 0.0
    part = values[-min(window, len(values)):]
    return sum(part) / len(part)


def tail_return(values, window):
    values = clean_values(values)
    if len(values) < 2:
        return 0.0
    part = values[-min(window, len(values)):]
    first = part[0]
    last = part[-1]
    return last / first - 1 if first else 0.0


def tail_range(values, window):
    values = clean_values(values)
    if not values:
        return 0.0
    part = values[-min(window, len(values)):]
    avg = sum(part) / len(part)
    return (max(part) - min(part)) / avg if avg else 0.0


def pct_changes(values):
    values = clean_values(values)
    result = []
    for i in range(1, len(values)):
        prev = values[i - 1]
        result.append(values[i] / prev - 1 if prev else 0.0)
    return result


def stddev(values):
    values = clean_values(values)
    if len(values) < 2:
        return 0.0
    avg = sum(values) / len(values)
    variance = sum([(value - avg) ** 2 for value in values]) / (len(values) - 1)
    return math.sqrt(max(variance, 0.0))


def rolling_std(values, window):
    values = clean_values(values)
    result = []
    if len(values) < window:
        return result
    for i in range(window - 1, len(values)):
        result.append(stddev(values[i - window + 1:i + 1]))
    return result


def rolling_range(values, window):
    values = clean_values(values)
    result = []
    if len(values) < window:
        return result
    for i in range(window - 1, len(values)):
        part = values[i - window + 1:i + 1]
        avg = sum(part) / len(part)
        result.append((max(part) - min(part)) / avg if avg else 0.0)
    return result


def count_less_equal(values, current):
    return len([value for value in values if value <= current])


def ma_slope(values, window, lag):
    values = clean_values(values)
    if len(values) < window + lag:
        return 0.0
    current = mean_tail(values, window)
    previous = mean_tail(values[:len(values) - lag], window)
    return current / previous - 1 if previous else 0.0


def calc_atr20(high, low, close):
    high = clean_values(high)
    low = clean_values(low)
    close = clean_values(close)
    length = min(len(high), len(low), len(close))
    if length < 6:
        return None
    true_ranges = []
    for i in range(1, length):
        tr = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )
        true_ranges.append(tr)
    values = true_ranges[-20:]
    if len(values) < 5:
        return None
    return sum(values) / len(values)


def stop_target(bars, close):
    if not close or close <= 0:
        return None, None
    atr = calc_atr20(bars.get("high", []), bars.get("low", []), bars.get("close", []))
    if atr:
        stop_pct = min(max(2 * atr / close, 0.06), 0.12)
        target_pct = stop_pct * 2
    else:
        stop_pct = 0.08
        target_pct = 0.18
    return round_price(close * (1 - stop_pct)), round_price(close * (1 + target_pct))


def grid_prices(bars):
    close = clean_values(bars.get("close", []))[-20:]
    if not close:
        return {"upper": None, "lower": None, "mid": None}
    upper = max(close)
    lower = min(close)
    return {"upper": round_price(upper), "lower": round_price(lower), "mid": round_price((upper + lower) / 2)}


def grid_score(state, grid):
    ret20 = abs(float(state.get("ret20") or 0.0))
    ma20_slope_value = abs(float(state.get("ma20_slope") or 0.0))
    range20 = float(state.get("range20") or 0.0)
    vol20 = float(state.get("vol20") or 0.0)
    close = state.get("close")
    mid = grid.get("mid")
    upper = grid.get("upper")
    stability = max(0.0, 1 - ret20 / 0.05) * 0.15 + max(0.0, 1 - ma20_slope_value / 0.02) * 0.15
    width = max(0.0, 1 - abs(range20 - 0.13) / 0.10) * 0.20
    volatility = max(0.0, 1 - abs(vol20 - 0.03) / 0.04) * 0.20
    position = 0.0
    if close is not None and mid and upper:
        distance_mid = abs(float(close) / mid - 1)
        near_upper = float(close) >= mid + (upper - mid) * 0.75
        position = max(0.0, 1 - distance_mid / 0.03) * 0.15 if not near_upper else 0.02
    return round(stability + width + volatility + position, 6)


def position_from_cash(cash, price, pct):
    if pct <= 0 or not price or price <= 0 or cash <= 0:
        return 0, 0.0, ""
    shares = int((cash * pct / price) // 100) * 100
    if shares <= 0:
        return 0, 0.0, "; cash_not_enough_for_100_shares"
    return shares, pct, ""


def last_value(values):
    values = clean_values(values)
    return values[-1] if values else None


def round_price(value):
    return round(float(value), 3) if value is not None else None


# =========================
# Runtime helpers
# =========================

def valid_bars(bars, min_count):
    if not bars:
        return False
    for field in ["close", "high", "low"]:
        if len(bars.get(field, [])) < min_count:
            return False
    return True


def empty_bars():
    return {"open": [], "close": [], "high": [], "low": [], "volume": [], "amount": []}


def executable_buy_rows(rows):
    return [row for row in rows if row.get("executable") and row.get("action") in ("buy", "add") and int(row.get("suggested_shares") or 0) >= 100]


def normalize_stock_list(stock_list):
    result = []
    seen = set()
    for stock in stock_list:
        code = normalize_symbol(str(stock).strip())
        if not code or code in seen:
            continue
        seen.add(code)
        result.append(code)
    return result


def normalize_symbol(symbol):
    code = str(symbol).strip()
    if not code:
        return ""
    code = code.upper()
    if code.startswith("SH") and len(code) >= 8:
        return code[2:8] + ".SH"
    if code.startswith("SZ") and len(code) >= 8:
        return code[2:8] + ".SZ"
    if code.endswith(".SH") or code.endswith(".SZ"):
        return code
    digits = "".join([ch for ch in code if ch.isdigit()])
    if len(digits) >= 6:
        digits = digits[-6:]
        if digits.startswith(("5", "6", "9")):
            return digits + ".SH"
        return digits + ".SZ"
    return code


def add_unique(values, item):
    if item not in values:
        values.append(item)


def safe_set_universe(ContextInfo, universe):
    try:
        ContextInfo.set_universe(universe)
    except Exception as e:
        log("set_universe failed error=%s" % e)


def get_current_date(ContextInfo):
    for attr in ["date", "trade_date", "cur_date", "current_date"]:
        try:
            value = getattr(ContextInfo, attr)
            if value:
                return value
        except Exception:
            pass
    try:
        timetag = ContextInfo.get_bar_timetag(ContextInfo.barpos)
        if timetag:
            return timetag
    except Exception:
        pass
    return None


def get_context_barpos(ContextInfo):
    for attr in ["barpos", "bar_pos", "current_bar", "curbar"]:
        try:
            value = getattr(ContextInfo, attr)
            if value is not None:
                return int(value)
        except Exception:
            pass
    return None


def reset_daily_counter_if_needed(current_date):
    if current_date != g.get("last_date"):
        g["last_date"] = current_date
        g["buy_count_today"] = 0


def reduce_cooldown():
    for stock in list(g["cooldown"].keys()):
        g["cooldown"][stock] -= 1
        if g["cooldown"][stock] <= 0:
            g["cooldown"].pop(stock, None)


def add_reason(reason_count, reason_examples, reason, stock):
    reason = str(reason or "unknown")
    reason_count[reason] = reason_count.get(reason, 0) + 1
    if reason not in reason_examples:
        reason_examples[reason] = stock


def log_grid_candidates(rows):
    top = sorted(rows, key=lambda row: float(row.get("score") or 0.0), reverse=True)[:5]
    for row in top:
        log("grid candidate stock=%s enabled=%s score=%.4f action=%s lower=%s mid=%s upper=%s"
            % (
                row.get("symbol"),
                row.get("executable"),
                float(row.get("score") or 0.0),
                row.get("action"),
                row.get("grid_lower"),
                row.get("grid_mid"),
                row.get("grid_upper"),
            ))


def log_filter_summary(reason_count, reason_examples):
    if not reason_count:
        log("no buy candidate: empty scan")
        return
    items = sorted(reason_count.items(), key=lambda item: item[1], reverse=True)
    parts = []
    for reason, count in items[:8]:
        parts.append("%s=%s(example:%s)" % (reason, count, reason_examples.get(reason, "")))
    log("no buy candidate reasons: " + "; ".join(parts))


def log_backtest_context(ContextInfo, barpos, current_date):
    sample_stock = g["stock_pool"][0] if g["stock_pool"] else ""
    sample_close = "NA"
    sample_bars_len = 0
    if sample_stock:
        bars = get_bars(ContextInfo, sample_stock, "1d", 5)
        if valid_bars(bars, 1):
            sample_close = "%.3f" % bars["close"][-1]
            sample_bars_len = len(bars["close"])
    log("backtest context barpos=%s date=%s pool=%s sample=%s sample_close=%s sample_bars=%s"
        % (barpos, current_date, len(g["stock_pool"]), sample_stock, sample_close, sample_bars_len))


def log(message):
    print("[%s] %s" % (STRATEGY_NAME, message))

