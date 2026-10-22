# Local data setup

Orders remain paused while you complete these steps. You do not need a paid subscription to begin. Enter secrets only in the local form, never in chat.

1. Get a free Alpha Vantage key at https://www.alphavantage.co/support/#api-key . Use your own email and follow the provider's account steps. The documented free allowance is 25 requests/day; the collector and optional MCP share that budget. We use this for the earnings calendar, not live prices.
2. Create a free Marketaux account at https://www.marketaux.com/register . Copy the API token from your dashboard into the local form. The free plan documents 100 requests/day and three articles/request; this project caps usage at 90 including failed requests/retries. See https://www.marketaux.com/pricing and https://www.marketaux.com/documentation .
3. SEC EDGAR requires no key. In `SEC_USER_AGENT`, enter your real contact name and email, such as `Your Name your-email@example.com`. This contact string accompanies requests to the SEC; it identifies the collector to the data provider. See https://www.sec.gov/about/developer-resources .

Reddit registration, OAuth and local classifier setup are no longer required. The current context policy uses the existing full-document agent; see AGENT_EXECUTION.md.

## Enter values on this PC

From this project's folder in PowerShell:

```powershell
.\scripts\configure-credentials.ps1 -Gui
```

The masked form saves Windows CurrentUser DPAPI-encrypted values to ignored `runtime/credentials.dpapi.json`. Blank fields preserve saved values. The SEC contact and user-agent fields are ordinary text because they identify requests to their providers. Nothing is submitted to a provider just by saving. Environment variables take precedence over the vault.

Check presence without contacting providers:

```powershell
.\.venv\Scripts\python.exe -m dualtrade.cli credentials
```

After entering keys, verify them using the existing shared quotas:

```powershell
.\.venv\Scripts\python.exe -m dualtrade.cli credentials --verify
```

Only success/failure and quota counts are reported. A successful key check is not a full observation pass. The optional Alpha Vantage MCP is registered with `scripts/register-alpha-mcp.ps1` after the key is available; it must use the same budget gateway.

## Document agent and observation

The active context policy uses the existing document agent and its evidence-bound full-text reviews. It does not require a Reddit token or local FinBERT model. Missing or stale source coverage and unreviewed material documents block affected entries. Agent confidence is not calibrated forecast accuracy. Legacy classifier tests remain research utilities, not the active readiness gate.

After credentials change, restart only this project's collector to load the new environment. Keep MT5 running with the EA in observation mode. The observation must cover a complete healthy regular session with the same configuration and source revision. On an ordinary US session in October 2026, observation starts at 16:45 Istanbul time (09:45 New York); early closes and daylight-saving changes must be taken from the application calendar. A late start cannot earn a full-session pass.

`preflight` and `status` list any remaining blockers. `start-demo` remains gated by functional acceptance and full observation; saving credentials never enables orders.
