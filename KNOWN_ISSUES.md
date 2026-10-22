# Initial AI review findings

Reviewed 2026-10-22. These findings remain open in this source snapshot. Reproduced validation defects are distinguished from static execution-path findings. No running bot was modified during the review.

## P1: Observation certificate can be rebound after the window

`dualtrade/collector.py:119-126`: validity/hash checks occur inside the observation window, but a later same-day call can overwrite the pass certificate with a new hash after the window. An in-memory reproduction completed a session with hash A, then called after finish with hash B and `valid=False`; B received acceptance. Require the persisted observed hash to match the certificate and reject rebinding. Test valid and invalid later samples for day, swing and investment.

## P1: Truncated economic anchors can reverse guidance meaning

`dualtrade/ai_news.py:111-117` and `dualtrade/material_news.py:24-30,65`: substring matching accepts `$12` inside `$120 million`. A reproduced assessment of guidance rising from $100 million to $120 million supplied `after=12`, `after_text="$12"`, unit USD million; validation accepted it and classification returned `exit`. Validate full numeric tokens, currency and scale in both import paths. Test digit/decimal prefixes, omitted scales, non-USD currencies and full matching amounts.

## P1: Partial position reductions lack completion reconciliation

`MQL5/Experts/DualTrade.mq5:642-651`: static review found DONE_PARTIAL leaves the sent/pending flags set. If actual volume remains above target, later calls return without completing the reduction. Persist and reconcile order/deal status and actual volume before retrying residual work. Cover partial/delayed completion, cancellation, restart and subsequent full closure in native tests.

## P2: Definitive no-fill rejections can leave entry intents blocked

`MQL5/Experts/DualTrade.mq5:950` and `610-628`: static review found rejection handling omits INVALID_PRICE, INVALID_FILL, PRICE_OFF and REQUOTE. Reconciliation expects fill then closure, so zero-fill rejects can remain blocked indefinitely. Classify definitive rejections separately from uncertain outcomes, clear only with sufficient evidence and never resend a consumed candidate. Test broker retcode fixtures and restart.

## P2: Agent evidence accepts stale or gapped chart history

`dualtrade/agent_bridge.py:112-124`: count, validity, uniqueness and closed-bar checks do not establish that the last closed bar is current or required in-session bars are present. Add session/calendar-aware freshness and coverage validation. Test fresh quote with stale history, missing intraday bars and legitimate overnight/weekend gaps.
