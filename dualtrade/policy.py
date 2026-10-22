"""Pure policy reference used by tests, reports, and snapshot validation."""
from dataclasses import dataclass
from math import floor, isfinite


def floor_volume(raw, minimum, maximum, step):
    if not all(isfinite(x) and x > 0 for x in (raw, minimum, maximum, step)):
        return 0.0
    value = round(floor((min(raw, maximum) + 1e-10) / step) * step, 8)
    return value if value >= minimum - 1e-10 else 0.0


def position_budget(equity, baseline, open_stop_downside, planned_open, target=40):
    return max(0.0, min(target, 80 - planned_open, equity - (baseline - 200) - open_stop_downside))


@dataclass
class DailyLedger:
    day: str
    baseline: float
    last_equity: float
    halted: bool = False
    unresolved: bool = False
    reconciled: bool = True

    def update(self, day, equity, cashflow=0.0, continuous=True, flat=False):
        self.baseline += cashflow
        if self.baseline - equity >= 200 - 1e-8:
            self.halted = True
            self.unresolved = not flat
        if day != self.day:
            if not continuous:
                self.reconciled = False
            elif not self.unresolved:
                self.baseline = equity
                self.day = day
                self.halted = False
        self.last_equity = equity


def exit_reason(style, now, session_close, *, earnings_close=0, earnings_end=0,
                macro_flatten=False, daily_halt=False, crossed=False, margin_breach=False):
    if daily_halt: return "DAILY_LOSS"
    if margin_breach: return "MARGIN_RESERVE"
    if earnings_close and earnings_close <= now: return "EARNINGS"
    if style == "day" and now >= session_close - 300: return "DAY_CLOSE"
    if style == "day" and macro_flatten: return "MACRO"
    if style == "swing" and crossed: return "DAILY_CROSS"
    return None  # Deliberately no elapsed-age or Friday exit.


def sentiment_veto(items, direction, source, now):
    if source == 'news':
        from .news_guard import news_votes, VOTE_MAX_AGE
        items = news_votes(items, now)
    max_age = VOTE_MAX_AGE if source == 'news' else 21600
    by_identity = {}
    for item in items:
        if not (now - max_age <= item["time"] <= now and item.get("reliable", False)):
            continue
        if not isfinite(item.get("score",float("nan"))) or not -1<=item["score"]<=1:continue
        if source=="news" and not item.get("publisher"):continue
        identity = item["author"] if source == "social" else item["cluster"]
        if identity and (identity not in by_identity or item["time"] > by_identity[identity]["time"]):
            by_identity[identity] = item
    values = list(by_identity.values())
    enough = len(values) >= (5 if source == "social" else 3)
    if source == "news":
        enough &= len({group for x in values for group in x['source_groups']}) >= 2
    if not enough: return False
    weight = lambda x: x.get('weight', 1.) if source == 'news' else 1.
    total_weight = sum(weight(x) for x in values)
    opposing = sum(weight(x) for x in values if x['score'] * direction < 0) / total_weight
    against = [x for x in values if x["score"] * direction < 0]
    mean = sum(x['score'] * direction * weight(x) for x in against) / sum(weight(x) for x in against) if against else 0
    return opposing + 1e-12 >= .7 and mean <= -.5 + 1e-12


def deduplicate_news(items):
    """Collapse identical URLs and near-identical syndicated headlines across feeds."""
    import re
    grouped=[]
    for item in sorted(items,key=lambda x:x['time']):
        words=set(re.findall(r'[a-z0-9]+',item.get('title','').lower()))
        for prior,prior_words in grouped:
            if prior['ticker']!=item['ticker']:continue
            same=(item.get('url') and item.get('url')==prior.get('url')) or item['cluster']==prior['cluster']
            overlap=len(words&prior_words)/max(1,len(words|prior_words))
            if same or (len(words)>=5 and len(prior_words)>=5 and overlap>=.75):break
        else:grouped.append((item,words))
    return [item for item,words in grouped]
