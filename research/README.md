# Offline frequency research

`frequency_lab.py` is a fixed-parameter diagnostic, **not deployable trading code**.
It imports no live bot modules and cannot place orders or request AI reviews.

The strategy forms a range from the first two complete 15-minute CFD bars,
09:45 and 10:00 New York on ordinary sessions. A subsequent closed-bar crossing
of either range edge creates a candidate for the next bar's open. The opposite
range edge is the stop; the target is 2R. There is no parameter search.

One position may be held across the entire universe, with at most five entries
per session and a one-hour cooldown per symbol. The cost gate is at most 0.20R.
The broker's current commission and slippage assumptions are charged to a fixed
one-lot reference trade; spread comes from the fully closed signal bar. Zero or
missing spread rejects the candidate. These are unit-R diagnostics, not account
returns; lot affordability and margin are not simulated.

Broker timestamps are interpreted as Europe/Athens wall time, then mapped to
the XNYS calendar. Entry must precede session close minus 35 minutes (15:25 on
normal days). Positions close at the open of the final complete M15 bar, close
minus 15 minutes (15:45), before the 15:50 deadline. Early closes use the same
relative cutoffs. An absent held-position bar produces an unresolved trade,
blocks the rest of the session and contributes no invented P&L. Missing range
bars invalidate that symbol's range. No session-completeness filter uses future
bars to decide earlier entries.

At each bar open, existing scheduled exits and opening gaps are handled first,
then eligible next-open entries. Intrabar stops precede targets when both are
touched; target gaps receive no improvement. A position exiting intrabar cannot
fund a new position at that same bar's earlier open.

Run using explicit local input paths:

```powershell
python research/frequency_lab.py --history PATH_TO_HISTORY --broker PATH_TO_PREFLIGHT_JSON --output PATH_TO_REPORT
```

The report includes input hashes, a chronological 70/30 split, candidate versus
cost-eligible versus executed frequency, and 1x/2x modeled costs. Parameters are
fixed across both split periods. The current stock universe has survivorship
bias, histories have unequal coverage, and current costs are not historical
quotes. Event/AI gates, shared account USD risk, margin, execution latency,
liquidity and market impact are absent. Tick and real volume are not used.
Do not interpret this simulation as evidence of real fills or trading readiness.

The `cost_2x` run reapplies the cost gate, which can change selected trades.
The `cost_1x` summary separately includes doubled costs on its unchanged trade
set, allowing a direct cost sensitivity comparison without selection changes.
