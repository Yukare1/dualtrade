# Fundamental investment policy 200 (introduced in 0.2.0)

Version 0.3.0 gives this policy its own investment slot alongside day and technical
swing. Existing policy-200 holdings under the old swing magic remain investments.
New entries use the investment magic. The shared account controls documented in
[THREE_BOOKS.md](THREE_BOOKS.md) additionally constrain all new entries; the
financial and thesis rules below remain the investment policy.

The investment slot is long-only and separate from day and technical swing. This
is research software: automatic account orders require validated acceptance,
and a healthy observation session and current-source acceptance are still required.
The 0.1.4 technical backtest does not measure this model's performance.

## Decisions

Universe: AAPL, MSFT, NVDA, AMZN, META, TSLA, AMD, XOM. JPM and ETFs are not
scored as industrial companies. The investment universe is independent of the
intraday 09:45 volatility pool. Investment data and execution use the same pinned
USD hedging demo account as the existing bot.

Eight aligned quarters are required. USD flows are converted from year-to-date
amounts into quarters; future filings and restatements are excluded until their
acceptance time. Old submissions are retrieved and cached. Missing debt, cash,
share counts or cash flows remain missing; the bot must not invent a zero.

Each of five components contributes 0, 10 or 20 points:

| Component | 20 | 10 | 0 |
|---|---|---|---|
| TTM revenue growth vs previous TTM | >=5% | >=0% | negative |
| TTM net-income growth vs previous TTM | >=5% | >=0% | negative |
| TTM operating margin | >=15% | >=10% | below 10% |
| TTM free-cash-flow margin | >=10% | >=5% | below 5% |
| Net debt / TTM FCF | <=2 | <=4 | above 4 |

Normal entries require score >=70, positive profit, operating income and FCF,
and positive prior-year comparison profit. Indicative equity value is the lower
of 25 times profit and 20 times FCF, divided by verified shares (the larger
diluted count is used when available). Normal entries require a 15% discount.
These are explicit research assumptions, not a calibrated forecast of returns.

## Material news

The existing headline credibility and sentiment filter remains separate. Material
events need primary economic evidence; a sentiment score cannot invent contract
revenue, profitability or a guidance change. The investment report exposes a
primary-review queue and news coverage/truncation, including API quota outages.
News collection targets five minutes inside research hours within the shared
90-request daily Marketaux budget. It is not a real-time news service.

SEC 8-K bankruptcy/non-reliance events are collected automatically. Other
economic facts are imported as complete, reviewed structured events. The importer
fetches the actual SEC/issuer primary document, disallows redirects and unrelated
hosts/CIKs, verifies the quoted passage and economic anchors, and records its hash.
This explicit review boundary is necessary for nonstandard contract terms and
guidance tables; a headline alone never becomes an actionable financial fact.

Commands (from this project):

```powershell
.\.venv\Scripts\python.exe -m dualtrade.cli investment-report
.\.venv\Scripts\python.exe -m dualtrade.cli material-import path\to\review.json
.\.venv\Scripts\python.exe -m dualtrade.cli status
.\.venv\Scripts\python.exe -m dualtrade.cli report
```

Review fields: `ticker`, `kind`, `event_key` (stable for this event, including
updates), `source` (primary HTTPS article URL), `published_at` (UTC Unix seconds),
`reviewed: true`, `reviewer`, and `quote` (an exact complete passage).
Guidance adds `metric` (`net_income` or `fcf`), `period`, `unit`, `before`, `after`,
`before_text`, `after_text`. The review must compare the same period and unit;
numbers are in those units and text anchors must occur in the quotation.
Contracts add `revenue_impact` (USD revenue for the coming twelve months),
`revenue_impact_text`, `months: 12`, `firm: true`. Maximum framework values,
optional purchases, multi-year totals and profit guesses are not firm revenue.
Corrections/conflicts must use the same `event_key`; they disable positive entry.
Primary links and excerpts are untrusted data, never instructions.

- Guidance up >=10%, or binding next-12-month contract revenue >=10% of TTM
  revenue: early-entry evidence, valid for 72 hours.
- Guidance down 10–25%, or confirmed contract-revenue loss 10–25%: reduce once
  to half the current volume. At >=25%, exit completely.
- Bankruptcy, non-reliance and primary-confirmed core business bans: exit.
- Product launches, layoffs, acquisitions, lawsuits, analyst price targets and
  rumors are context until their economic effect can be established.

Early entries require score >=60, leverage <=4, all profitability checks, price
no higher than indicative value, and complete current news coverage. Price must
not exceed the pre-event completed M15 close by either 5% or 1.5 daily ATR. The
reference price is read from broker history, not trusted from the review file.
An event cannot open another early position after its first order attempt.

An early thesis is reviewed at the next financial quarter or after 90 days. It
becomes an ordinary holding if score >=70; otherwise it exits. There is no
automatic averaging down or topping up.

## Risk and exits

Normal nominal exposure <=25% of equity; early exposure <=12.5%. Planned risk
<=40 USD normally, <=20 USD early, <=80 USD for the whole account. The existing
200 USD daily equity-loss halt, five-entry counter, one-hour cooldown and 20%
margin reserve remain authoritative as ceilings, further reduced by shared equity
fractions. One intraday, one technical swing and one investment slot;
the same symbol cannot occupy both.

Initial stop: four closed daily ATR below entry, never loosened. The cost/risk
test reserves 180 calendar days of financing (not 180 trading days), commission
and slippage. Total cost must be <=5% of notional and net reward to indicative
value / stop risk >=1.5. Financing is rechecked daily; insufficient reserve
reduces exposure or closes a below-minimum-lot position. Credit is never assumed.

Hold through earnings, ordinary session closes, weekends and daily EMA crosses.
No technical 2R target or trailing rule is imposed on the investment slot. Exit
when a closed daily price reaches indicative value, TTM profitability/FCF turns
nonpositive, two distinct consecutive quarters score below 50, or material news
breaks the thesis. Existing broker stops, margin and account-loss protections
always take precedence.

Material reductions persist a target quantity and event ID before execution.
Duplicate reports cannot repeatedly halve the holding. An uncertain reduction
blocks new entries and waits for position/history reconciliation; it is never
blindly repeated. A close request survives restart and stale research.

Politician council and validated opposing sentiment can veto new purchases.
Missing Reddit/FinBERT/council data warns rather than blocks the investment
section. Such missing evidence never disables protective management. Intraday
source requirements remain unchanged.

## Operation and validation

Bridge protocol 4 carries independent day/swing/investment readiness and selected
styles. Old protocol snapshots cannot authorize new orders. `start-demo --style
investment` checks investment observation and source-bound acceptance;
`--style day` checks the existing day requirements; default `--style all` checks
both. This does not change the EA's explicit `AllowDemoOrders=false` permission.

Legacy swing positions retain their old policy until closed. New investments
persist policy 200 against the stable position ID. Historical reports and legacy
shadow ledgers are not relabelled. Prospective `fundamental` and
`fundamental_news` portfolios share account capacity with simulated intraday
positions. Their SPY comparison retains cash and respects broker minimum volume;
it reports unavailable if a 25% allocation cannot meet that minimum.

The shadow engine samples collector quotes. Intrapoll stop crossings, exact
rollover timing, historical dividends and fill liquidity are not reconstructed.
No historical news advantage is inferred without first-availability records.
Tests and native tester assertions establish behavior, not investment expectancy.
