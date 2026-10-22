# News guard version 2

This is the current reporting/sentiment policy. It supersedes the six-hour news
rules in the historical release notes in README.md. The separate primary-document
economic review in INVESTMENT_MODEL.md and SEC protective exits are unchanged.

## Sources and article roles

The exact-host registry in `dualtrade/news_guard.py` contains 19 editorial and
distribution domains. The original Reuters, AP, Bloomberg, CNBC, WSJ, Barron's,
MarketWatch and FT entries are joined by Mint, Business Standard, GuruFocus,
FinanceFeeds, Fox Business, Yahoo Finance, The Manila Times, the Gleaner,
PR Newswire, Business Wire and GlobeNewswire. `SOURCE_REVIEW` retains the review
date and official links for additions. Review establishes a publication's identity
and role; it does not establish an accuracy percentage or endorse every article.

Reviewed single-source reporting can contribute with base weight 0.7; matched
independent reporting has weight 1.0. The three Dow Jones titles count as one
editorial group. Explicit wire attribution shares the Reuters/AP/Bloomberg group.
Identical copies, shared IDs, near-identical headlines and distribution copies
cannot add independent sources or story votes. Exact HTTPS host checks still
reject lookalikes, credential-bearing URLs, arbitrary subdomains and invalid ports.
Publisher names in a feed cannot establish domain identity. Unknown sources and
legacy cache rows without actual provenance remain ineligible.

Products, partnerships, contracts, earnings, guidance, layoffs, capital actions,
management, operations, legal/regulatory events and general company reporting are
supported. An unrecognized event template no longer automatically rejects a
reviewed company's news. Multi-company headlines can apply to each named company.
An incidental provider tag cannot promote an unrelated headline into company news.
Ordinary words such as `will`, `may` and `not` are not blanket rejection rules.

Analysis/ratings, press releases, rumours, corrections, broad ETF-market context
and indirect supplier/rival news remain available to research with explicit
context labels. They cannot produce a directional veto. Distributed press releases
are not independent editorial confirmation. Explicit parody/manipulation, invalid
dates/scores, wrong-company associations and conflicting reported facts remain
blocked. Admission means usable research, not proven factual truth.

## Age and veto behavior

- Retain news for 72 hours. News beyond 72 hours or with future/invalid dates is withheld.
- Eligible editorial reporting can contribute for 48 hours; its weight halves every 24 hours.
- News aged 48–72 hours is research context only. Original publication and retrieval dates are preserved.
- Identical reposts cannot rejuvenate an older known story. Corroboration compares matching periods and numeric anchors within 12 hours.
- Require three distinct eligible events and two independent editorial groups for a news veto; use weighted 70% opposition and opposing mean magnitude at least 0.5.
- Strongly opposing sentiment on one story abstains. A lone accepted report cannot veto, create an order or raise risk.
- Social retention remains six hours and uses its existing five-author rule. Feed health remains independent of article age: successful news/social collection within 20 minutes and snapshot age below 60 seconds are still required by the intraday gate.

The collector, policy boundary and report all use these limits, so a longer guard
window is not silently truncated by a six-hour cache or a second veto age check.
Cached acceptance flags are recomputed from source fields at the policy boundary.
Changing the guard invalidates previous sentiment/observation acceptance.

## Inspect and reproduce

`python -m dualtrade.cli news-report` reevaluates the current cache without a network
request. `status`/`report` include article statuses, admitted articles and veto-eligible
events. Reasons, warnings, article role, source group, age, weight and supporting
URLs remain available per article.

`python scripts/test-live-news.py` fetches a current quota-counted API sample and
writes HTML/JSON evidence under `runtime/reports`. Add `--baseline-report <report.json>`
to compare the baseline's original captured rows at the original evaluation time.
Use `--observations <sample-observations.json>` for an offline replay; add
`--retry-failed` to retry failed batches only. Original API scores, publication and
retrieval times, coverage/truncation details and failed attempts are retained.
The sample never publishes an EA snapshot, changes feed health or fabricates an
acceptance report. Its API requests share the persistent daily quota.

Synthetic adversarial tests and a small live sample cannot establish real-world
misinformation-detection accuracy. Textual matching cannot read full articles,
prove editorial originality or detect every misleading claim. The report therefore
separates research admission from active veto evidence and never labels an article
as definitely true or false merely from these rules.
