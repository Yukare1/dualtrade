# Reliability and trade-frequency review

This change broadens prospective scanning while retaining the existing strategy,
event, account, cost and risk gates. It does not target a compulsory trade count.

## Why entries were scarce

At the diagnostic snapshot on 2026-10-22, the inspected session had logged 48
`NO_SETUP` evaluations across three symbols and no qualified candidates. These
are snapshot counts, not final session totals. Two symbols were blocked by earnings or
unresolved material-news conditions, leaving one eligible symbol. The event
review service had also stopped. Its original termination cause was not
captured; the new service records failures and recovers from transient polling
errors without reusing a stale health decision.

The existing signal generator emits scores of at least 70, above the configured
65 threshold. Lowering that threshold would not create additional signals.
An older v0.1.4 replay over 201 sessions reported 10 day entries at baseline
costs; it is a diagnostic of that older strategy, not a current-version replay.
Lowering its net reward/risk cutoff from 1.5 to 1.4 admitted only six additional
gate evaluations, not six demonstrated executable or profitable trades.

## Prospective scan change

`research.day_pool_size` defaults to ten (valid range one through thirty).
At the existing 09:45 New York selection window, rank only symbols whose latest
same-context snapshot is at most 60 seconds old and explicitly marks both the
broker contract and entry eligibility valid. Blocked high-volatility symbols
therefore do not consume the limited scan slots. Missing evidence retries only
inside the original window. A late start never reconstructs a better pool.

The pool remains frozen for the session; newly blocked symbols are not replaced
later. Holdings remain visible for continuity. All current entry checks run
again, so selection itself grants no permission. The maximum daily entries,
per-symbol cooldown, position limits and USD risk caps are unchanged. Wider
coverage is not evidence of improved returns or several fills every day.

## Reliability repairs

- Completed observations cannot be rebound to a different decision revision.
- Economic evidence must contain complete numeric tokens with matching currency
  and scale, preventing `$12` from being accepted inside `$120 million`.
- Definitive zero-fill entry rejections release their pending intent; ambiguous
  broker outcomes remain blocked. A consumed candidate is never resent.
- Partial reductions reconcile actual order state and volume before residual
  work can retry, including restarts and later full closure.
- Agent chart evidence requires the latest expected closed bucket and continuous
  recent broker-session coverage. Overnight/weekend/holiday gaps are allowed.
  On ambiguous clock-change weekends, only current-session coverage is validated
  and every timeframe needs a completed current bucket. Older bars remain
  contextual; their historical UTC offset is not established by the current
  broker clock.
- Native diagnostics identify the gate preventing an entry once per engine/bar.
- The review service logs fatal and transient failures and invalidates cached
  health before retrying. Service deployment checks support ordinary verified
  upgrades without treating an old migration receipt as current evidence.

## Alternative researched and rejected

The offline `research/frequency_lab.py` tests a fixed opening-range breakout,
with next-open execution, one global position, five entries per day at most,
one-hour symbol cooldown and modeled spread/commission/slippage. No parameters
were selected from the held-out period.

Across 76 held-out sessions starting 2026-07-07, it generated 1,535 candidates
and 98 entries (1.29/day). Of those entries, 97 had resolved outcomes totaling
**-14.39R**; one lacked an exit bar and remains unresolved. Doubling costs on
the same resolved trades gave **-19.27R**. These incomplete unit-R results omit
live event/AI gates, margin and account sizing; they are not account returns.
The alternative stays offline because the tested result does not justify
activation. See `research/README.md` for the precise assumptions and limitations.

## Deployment

Every source revision still requires current functional acceptance and verified
deployment. Observation has a separate decision revision: a completed healthy
session may be reused after changes that do not affect the decision mechanism.
Changes to strategy, eligibility, evidence, execution, risk or decision-relevant
configuration require a new observation session. Logging or formatting changes
do not automatically establish equivalence; the recorded decision binding must
match the current reviewed implementation.

The decision binding uses a reviewed file manifest, normalized Python ASTs and
MQL token streams, plus decision configuration and model settings. Comments,
ordinary formatting and documentation do not count as decision changes.
Unrecognized production files and missing required files fail conservatively.
This is a bounded equivalence rule, not a proof that arbitrary refactoring or
dependency changes preserve behavior; mixed decision/operational changes can
still require fresh observation. Any exemption needs review of its dependencies
and side effects before it can be treated as unrelated to decisions.

Build and test the exact revision, then deploy through the guarded observation
workflow on a flat pinned demo. Reusable observation does not transfer old
candidate approvals, deployment receipts or session permission. Those remain
bound to the current full source context. Legacy evidence without a verifiable
decision binding cannot be relabelled as a successful current observation, and
dated waivers are never migrated. GitHub publication alone does not deploy an EA.
