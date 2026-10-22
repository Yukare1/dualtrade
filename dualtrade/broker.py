from __future__ import annotations
import math
from pathlib import Path
import time
import psutil
from datetime import datetime, timezone
from .config import bridge
from .calendar import NY


def native_sessions(path, now):
    import csv
    try:
        rows = list(csv.reader(path.read_text().splitlines(), delimiter="|"))
        clock = rows[0]
        if clock[0] != "CLOCK" or not 0 <= now-int(clock[1]) <= 15: return None
        offset = round((int(clock[2])-int(clock[1]))/1800)*1800
        if abs(int(clock[2])-int(clock[1])-offset) > 5: return None
        sessions = {}
        for r in rows[1:]:
            if r[0] == "SESSION": sessions.setdefault(r[1], {}).setdefault(int(r[2]), []).append((int(r[4])%86400, int(r[5])%86400))
        return offset, sessions
    except (OSError, ValueError, IndexError): return None


def interest_cost(price, size, rate):
    """MT5 INTEREST_CURRENT annual percentage, 360-day banking year; no credits."""
    return max(0.0, -rate) / 100 * price * size / 360


class BrokerError(RuntimeError): pass


class ReadOnlyMT5:
    """Never sends orders, changes account, or launches a stopped terminal."""
    def __init__(self, cfg):
        self.cfg = cfg
        import MetaTrader5 as mt
        self.mt = mt

    def connect(self):
        path = Path(self.cfg["terminal_path"]).resolve()
        running = False
        for proc in psutil.process_iter(["name", "exe"]):
            try:
                if proc.info["exe"] and Path(proc.info["exe"]).resolve() == path:
                    running = True
            except (psutil.AccessDenied, OSError):
                pass
        if not running:
            raise BrokerError("MT5_NOT_RUNNING: open the IC Markets terminal and select the intended demo account")
        if not self.mt.initialize(str(path), timeout=10000):
            raise BrokerError("MT5_CONNECTION_FAILED")
        info = self.mt.account_info()
        if info is None or info.trade_mode != self.mt.ACCOUNT_TRADE_MODE_DEMO:
            self.mt.shutdown()
            raise BrokerError("DEMO_ACCOUNT_REQUIRED")
        if info.server != self.cfg["expected_server"] or info.currency != "USD":
            self.mt.shutdown()
            raise BrokerError("ACCOUNT_SERVER_OR_CURRENCY_MISMATCH")
        if self.cfg.get("expected_login") and info.login != self.cfg["expected_login"]:
            self.mt.shutdown()
            raise BrokerError("ACCOUNT_IDENTITY_MISMATCH")
        return info

    def positions(self):
        result = self.mt.positions_get()
        if result is None: raise BrokerError("POSITION_QUERY_FAILED")
        return list(result)

    def orders(self):
        result = self.mt.orders_get()
        if result is None: raise BrokerError("ORDER_QUERY_FAILED")
        return list(result)

    def inspect(self):
        account = self.connect()
        symbols = self.mt.symbols_get()
        if symbols is None: raise BrokerError("SYMBOL_QUERY_FAILED")
        mappings, issues = {}, []
        native = native_sessions(bridge(self.cfg)/"broker-native.txt", time.time())
        for ticker, spec in self.cfg["symbols"].items():
            contract = dict(self.cfg["contracts"].get(ticker, {}))
            explicit = contract.get("symbol")
            candidates = [s for s in symbols if s.name == (explicit or ticker)]
            if len(candidates) != 1:
                issues.append(f"{ticker}: verified exact broker mapping required")
                continue
            s = candidates[0]
            if s.swap_mode == self.mt.SYMBOL_SWAP_MODE_INTEREST_CURRENT and s.bid > 0 and s.ask >= s.bid:
                contract["financing_long_per_lot_day"] = interest_cost(s.ask, s.trade_contract_size, s.swap_long)
                contract["financing_short_per_lot_day"] = interest_cost(s.ask, s.trade_contract_size, s.swap_short)
            else:
                contract["verified"] = False
            if native and s.name in native[1]:
                offset, hours = native
                contract["broker_utc_offset_seconds"] = offset
                ny_offset = int(datetime.now(NY).utcoffset().total_seconds())
                adjusted = {}
                for day, windows in hours[s.name].items():
                    # Multiple/discontinuous/overnight sessions require explicit handling.
                    if len(windows) != 1: continue
                    start, end = (v-offset+ny_offset for v in windows[0])
                    if 0 <= start < end < 86400:
                        adjusted[str((day+6)%7)] = [f"{start//3600:02d}:{start%3600//60:02d}", f"{end//3600:02d}:{end%3600//60:02d}"]
                contract["sessions"] = adjusted
            else: contract["verified"] = False
            # A broker symbol with the same spelling is not proof of the underlying.
            verified = contract.get("verified") is True and bool(contract.get("evidence"))
            required = ("commission_round_turn_per_lot", "slippage_points", "max_margin_rate", "financing_long_per_lot_day", "financing_short_per_lot_day", "broker_utc_offset_seconds", "sessions")
            complete = all(k in contract for k in required)
            numeric = complete and all(isinstance(contract[k], (int, float)) and math.isfinite(contract[k]) for k in required[:-1])
            valid = numeric and all(contract[k] >= 0 for k in required[:5]) and 0 < contract["max_margin_rate"] <= 1 and bool(contract["sessions"])
            if not (verified and complete and valid):
                issues.append(f"{ticker}: costs, financing, margin schedule, sessions and underlying evidence require verification")
            mappings[ticker] = {"symbol": s.name, "description": s.description, "currency": s.currency_profit,
                "contract_size": s.trade_contract_size, "minimum": s.volume_min, "step": s.volume_step,
                "maximum": s.volume_max, "tick_size": s.trade_tick_size, "point": s.point,
                "verified": bool(verified and complete and valid and s.currency_profit == "USD" and s.trade_contract_size > 0 and s.volume_step > 0),
                "trade_mode": s.trade_mode, "kind": spec["kind"], **{"costs": contract}}
        positions, orders = self.positions(), self.orders()
        magic = {self.cfg["magic_day"], self.cfg["magic_swing"], self.cfg['magic_investment']}
        foreign = any(p.magic not in magic for p in positions + orders)
        if foreign: issues.append("FOREIGN_EXPOSURE: existing positions/orders belong to another system")
        if not self.cfg.get("expected_login"): issues.append("ACCOUNT_NOT_PINNED: expected_login must match the inspected demo")
        if not positions and abs(account.balance - self.cfg["starting_balance"]) > .01:
            issues.append("STARTING_BALANCE_MISMATCH")
        return {"account": {"login": account.login, "server": account.server, "currency": account.currency,
                            "balance": account.balance, "equity": account.equity, "margin_free": account.margin_free,
                            "demo": True, "hedging": account.margin_mode == self.mt.ACCOUNT_MARGIN_MODE_RETAIL_HEDGING},
                "mappings": mappings, "issues": issues, "foreign_exposure": foreign,
                "positions": [p._asdict() for p in positions]}

    def rank(self, mappings):
        result = []
        for ticker, m in mappings.items():
            if not m["verified"]: continue
            name = m["symbol"]
            if not self.mt.symbol_select(name, True): continue
            quote = self.mt.symbol_info_tick(name)
            bars = self.mt.copy_rates_from_pos(name, self.mt.TIMEFRAME_M15, 1, 64)
            if quote is None or bars is None or len(bars) < 30: continue
            if not 0 <= time.time() - (quote.time-m["costs"]["broker_utc_offset_seconds"]) <= 30 or quote.ask < quote.bid: continue
            tr = [max(bars[i]["high"] - bars[i]["low"], abs(bars[i]["high"] - bars[i-1]["close"]), abs(bars[i]["low"] - bars[i-1]["close"])) for i in range(1, len(bars))]
            atr = sum(tr[:14]) / 14
            for value in tr[14:]: atr = (13 * atr + value) / 14
            price = (quote.ask + quote.bid) / 2
            if price >= 10 and 0 <= quote.ask - quote.bid <= min(price * .001, atr * .15):
                result.append((ticker, float(atr / price)))
        return [t for t, score in sorted(result, key=lambda item: (-item[1], item[0]))]
