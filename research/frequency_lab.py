"""Offline, fixed-parameter opening-range diagnostic. Never imported by live code."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

NY = ZoneInfo("America/New_York")
ATHENS = ZoneInfo("Europe/Athens")
STEP = 900
PARAMETERS = {"opening_range_minutes": 30, "first_complete_bar_minutes_after_open": 15,
              "target_r": 2.0, "maximum_cost_r": 0.20, "maximum_entries_per_day": 5,
              "symbol_cooldown_seconds": 3600, "reference_lots": 1.0}


def broker_timestamp(value):
    # MT5 export encodes broker wall time as an epoch-shaped integer.
    wall = datetime.fromtimestamp(int(value), timezone.utc).replace(tzinfo=None)
    return int(wall.replace(tzinfo=ATHENS).timestamp())


def load_bars(directory):
    bars, quality, hashes = {}, {}, {}
    for path in sorted(Path(directory).glob("*-M15.csv")):
        symbol = path.stem.removesuffix("-M15")
        frame = pd.read_csv(path)
        result, invalid = {}, 0
        for row in frame.to_dict("records"):
            stamp = broker_timestamp(row["time"])
            prices = [row[k] for k in ("open", "high", "low", "close")]
            if not all(pd.notna(v) and float(v) > 0 for v in prices):
                invalid += 1
                continue
            if not row["low"] <= min(row["open"], row["close"]) <= max(row["open"], row["close"]) <= row["high"]:
                invalid += 1
                continue
            if stamp in result:
                raise ValueError(f"duplicate bar: {symbol} {stamp}")
            result[stamp] = {key: float(row[key]) for key in ("open", "high", "low", "close", "spread")}
        bars[symbol] = result
        quality[symbol] = {"rows": len(frame), "valid_bars": len(result), "invalid_ohlc": invalid,
                           "zero_or_missing_spread": sum(not v["spread"] > 0 for v in result.values())}
        hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return bars, quality, hashes


def sessions_between(start, end):
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    calendar = xcals.get_calendar("XNYS", start=start - pd.Timedelta(days=7), end=end + pd.Timedelta(days=7))
    return [(str(day.date()), int(calendar.session_open(day).timestamp()),
             int(calendar.session_close(day).timestamp())) for day in calendar.sessions
            if start.date() <= day.date() <= end.date()]


def round_trip_cost(spec, spread, multiplier):
    """Price units for one reference lot; no claims about account affordability."""
    costs = spec.get("costs", {})
    fields = [spec.get("point"), spec.get("contract_size"), costs.get("slippage_points"),
              costs.get("commission_round_turn_per_lot"), costs.get("minimum_commission_round_turn")]
    if not spread > 0 or any(v is None or not pd.notna(v) for v in fields):
        return None
    point, contract, slip, commission, minimum = map(float, fields)
    if point <= 0 or contract <= 0 or min(slip, commission, minimum) < 0:
        return None
    # Slippage applies on each side; historical bid/ask spread charged once.
    return multiplier * (spread * point + 2 * slip * point + max(commission, minimum) / contract)


def exit_fill(position, bar, scheduled=False, open_only=False):
    """Scheduled market exit, adverse/favourable gaps, then conservative OHLC ordering."""
    side, stop, target = position["side"], position["stop"], position["target"]
    if scheduled:
        return bar["open"], "session_exit"
    if side * (bar["open"] - stop) <= 0:
        return bar["open"], "stop_gap"
    if side * (bar["open"] - target) >= 0:
        return target, "target_gap_capped"
    if open_only:
        return None
    stop_hit = bar["low"] <= stop if side == 1 else bar["high"] >= stop
    target_hit = bar["high"] >= target if side == 1 else bar["low"] <= target
    if stop_hit:
        return stop, "stop_first" if target_hit else "stop"
    if target_hit:
        return target, "target"
    return None


def evaluate(bars, specs, sessions, multiplier=1.0):
    counts = Counter()
    trades, candidates = [], []
    for day, opened, closed in sessions:
        first, range_end, flatten = opened + STEP, opened + 3 * STEP, closed - STEP
        entry_deadline = closed - 35 * 60
        opening, previous, waiting, cooldown = {}, {}, {}, {}
        position, blocked, entries = None, False, 0
        for stamp in range(first, flatten + 1, STEP):
            current = {s: rows[stamp] for s, rows in bars.items() if stamp in rows}
            if stamp == first and current:
                counts["sessions_with_any_opening_data"] += 1
            def finish(fill, reason):
                nonlocal position
                trade = dict(position, exit_time=stamp, exit_price=fill, exit_reason=reason)
                trade["gross_r"] = position["side"] * (fill - position["entry"]) / position["risk"]
                trade["net_r"] = trade["gross_r"] - position["cost"] / position["risk"]
                trades.append(trade)
                position = None
            # A missing held-position bar cannot supply an executable exit.
            if position:
                if position["symbol"] not in current:
                    trades.append(dict(position, exit_time=stamp, exit_reason="missing_bar_unresolved", net_r=None, gross_r=None))
                    position, blocked = None, True
                    counts["unresolved_positions"] += 1
                else:
                    fill = exit_fill(position, current[position["symbol"]], stamp >= flatten, open_only=True)
                    if fill:
                        finish(*fill)
            # Only candidates generated at the preceding bar's close may enter.
            for symbol, signal in sorted(waiting.items()):
                record = dict(signal, symbol=symbol, entry_time=stamp, session=day, cost_eligible=False)
                candidates.append(record)
                counts["candidates"] += 1
                if symbol not in current:
                    counts["candidate_missing_next_bar"] += 1
                    continue
                entry = current[symbol]["open"]
                risk = signal["side"] * (entry - signal["stop"])
                cost = round_trip_cost(specs.get(symbol, {}), signal["spread"], multiplier)
                if risk <= 0 or cost is None or cost / risk > PARAMETERS["maximum_cost_r"]:
                    counts["cost_or_gap_ineligible"] += 1
                    continue
                record["cost_eligible"] = True
                counts["cost_eligible_candidates"] += 1
                if blocked or position or entries >= 5 or stamp - cooldown.get(symbol, -10**12) < 3600:
                    counts["portfolio_or_cooldown_blocked"] += 1
                    continue
                position = dict(symbol=symbol, session=day, signal_time=signal["signal_time"], entry_time=stamp,
                                entry=entry, side=signal["side"], stop=signal["stop"], risk=risk,
                                target=entry + signal["side"] * 2 * risk, cost=cost)
                entries += 1
                cooldown[symbol] = stamp
                counts["entries"] += 1
            if position:
                fill = exit_fill(position, current[position["symbol"]])
                if fill:
                    finish(*fill)
            waiting = {}
            for symbol in bars:
                row = current.get(symbol)
                if row is None:
                    previous.pop(symbol, None)
                    if stamp < range_end:
                        opening.pop(symbol, None)
                    continue
                if stamp == first:
                    opening[symbol] = [row["high"], row["low"]]
                elif stamp == first + STEP and symbol in opening:
                    opening[symbol] = [max(opening[symbol][0], row["high"]), min(opening[symbol][1], row["low"])]
                elif stamp >= range_end and stamp + STEP < entry_deadline and symbol in opening and symbol in previous:
                    high, low = opening[symbol]
                    prior = previous[symbol]
                    side = 1 if prior <= high < row["close"] else -1 if prior >= low > row["close"] else 0
                    if side:
                        waiting[symbol] = {"signal_time": stamp + STEP, "side": side,
                                           "stop": low if side == 1 else high, "spread": row["spread"]}
                previous[symbol] = row["close"]
        if position:
            raise AssertionError("position survived scheduled exit")
    return {"counts": dict(counts), "candidates": candidates, "trades": trades}


def summarize(result, days):
    valid = [t for t in result["trades"] if t["net_r"] is not None]
    sessions = len(days)
    return {"calendar_sessions": sessions, **result["counts"],
            "candidate_rate_per_session": len(result["candidates"]) / sessions if sessions else 0,
            "cost_eligible_rate_per_session": sum(c["cost_eligible"] for c in result["candidates"]) / sessions if sessions else 0,
            "simulated_entry_rate_per_session": len(result["trades"]) / sessions if sessions else 0,
            "resolved_trades": len(valid), "total_net_r": sum(t["net_r"] for t in valid),
            "mean_net_r": sum(t["net_r"] for t in valid) / len(valid) if valid else None,
            "win_fraction": sum(t["net_r"] > 0 for t in valid) / len(valid) if valid else None,
            "meaning": "Unit-R diagnostics at fixed reference lot; no portfolio USD equity or return estimate."}


def run(history, broker_file, output):
    bars, quality, hashes = load_bars(history)
    stamps = [stamp for rows in bars.values() for stamp in rows]
    start = datetime.fromtimestamp(min(stamps), NY).date().isoformat()
    end = datetime.fromtimestamp(max(stamps), NY).date().isoformat()
    sessions = sessions_between(start, end)
    cut = int(len(sessions) * 0.70)
    specs = json.loads(Path(broker_file).read_text(encoding="utf-8-sig"))["mappings"]
    report = {"strategy": "Fixed 30-minute complete-bar opening-range breakout", "deployable": False,
              "parameters": PARAMETERS, "start": start, "end": end,
              "chronological_split": {"train_sessions": cut, "test_sessions": len(sessions) - cut,
                                      "test_start": sessions[cut][0], "parameters_tuned": False},
              "quality": quality, "history_sha256": hashes, "results": {},
              "limitations": ["Research only; no live imports, model calls or orders.",
                  "Current-universe survivorship bias; symbol histories start at different dates.",
                  "Broker epochs interpreted as Europe/Athens wall time, converted with DST to XNYS sessions.",
                  "Historical spread uses the prior fully closed signal bar; zero or missing spread rejects candidate.",
                  "Current broker commissions/slippage applied historically, not point-in-time costs; spread is not a guaranteed execution quote.",
                  "Fixed one-lot reference costs and unit R; no account sizing, margin, shared USD risk or account equity simulation.",
                  "Event veto, AI review, execution latency, liquidity, partial fills and market impact are disabled/unmodelled.",
                  "Tick/real volume is never used or fabricated.",
                  "Missing held-position bars produce unresolved trades, excluded from R summaries; all further entries that day are blocked.",
                  "A day with an unresolved trade has unknown P&L; reported R totals are incomplete when unresolved_positions > 0.",
                  "Cross-symbol simultaneous eligible candidates use alphabetical symbol priority.",
                  "cost_2x re-evaluates eligibility and may select different trades; fixed-trade 2x totals are also reported under cost_1x.",
                  "Stops have no guaranteed price; adverse gaps execute at observed next open, with additional modeled costs."]}
    for multiplier in (1.0, 2.0):
        key = f"cost_{int(multiplier)}x"
        report["results"][key] = {}
        for name, subset in (("train", sessions[:cut]), ("test", sessions[cut:]), ("all", sessions)):
            result = evaluate(bars, specs, subset, multiplier)
            report["results"][key][name] = summarize(result, subset)
            if multiplier == 1.0:
                resolved = [t for t in result["trades"] if t["net_r"] is not None]
                stressed = [t["gross_r"] - 2 * t["cost"] / t["risk"] for t in resolved]
                report["results"][key][name]["fixed_same_trades_2x_cost_total_net_r"] = sum(stressed)
                report["results"][key][name]["fixed_same_trades_2x_cost_mean_net_r"] = sum(stressed) / len(stressed) if stressed else None
            if name == "all":
                report["results"][key]["trades"] = result["trades"]
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", required=True)
    parser.add_argument("--broker", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = run(args.history, args.broker, args.output)
    print(json.dumps({k: {s: v[s] for s in ("train", "test")} for k, v in result["results"].items()}, indent=2))
