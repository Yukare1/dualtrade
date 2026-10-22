# Contributing

Start with an issue describing the failure, the affected source lines and a minimal reproducer. AI-assisted contributions should state what was independently verified and what remains a hypothesis.

Use an isolated checkout without private runtime/configuration files. Run `python -m pytest -q` after installing `.[test]` and creating `build/`. Add a regression that fails before the proposed fix. Include platform, command, output summary and test limits in the pull request. Native fills and recovery claims need Strategy Tester evidence beyond the compatibility harness.

Preserve demo identity checks, native order ownership, bounded review expiry, one-use consumption, immutable decisions and existing risk ceilings. Do not weaken gates to make a test pass. Do not deploy to an active terminal, change order permissions, publish private logs or copy local credentials as part of a contribution.

Keep fixes narrowly scoped and explain behavior before/after. Third-party code needs its original attribution and compatible permission. A project-wide license remains to be selected by the owner; discuss substantial code reuse before submitting it. Human review is required before merging and deployment.
