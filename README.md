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

## AI-assisted development

AI reviewers and human contributors are welcome. Start with [KNOWN_ISSUES.md](KNOWN_ISSUES.md), [CONTRIBUTING.md](CONTRIBUTING.md) and [AI_REVIEW_REQUEST.md](AI_REVIEW_REQUEST.md). Independent AI reviews have identified reproducible research-validation defects and execution-recovery gaps. Each proposed fix should include a failing regression and preserve order ownership, identity checks and risk ceilings.

Repository issues are requests for development help; opening an issue does not automatically start an external AI service. No paid AI integration is enabled by this publication.

## Documentation and attribution

Technical notes describe development behavior and must not be read as deployment or acceptance certificates. This is a sanitized source snapshot, not a backup of a running account.

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and [PROVENANCE.json](PROVENANCE.json). The bundled trader-skill modules retain their upstream MIT license. A repository-wide license has not yet been selected; public visibility alone is not a blanket reuse grant.
