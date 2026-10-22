"""Shared account policy; the native EA remains the sole execution owner.

The aggressive profile preserves the existing dollar ceilings. Equity fractions
only reduce those ceilings as capital shrinks; they never increase them.
"""
from __future__ import annotations
from dataclasses import dataclass
from math import isfinite

DEFAULTS = dict(position_fraction=.04, aggregate_fraction=.08,
                sector_fraction=.06, daily_fraction=.20,
                max_lifetime_loss=1000., max_drawdown=1000.,
                max_weekly_loss=600., gross_exposure_multiple=4.,
                sector_exposure_multiple=2., stress_fraction=.08,
                stress_budget_fraction=.20)
FIELDS = tuple(DEFAULTS)


def options(cfg=None):
    return {**DEFAULTS, **(cfg or {}).get('shared_risk', {})}


def validate(cfg):
    raw = cfg.get('shared_risk', {})
    if set(raw)-set(DEFAULTS):
        raise ValueError('Unknown shared risk setting')
    p = options(cfg)
    for key, ceiling in DEFAULTS.items():
        value = p[key]
        valid_range = (ceiling <= value <= .30) if key == 'stress_fraction' and isinstance(value, (int, float)) else (isinstance(value, (int, float)) and 0 < value <= ceiling)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or not valid_range:
            raise ValueError('Invalid or increased shared risk limit: '+key)
    if p['position_fraction'] > p['aggregate_fraction'] or p['sector_fraction'] > p['aggregate_fraction']:
        raise ValueError('Position/sector risk exceeds account risk')
    return p


def budget(equity, daily_base, planned, downside, *, capital_base,
           peak, week_base, sector_planned=0., cfg=None, target=40.):
    """Money available for another position, including all three books.

    downside is additional loss from current executable prices to stops, not
    original entry risk. The two quantities must not be substituted.
    """
    p = options(cfg)
    values = (equity, daily_base, planned, downside, capital_base, peak,
              week_base, sector_planned, target)
    if not all(isfinite(v) for v in values) or equity <= 0 or min(planned, downside, sector_planned) < 0:
        return 0.
    daily = min((cfg or {}).get('risk', {}).get('daily_loss', 200.), daily_base*p['daily_fraction'])
    aggregate = min((cfg or {}).get('risk', {}).get('aggregate', 80.), equity*p['aggregate_fraction'])
    return max(0., min(target, equity*p['position_fraction'],
        aggregate-planned, equity*p['sector_fraction']-sector_planned,
        equity-(daily_base-daily)-downside,
        equity-(capital_base-p['max_lifetime_loss'])-downside,
        equity-(peak-p['max_drawdown'])-downside,
        equity-(week_base-p['max_weekly_loss'])-downside))


@dataclass
class AccountLedger:
    """Persistent reference model for account boundaries and halt tests."""
    capital_base: float
    peak: float
    week_base: float
    week: int
    hard_halt: bool = False
    week_halt: bool = False
    unresolved: bool = False

    def mark(self, equity, week, *, cashflow=0., continuous=True, cfg=None):
        p = options(cfg)
        self.capital_base += cashflow
        self.peak += cashflow
        self.week_base += cashflow
        self.peak = max(self.peak, equity)
        self.hard_halt |= (self.capital_base-equity >= p['max_lifetime_loss'] or
                           self.peak-equity >= p['max_drawdown'])
        self.week_halt |= self.week_base-equity >= p['max_weekly_loss']
        if week != self.week:
            if not continuous:
                self.unresolved = True
            elif not self.unresolved and not self.hard_halt:
                self.week, self.week_base, self.week_halt = week, equity, False

