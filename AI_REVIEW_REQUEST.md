# Request for AI development support

Please review this demo-only MT5/Python research system and propose small, testable improvements. Focus first on the issues in KNOWN_ISSUES.md.

1. Verify the reported failure against the current source. Distinguish an executable reproducer from static reasoning.
2. Propose the smallest fix preserving native order ownership, account binding, conservative reconciliation, bounded review lifetime and risk ceilings.
3. Add a regression that fails on the baseline, plus nearby negative/boundary cases.
4. Report exact paths/lines, impact, reproduction steps, expected/actual behavior and validation limits.
5. Submit a pull request or issue comment with evidence. Do not claim broker execution or profitability from unit tests.

Priorities: observation certificate binding; numeric token/currency/scale validation; partial reduction reconciliation; definitive zero-fill rejection recovery; session-aware history freshness.

Do not access credentials, live account configuration or external AI subscriptions. Do not send orders, enable trading, alter acceptance evidence or deploy changes. This request asks for source review and proposed patches only.

Initial review: two independent AI code reviewers and one publication auditor reviewed the source on 2026-10-22. Their findings are documented, not silently applied to the running bot. External AI services must be explicitly available/configured before they can participate.
