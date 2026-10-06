# Security Automation

Status: **active** as of 2026-09-08. The initial Betterleaks rollout was merged through PR #48.

## Betterleaks controls

This repository now uses Betterleaks as a local pre-commit secret scan and as a pull-request GitHub Actions gate.

### Local pre-commit scan

- Configuration: `.pre-commit-config.yaml`
- Betterleaks release: `v1.8.1`
- Pinned commit: `5eab48332cc48565864514e3bc6de89df091a7c4`
- Hook: `betterleaks-system`
- Scope: staged changes only
- Output: redacted

Each local clone must install the hook once:

```bash
pre-commit install
```

Normal commits then run the staged Betterleaks scan automatically.

Manual staged scan:

```bash
betterleaks git . --pre-commit --staged --redact
```

### Pull-request GitHub Actions scan

Workflow: `.github/workflows/betterleaks.yml`

The workflow:

- runs on `pull_request`;
- uses `permissions: contents: read`;
- uses full Git history only to resolve the requested commit range;
- installs the pinned Betterleaks version;
- scans only `${BASE_SHA}..${HEAD_SHA}` for the pull request;
- uses `--redact` so detected values are not printed in full;
- does **not** enable credential validation or outbound secret-verification requests.

The PR gate intentionally scans newly introduced commits rather than treating the repository's full historical scan as a blocking check. Previously reported historical findings were separately reviewed as synthetic, test, documentation, or example values.

## Operating rules

- Do not bypass or remove the Betterleaks gate merely to obtain a green build.
- Do not broadly disable generic secret/password rules.
- If a known synthetic fixture needs filtering later, prefer a narrow, reviewed, repo-specific filter.
- Never paste an unredacted suspected credential into issues, PR comments, logs, screenshots, or reports.
- If a finding may be a real credential, stop the change, verify ownership, rotate/revoke the credential as appropriate, and then remediate the source/history based on exposure.
- A Git history rewrite is an incident-response tool, not the default response to a benign test/example finding.

## Known scope limitation

The local pre-commit hook protects what is staged for commit. It does not claim to scan every unstaged or untracked local file. Review `git status` before commits and keep secrets in ignored environment/secret stores rather than relying on scanning alone.

## Manual audit

A periodic full-history review can still be run manually:

```bash
betterleaks git . --redact
```

Treat that command as an audit/triage tool. Do not convert it into a failing PR gate without first accounting for reviewed historical fixtures and false positives.
