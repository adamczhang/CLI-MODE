# Guided Windows first-run setup

This is dependency setup after normal Codex plugin installation, not a replacement
plugin installer. Never install or sign in merely because a scan found missing tools.

## Bootstrap before Python or hooks

On Windows, before invoking the Python controller for a first-time user, run the
bundled `scripts/setup.ps1 -Action Scan` with Windows PowerShell 5.1:

```powershell
powershell.exe -NoProfile -File "<plugin-root>/scripts/setup.ps1" -Action Scan
```

This needs neither Python nor PowerShell 7, and CLI-MODE uses only the Windows
PowerShell 5.1 built into Windows: PowerShell 7 is not a prerequisite. It reports
Python (3.10+), Node.js (22.13+), npm, npx and ACPX for every backend, plus that backend's own
prerequisites. Antigravity adds its native CLI and a separately installed ACP
runtime/configuration; Claude Code adds its CLI and a verified sign-in, because
ACPX manages the Claude ACP adapter package itself. The scan is per backend and
its receipt is stored per backend.
Custom ACP launchers are preserved and reported separately; configured does not
prove authentication or connectivity. Scan makes no network requests or installs.
On other operating systems, offer manual setup; the popup is Windows-only.

## Approval and launch

Show the exact missing/failed components. Ask: "Install these missing components
in a PowerShell window and guide sign-in for the selected agent?" Explain that the user
completes browser authentication and any Windows installer prompts. The approved
wizard may add PATH entries and a managed ACPX launcher if none is configured.
Offer Install, Recheck, Manual installation, and Exit. If all tools are present
but authentication failed, offer Sign in with the same approval requirement.
Full Access is required; never change task permissions or hook trust automatically.

After explicit approval, run controller `setup-start --approved`. If Python is
missing, use Windows PowerShell with `setup.ps1 -Action Start -Approved` instead.
That launches a visible PowerShell window and immediately returns runId and statusPath.
Keep the task running. Poll `setup-status` every 5-10 seconds, not a long blocking
wait, and report component changes or user actions. Do not create an automation.
For bootstrap monitoring, use `setup.ps1 -Action Status -RunId <returned-id>`.
Do not start another installer after compaction; recover the saved run ID/status.

## Completion and recovery

On complete, controller setup-status refreshes PATH and reruns first-time-check.
Display its nested setup.activationMenu through format-menu/inline rendering.
For bootstrap completion, invoke the newly installed Python by its discovered
absolute path, open frontend, then first-time-check in this same task/workspace.
Hook approval remains user-controlled. Once ready, show Agent Settings and wait
for activation confirmation. Installer completion alone never activates an agent.

On failed, interrupted, or canceled status, show the failed component and offer:
Retry; Install manually/separately; Exit. Manual help comes from setup-manual or
`setup.ps1 -Action Manual`, including official links and the ACPX npm command.
Say: "After installing the missing components, run /cli again to resume setup."
Retry rescans and skips working tools. Never automatically retry installation.
`/cli stop` requests cancellation; an in-progress package operation may finish
before the next cancellation boundary. State that limitation; do not claim the
installer stopped merely because agent mode stopped. Close the popup to interrupt.
A closed window is detected as interrupted. Recheck completed components afterward.

Existing custom ACP launchers are never replaced or given credentials by the
wizard. Guide their separate sign-in and rerun /cli; activation verifies the actual
configured runtime. A managed new runtime uses an isolated local profile and a
standard advertised ACP authenticate method followed by session/new. No task prompt
is sent. Unsupported auth methods fail with manual instructions. Native sign-in
uses a zero-turn structured /usage response. Claude Code uses the equivalent
zero-turn local /usage command through its own backend helper.
Raw protocol output, tokens and browser authorization URLs are not saved in status.

The popup uses winget for Python/PowerShell/Node, npm for pinned ACPX, Google's CLI
installer and the official ACP registry archive. Missing winget, blocked scripts,
network issues or account restrictions lead to manual setup, not policy changes.
The installer does not bypass machine execution policy or auto-approve elevation.
A full fresh-machine browser sign-in remains an integration test; fixture tests
and existing-account scans do not establish that end-to-end result.
