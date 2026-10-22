# Native execution and bounded agent reviews

The native MT5 EA owns all orders and protective management. Python publishes evidence-bound decisions; it does not directly place broker orders. A cloned repository contains no authorization to operate any account.

## Entry lifecycle

1. The closed-bar strategy checks structure, levels, costs, session, events and shared risk, then writes an immutable candidate.
2. `python -m dualtrade.agent_bridge pending` lists candidates. `inspect ID` produces evidence including closed M15/H1/H4 candles, native levels, quote, contract specification and document context.
3. A review binds schema, request ID, context and evidence hash; it contains APPROVE/REJECT, a bounded USD risk, `win_probability: null`, rationale and explicit checks.
4. `python -m dualtrade.agent_bridge review FILE` validates and publishes the review. It is not a fill confirmation.
5. MT5 rejects expired/reused decisions, changed context, excessive price movement and unacceptable costs. It recalculates volume from approved risk and native stop distance, rounds down and checks all native gates.

Reviews live at most 55 seconds within the 60-second fresh-bar window. `wait --timeout 50` can watch without extending either deadline. Quote freshness is separately limited. Model or scheduling latency may mean no trade.

## Activation and protection

Activation requires a user-authorized, explicitly bounded demo session, verified demo identity, current functional acceptance and deployment, valid full-session observation and fresh source/reconciliation state. Do not fabricate records or broaden the historical date-bound exception. The public snapshot intentionally omits all local authorizations and acceptance files.

Observation binds the decision mechanism separately from the exact deployed
source. Preserve a genuine observation across proven non-decision maintenance
such as diagnostics, reports, tests and formatting. Signal, risk, entry/exit,
decision-data, model/prompt and decision-schema changes require new observation.
The conservative manifest in `dualtrade/decision_revision.py` defines this
boundary; unknown production code is not silently exempt. Exact-source tests,
deployment and fresh native context remain mandatory after every upgrade.
Historical certificates without a provable equivalent decision binding are
never relabeled automatically. Observation does not grant new session authority.

`stop` prevents new entries while preserving native position management. Do not remove the EA or disable its order permission as a substitute when it owns open positions. Existing broker-held protection remains subject to market execution and gaps.

Discretionary exits bind the exact position identity, ticket, volume and context. Foreign or changed positions must be rejected. Fundamental entry reviews are suspended while the technical agent-review requirement is enabled.

## Verification

Run unit tests in an isolated checkout. Native acceptance requires a separately configured demo/test environment. Read KNOWN_ISSUES.md for observation-certificate, economic-evidence, stale-history and partial-execution findings; existing controls and tests do not prove recovery from every failure.
