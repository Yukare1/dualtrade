# Politician disclosure council

The five watched households are Nancy Pelosi, Michael McCaul, Josh Gottheimer, Ro Khanna and Dan Crenshaw. This module reads public House disclosures. A reported purchase is **not a confirmed current open position**: disclosures arrive after trades, amounts are ranges, and undisclosed later activity can change holdings. There is no inside information here.

## Importance weights

The user requested political importance rather than equal votes. The initial, transparent heuristic assigns the highest documented applicable category: former Speaker = 3 points; former full-committee chair = 2; current subcommittee ranking member = 1.5; committee member = 1. This is an implementation assumption about political influence, not an official importance index or a measure of investment skill. It does not assume access to nonpublic information.

| Household | Points | Base weight | Documented role |
|---|---:|---:|---|
| Nancy Pelosi | 3 | 33.3% | [Speaker Emerita / former Speaker](https://pelosi.house.gov/biography) |
| Michael McCaul | 2 | 22.2% | [Former full-committee chair / chairman emeritus](https://mccaul.house.gov/issues/foreign-affairs) |
| Josh Gottheimer | 1.5 | 16.7% | [Intelligence subcommittee ranking member; Financial Services member](https://gottheimer.house.gov/committees-caucuses) |
| Ro Khanna | 1.5 | 16.7% | [Armed Services CITI subcommittee ranking member](https://armedservices.house.gov/news/documentsingle.aspx?DocumentID=4921) |
| Dan Crenshaw | 1 | 11.1% | [Energy and Commerce / Intelligence member](https://crenshaw.house.gov/committees) |

The versioned rules and source links live in `council.example.json`. Roles were reviewed on October 22, 2026 and must be reviewed by the next Congress, January 3, 2027. Configuration changes invalidate prior source health and observation acceptance. No household may exceed 40% of total base weight.

## Swing-entry effect

The council is an additional opposing-evidence veto after the existing technical, event and sentiment rules. It cannot generate an order, reverse a position, increase risk, loosen a stop, change day trades or trigger an exit. In particular, a politician's later sale does not close an existing bot swing. Existing protective management continues independently.

Each member household has at most one vote for a stock. Self, spouse, joint and dependent-child activity are labelled and combined; repeated transactions do not add votes. Conflicting directions on the same latest trade date abstain. More recent ambiguous equity activity invalidates an older clear vote. Options, exchanges, private assets and unresolved amendments remain context only; option purchases are never converted into share purchases or bot option orders. A sale indicates reduced reported exposure, **not an inferred short position**.

A purchase vote opposes a new technical short; a sale/reduction vote opposes a new technical long. A veto requires all of:

- At least two independent households opposing that entry.
- Active evidence covering at least 40% of the complete council's base weight.
- At least two thirds of effective directional weight opposing the entry.

Effective weight = political importance weight × `2^(-trade_age_days/45)`. Evidence expires 90 days after filing or 180 days after the trade, whichever occurs first. Those are research-evidence freshness limits, **not holding limits on bot swing positions**. Healthy but insufficient evidence is neutral. Missing/stale source coverage blocks new swing entries while day-entry and protective-exit rules remain independent.

The House index is refreshed hourly by the collector during its existing research hours. Known source documents survive outages; a successful complete refresh is required within 24 hours. Original PDF URL, DocID, filing date, trade date, owner, amount bounds, disclosure lag, SHA-256 and first-observed timestamp are preserved. A document is usable only after it was actually observed, so historical tests cannot trade on its earlier transaction date. Incomplete tables, scanned reports and unresolved amendments are quarantined instead of silently treated as no trades. Senate access is not automated.

## Commands and evidence

```powershell
.\.venv\Scripts\python.exe -m dualtrade.cli council refresh
.\.venv\Scripts\python.exe -m dualtrade.cli council status
.\.venv\Scripts\python.exe -m dualtrade.cli council report
```

The report is `runtime/reports/politician-council.md`, with structured evidence in the adjacent JSON. It includes all reported tickers, while MT5 entries remain restricted to the verified broker universe. No unsupported ticker is substituted or automatically enabled.

The fourth shadow book, `events_sentiment_council`, compares the additional veto with the existing three books using the same candidate prices and costs. It starts prospectively when the new collector runs; earlier council votes are not fabricated. Candidates from older EA journals lack contemporaneous council evidence and cannot enter its swing book. The historical technical-only replay explicitly bypasses council votes and cannot measure their benefit.

### Scanned-paper reports

Some McCaul/Khanna reports contain image pages, not extractable text. These require complete visual review; automated text extraction alone cannot establish the table's accuracy. To import a transcription, use `council-import <review.json>`. The JSON needs `year`, `doc_id`, `pdf_sha256`, `page_count`, `row_count`, `reviewer`, `reviewed_complete_document: true`, and a `rows` array. Every row must contain `owner` (blank/SP/JT/DC), `asset`, `ticker` (or null), `asset_type`, `type` (P/S/S (partial)/E), ISO `date`, ISO `notified`, original `amount` range and `status`. Include every transaction on every page, not just the desired stocks. The importer checks the official index identity and downloaded PDF hash; after import run `council refresh` again. No unreviewed or invented transcriptions are provided.

## Deployment state

Council bridge protocol 2 is supported by the current EA version 0.1.4, loaded through native MT5 MCP in observation mode. An older attached EA cannot accept protocol 2 as permission to enter. All earlier demo activation blockers still apply. The expanded council gate needs complete source coverage and a new full healthy observation session. Profitable results or superior politician selection have not been established.
