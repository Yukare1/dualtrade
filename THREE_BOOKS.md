# DualTrade 0.3.0: day, technical swing and fundamental investment

Three independent one-position slots use one native `SubmitOrder` function and
one account risk engine. This is demo-only research software.

## Ownership and migration

- Day: magic 26102201; existing closed-M15/H1/H4 strategy retained.
- Technical swing: magic 26102202; closed-H4 EMA pullbacks and Donchian retests,
  daily trend, long-only, closed-daily SPY trend and 20-session relative strength.
  Stock and benchmark must have the same completed daily date. This section is
  independent of the intraday volatility pool and Reddit availability.
- Fundamental investment: new magic 26102203, policy 200. Existing financial
  scoring, valuation, material-news review, 180-day cost reserve, earnings holding
  policy and thesis exits are retained; see INVESTMENT_MODEL.md.

Existing policy-200/`DT:INV` holdings under magic 26102202 remain investments.
Pending old investment intents migrate durably into slot 2, retaining the original
magic. Existing technical swing holdings keep their old exit policy. Same-symbol
overlap is rejected across sections; an unknown order outcome blocks all entries.

Swing needs fresh contracts, SEC/earnings, macro and price context. Available
adverse sentiment/council evidence may veto entry. Missing social evidence cannot
disable swing/investment protection. Broker tick volume is not exchange volume;
exchange-volume/VWAP confirmation is not implemented in this revision.

## Aggressive shared risk profile

The example research configuration caps lifetime loss at USD 1,000. Dollar ceilings remain;
equity fractions reduce risk after losses, and never increase those ceilings.

| Constraint | Maximum |
|---|---|
| Target planned loss per new position | min($40, 4% current equity) |
| All three sections together | min($80, 8% current equity) |
| Same risk group | 6% equity planned risk |
| Daily equity loss | min($200, 20% cashflow-adjusted daily baseline) |
| Weekly equity loss | $600 |
| Loss against initial capital, adjusted for external flows | $1,000 |
| Loss from cashflow-adjusted equity peak | $1,000 |
| Gross nominal exposure, account / same group | 4x / 2x equity |
| Common price shock | at least 8% nominal move; stress-loss budget 20% equity |
| Free margin / new attempts / same-symbol cooldown | 20% / 5 per NY day / 1 hour |

These are explicit research assumptions, not calibrated forecasts. Technology
names and QQQ share a risk group; other groups follow configured sectors. All
positions consume a common-shock budget. This is a concentration/stress control,
not an estimated correlation matrix.

Native contract profit/margin, costs and financing determine size, rounded down.
Original planned risk and additional downside from current prices are separate.
Minimum-lot or remaining-capacity failures reject an order. Actual fills are
checked again, and excessive/unprotected fills are closed through reconciliation.

Total-loss and peak-loss halts persist across rollover/restart. Cashflows adjust
all baselines without increasing the $1,000 loss allowance. Unknown adjustments
and disconnected boundaries block entries. Last accepted protective risk limits
persist while the research bridge is unavailable. Gaps and execution failures can
exceed planned losses; these budgets do not guarantee a realized-loss ceiling.

## Operation and evidence

Protocol 4 carries independent readiness, observation and selection for every
section, plus shared risk and per-contract groups/swing context. Old/incomplete
snapshots cannot authorize entries. The independent control file names the exact
selected styles. `stop` blocks entries while keeping protective management.

```
python -m dualtrade.cli swing-report
python -m dualtrade.cli start-demo --style swing
python -m dualtrade.cli start-demo --style investment
python -m dualtrade.cli start-demo --style all
```

Account automation requires the pinned USD hedging demo, explicit EA order
permission, current-source acceptance and a healthy observation session for each
selected section. New source invalidates old acceptance/observation. Defaults
do not enable account orders.

The integration expert refuses initialization outside Strategy Tester. It tests
production automatic submission for all sections, broker stops, independent exit
policies, protection with research offline, old investment migration, shared
capacity, sector limits and unknown order outcomes. Controlled simulator faults
are not physical network outages or a real process crash. Functional evidence
does not establish strategy expectancy or profitability.

Historical shadow/replay books remain separate research instruments and do not
model the new three-section account as one portfolio. Actual demo deal reporting
separates all three sections; no combined profitability result is claimed.
