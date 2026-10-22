# DualTrade

Demo-only MetaTrader 5 trading research system combining a native MQL5 Expert Advisor, a Python collector, official-document reviews and bounded AI decisions.

**Research software with known correctness gaps; profitability and production readiness have not been established.** Read [KNOWN_ISSUES.md](KNOWN_ISSUES.md) before experimenting. The example risk limits are aggressive demo research parameters, not recommendations.

## Architecture

- `MQL5/Experts/DualTrade.mq5`: sole owner of orders, protective stops, exits and account risk checks.
- `dualtrade/`: research collectors, document evidence, strategy readiness, observation and agent bridge.
- `scripts/event-agent/`: local candidate watcher and bounded review dispatch.
- `tests/`: Python regressions and a C# compatibility harness for selected production MQL function bodies.

Day, technical swing and fundamental investment sections share native risk controls. Missing, stale or inconsistent evidence is intended to block new entries. AI reviews are bounded inputs; they are not calibrated win probabilities or permission to bypass native checks. See [AGENT_EXECUTION.md](AGENT_EXECUTION.md), [AI_NEWS.md](AI_NEWS.md), [THREE_BOOKS.md](THREE_BOOKS.md) and [INVESTMENT_MODEL.md](INVESTMENT_MODEL.md).

## Local development

Python 3.11+ is required. Windows, MetaTrader 5 and MetaEditor are needed for broker integration; pure Python work can run without a terminal. A Windows .NET Framework C# compiler enables the compatibility tests.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
New-Item -ItemType Directory -Force build | Out-Null
.\.venv\Scripts\python.exe -m pytest -q
```

Install from `pyproject.toml` for a fresh environment. `requirements.lock.txt` records the original Windows environment and may contain versions unsuitable for other platforms; it is not a portable lock.

For an isolated demo setup, copy `config.example.json` to ignored `config.local.json`, configure your own terminal and demo identity, and follow [CREDENTIAL_SETUP.md](CREDENTIAL_SETUP.md). Keep order permission disabled while configuring. Provider keys belong in local environment variables or the local encrypted vault. No account identity, credentials, deployed binary, observations or activation evidence are included in this repository.

Full functional acceptance additionally requires local compilation, native Strategy Tester integration and current source/configuration evidence. Activation also needs a complete healthy observation bound to the decision mechanism; proven non-decision maintenance can reuse that observation while still requiring current deployment checks. See [TUNING.md](TUNING.md). `scripts/check.ps1 -NativeIntegration` is a broker-environment workflow, not the ordinary CI command. A passing unit suite does not enable trading or establish profitability. The historical, date-bound demo exception implemented in source carries no authorization for a new installation.

Prepare the existing SEC analysis cache ahead of the session with
`python scripts/prepare-fundamentals.py`. It uses the same point-in-time
transformations and filing-specific cache keys as the collector, preserves
missing fields, and cannot send orders. Newer collector results win concurrent
updates; a failed preparation leaves existing caches intact. Its report is
`runtime/reports/fundamental-preparation.json`.

On a configured Windows workstation using Turkey time,
`scripts/register-fundamental-preparation.ps1` installs a daily 15:00 preparation
task. Scheduled runs skip exchange holidays and runs starting at or after
09:25 New York; keep the computer and user session available. Existing freshness
limits and checks for newly filed documents still apply during the session.
The separate document reader retains ownership of full-text reviews and
uncertainty; preparation does not mark documents reviewed or grant eligibility.

The configured stock universe contains 30 issuers, shared by SEC monitoring,
fundamental preparation and the day/swing/investment research paths:
AAPL, MSFT, NVDA, AMZN, META, TSLA, AMD, JPM, XOM, GOOG, AVGO, NFLX, ADBE,
CRM, ORCL, INTC, QCOM, MU, TXN, COST, UNH, HD, MCD, NKE, BA, CAT, CVX, KO,
PEP and V. SPY and QQQ remain separate ETF instruments. The daily frozen scan
pool selects at most ten eligible symbols; position and risk caps are
independent of universe size.

Issuer identifiers are matched to the [SEC ticker registry](https://www.sec.gov/files/company_tickers.json).
GOOG is the configured Alphabet share class; it does not alias GOOGL. XOM uses
its current holding-company CIK; missing historical metrics remain explicit.
Every stock requires healthy SEC coverage. Existing seven direct issuer feeds
and the two Fed feeds remain configured; optional issuer feeds are not invented
for the additional companies. Poll intervals remain 300 seconds for SEC and
120 seconds for feeds. The daily discovery budget is 12,000 requests versus
about 9,906 scheduled source checks before document requests on a full weekday.
Broker mappings, costs and native sessions must be verified locally for all
instruments; the example configuration grants no broker authorization.

## AI-assisted development

AI reviewers and human contributors are welcome. Start with [KNOWN_ISSUES.md](KNOWN_ISSUES.md), [CONTRIBUTING.md](CONTRIBUTING.md) and [AI_REVIEW_REQUEST.md](AI_REVIEW_REQUEST.md). Independent AI reviews have identified reproducible research-validation defects and execution-recovery gaps. Each proposed fix should include a failing regression and preserve order ownership, identity checks and risk ceilings.

Repository issues are requests for development help; opening an issue does not automatically start an external AI service. No paid AI integration is enabled by this publication.

## Documentation and attribution

Technical notes describe development behavior and must not be read as deployment or acceptance certificates. This is a sanitized source snapshot, not a backup of a running account.

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and [PROVENANCE.json](PROVENANCE.json). The bundled trader-skill modules retain their upstream MIT license. A repository-wide license has not yet been selected; public visibility alone is not a blanket reuse grant.

Supplemental Marketaux discovery is shared across the universe and refreshes
every ten minutes during the exchange session (roughly 45 requests on a normal
full day including surrounding polling windows). The local ceiling is 95
requests/day, below the free plan's 100; retries and other consumers of the same
key can consume additional capacity. Its three-article free response is not
complete news coverage for 30 stocks. Primary SEC/Fed and configured issuer-feed
monitoring remain separate.
