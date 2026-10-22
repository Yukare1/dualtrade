"""Prospective shadow accounting; no retrospective sentiment fabrication."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from statistics import median
import math


VARIANTS = ("technical", "events", "events_sentiment", "events_sentiment_council")


@dataclass
class ShadowPosition:
    symbol: str
    style: str
    strategy: str
    direction: int
    opened: float
    entry: float
    stop: float
    target: float
    quantity: float
    unit_value: float
    costs: float
    initial_r: float
    close_time: float
    financing_per_day: float = 0
    last_financing_day: str = ""


class ShadowLedger:
    def __init__(self, state=None):
        self.state = state or {v: {"equity": 1000.0, "positions": {}, "closed": [], "peak": 1000.0, "max_drawdown": 0.0} for v in VARIANTS}

    def enter(self, p: ShadowPosition, event_ok, sentiment_ok, council_ok=True):
        for variant in self.state:
            book = self.state[variant]
            reason = "EVENT" if variant != "technical" and not event_ok else "SENTIMENT" if variant in ("events_sentiment","events_sentiment_council") and not sentiment_ok else "COUNCIL" if variant=="events_sentiment_council" and p.style=="swing" and not council_ok else "SLOT_OR_SYMBOL" if p.style in book["positions"] or any(q["symbol"] == p.symbol for q in book["positions"].values()) else None
            if reason:
                self.reject(book, p.style, reason); continue
            book["positions"][p.style] = asdict(p)

    @staticmethod
    def reject(book, style, reason):
        key=style+":"+reason; counts=book.setdefault("rejections",{})
        counts[key]=counts.get(key,0)+1

    def offer(self, p, event_ok, sentiment_ok, spec, now, prices, unit_risk, unit_margin, council_ok=True):
        """Same price/cost model, independently constrained $1,000 portfolios."""
        from .policy import floor_volume, position_budget
        from .calendar import now_ny
        day=now_ny(now).date().isoformat()
        for variant,book in self.state.items():
            reason=None
            if variant!="technical" and not event_ok: reason="EVENT"
            elif variant in ("events_sentiment","events_sentiment_council") and not sentiment_ok: reason="SENTIMENT"
            elif variant=="events_sentiment_council" and p.style=="swing" and not council_ok:reason="COUNCIL"
            elif p.style in book["positions"] or any(q["symbol"]==p.symbol for q in book["positions"].values()): reason="SLOT_OR_SYMBOL"
            equity=book.get("marked_equity",book["equity"])
            if book.get("day")!=day and not (book.get("halted") and book["positions"]):
                book.update(day=day,baseline=equity,entries=0,halted=False)
            if book.get("halted"): reason="DAILY_LOSS"
            elif book.get("entries",0)>=5: reason="DAILY_ENTRY_CAP"
            elif now-book.get("last_entry",{}).get(p.symbol,0)<3600: reason="COOLDOWN"
            planned=sum(q["quantity"]*abs(q["entry"]-q["stop"])*q["unit_value"]+q["costs"] for q in book["positions"].values())
            downside=0; margin=0
            for q in book["positions"].values():
                bid,ask=prices.get(q["symbol"],(q["entry"],q["entry"]))
                price=bid if q["direction"]==1 else ask
                downside+=max(0,q["direction"]*(price-q["stop"]))*q["quantity"]*q["unit_value"]+q["costs"]
                margin+=price*q["quantity"]*q["unit_value"]*.2
            budget=position_budget(equity,book.get("baseline",equity),downside,planned)
            quantity=floor_volume(min(budget/unit_risk,max(0,equity*.8-margin)/unit_margin),spec["minimum"],spec["maximum"],spec["step"]) if unit_risk>0 and unit_margin>0 else 0
            if quantity<=0: reason=reason or "MINIMUM_LOT_RISK_OR_MARGIN"
            if reason: self.reject(book,p.style,reason); continue
            item=asdict(p); item["costs"]*=quantity; item["quantity"]=quantity
            book["positions"][p.style]=item; book["entries"]=book.get("entries",0)+1
            book.setdefault("last_entry",{})[p.symbol]=now

    def mark(self, symbol, bid, ask, now, *, atr=None, macro_flatten=False, issuer_exit=False, crossed=False):
        for variant, book in self.state.items():
            for style, p in list(book["positions"].items()):
                if p["symbol"] != symbol: continue
                direction = p["direction"]; price = bid if direction == 1 else ask
                # Carry estimate accrues indefinitely. Credits are never assumed.
                if p.get("financing_per_day",0):
                    elapsed=max(0,now-p.get("financing_checked",p["opened"]))
                    p["costs"]+=elapsed/86400*p["financing_per_day"]*p["quantity"]
                    p["financing_checked"]=now
                move = direction * (price - p["entry"])
                stop_hit = direction * (price - p["stop"]) <= 0
                target_hit = direction * (price - p["target"]) >= 0
                reason = "STOP" if stop_hit else "TARGET" if target_hit else None
                if style == "day" and now >= p["close_time"] - 300: reason = reason or "DAY_CLOSE"
                if variant != "technical" and (issuer_exit or (style == "day" and macro_flatten)): reason = reason or "EVENT"
                if style == "swing" and crossed: reason = reason or "DAILY_CROSS"
                if book.get("halted"): reason=reason or "DAILY_LOSS"
                if book.get("margin_breach"):reason=reason or "MARGIN_RESERVE"
                if reason:
                    pnl = move * p["quantity"] * p["unit_value"] - p["costs"]
                    book["equity"] += pnl
                    book["closed"].append({**p, "closed": now, "exit": price, "pnl": pnl, "reason": reason, "holding_seconds": now-p["opened"]})
                    del book["positions"][style]
                elif atr and atr > 0:
                    proposed = p["stop"]
                    if move >= p["initial_r"]:
                        be = p["entry"] + direction * p["costs"] / (p["quantity"] * p["unit_value"])
                        proposed = max(proposed, be) if direction == 1 else min(proposed, be)
                    if move >= 1.5 * p["initial_r"]:
                        trail = price - direction * 2 * atr
                        proposed = max(proposed, trail) if direction == 1 else min(proposed, trail)
                    p["stop"] = proposed
            # Equity marks for the complete portfolio are supplied separately.

    def mark_equity(self, prices):
        for book in self.state.values():
            floating = 0.0
            for p in book["positions"].values():
                if p["symbol"] not in prices: continue
                bid, ask = prices[p["symbol"]]
                price = bid if p["direction"] == 1 else ask
                floating += p["direction"] * (price-p["entry"]) * p["quantity"] * p["unit_value"] - p["costs"]
            marked = book["equity"] + floating
            book["marked_equity"]=marked
            margin=sum((prices.get(p["symbol"],(p["entry"],p["entry"]))[1])*p["quantity"]*p["unit_value"]*.2 for p in book["positions"].values())
            book["margin_breach"]=margin>marked*.8
            if marked<=book.get("baseline",1000)-200: book["halted"]=True
            book["peak"] = max(book["peak"], marked)
            book["max_drawdown"] = max(book["max_drawdown"], book["peak"]-marked)
            for style in ("day","swing"):
                closed=sum(p["pnl"] for p in book["closed"] if p["style"]==style)
                p=book["positions"].get(style); unrealized=0
                if p and p["symbol"] in prices:
                    bid,ask=prices[p["symbol"]];price=bid if p["direction"]==1 else ask
                    unrealized=p["direction"]*(price-p["entry"])*p["quantity"]*p["unit_value"]-p["costs"]
                curve=closed+unrealized;state=book.setdefault("style_drawdown",{}).setdefault(style,{"peak":0,"drawdown":0})
                state["peak"]=max(state["peak"],curve);state["drawdown"]=max(state["drawdown"],state["peak"]-curve)

    def summary(self):
        out = {}
        for name, book in self.state.items():
            styles = {}
            for style in ("day", "swing"):
                trades = [p for p in book["closed"] if p["style"] == style]
                holds = sorted(p["holding_seconds"] / 3600 for p in trades)
                net = sum(p["pnl"] for p in trades)
                styles[style] = {"trades": len(trades), "net_pnl": net, "expectancy": net/len(trades) if trades else None,
                                 "max_drawdown":book.get("style_drawdown",{}).get(style,{}).get("drawdown",0),
                                 "rejections":{k.split(":",1)[1]:v for k,v in book.get("rejections",{}).items() if k.startswith(style+":")},
                                 "median_hours": median(holds) if holds else None,
                                 "p90_hours": holds[max(0, math.ceil(.9*len(holds))-1)] if holds else None}
            out[name] = {"styles": styles, "open_positions": len(book["positions"]), "max_drawdown": book["max_drawdown"]}
        return out
