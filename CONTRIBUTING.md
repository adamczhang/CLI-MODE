# Contributing

Clone https://github.com/adamczhang/CLI-MODE and create a branch for your change.
Six backends are implemented: Antigravity CLI (`agy`), Claude Code CLI
(`claude`), Grok Build CLI (`grok-build`), Cursor CLI (`cursor`) and GitHub
Copilot CLI (`copilot`) and Codex CLI (`codex`). CLI-MODE remains the sole
user-facing skill; additional backends belong in the agent menu, follow the
[backend contract](plugins/cli-mode/codex/skills/cli-mode/references/backend-contract.md),
and must register a runtime adapter in `plugins/cli-mode/scripts/adapters.py`.
A registry record alone is discovery metadata, never an implementation.

On Windows with Python 3.13 and PowerShell available:

```powershell
python -m unittest discover -s checks -p 'test_*.py'
python scripts/package_plugin.py
python checks/package_smoke.py
```

ACPX is pinned in `plugins/cli-mode/runtime/acpx/package.json` and its
`package-lock.json`; setup installs exactly that with `npm ci`. To move to a new
ACPX release, update the version there, `ACPX_VERSION` in
`plugins/cli-mode/scripts/acpx.py` and `$AcpxVersion` in `setup.ps1`, then
regenerate the lockfile with `npm install --package-lock-only` in that folder.

`package_plugin.py` writes the archive and checksum to `dist/`, which is not
tracked. Do not commit archives: for a release, build from the tagged commit and
attach both files to that tag's GitHub Release.

Keep the offline suite green and add tests beside your change. Use only
capabilities the provider actually advertises: never substitute a working
choice for an unavailable one, and never report success you did not observe.

The package tests use isolated state and fixture agents. Do not put credentials,
auth profiles or user session state in the repository. Live installation and
browser sign-in checks are separate, opt-in integration tests. Document exactly
what was verified; do not infer a working fresh-user login from fixture tests.
Update the README/changelog when behavior changes and submit a pull request.

For explicitly authorized live account validation, follow
`checks/five-cli-validation-plan.md`. `python checks/five_cli_live.py` runs three
connected research prompts and a close/reopen check per provider, recording
evidence in disposable workspaces. `python checks/five_cli_controls.py --output
<absolute-evidence-directory>` separately tests settings, idle recovery, native
commands and cancellation. Both consume provider quota; run them sequentially.
These controller checks do not certify installed Desktop rendering or hook trust.
`python checks/live_parity_probe.py [--agents ...] [--model agent=model]` checks
that every agent gets equal support: one activation, one tool-using turn relayed as
the host would, a read-only provider command, the shared refusals, and stop.

An optional GitHub Actions template is provided at `scripts/github-actions-validate.yml`.
Actions CI is not enabled: publishing credentials lacked workflow permission.
A maintainer with that permission can place it at `.github/workflows/validate.yml`.
