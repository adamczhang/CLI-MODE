# CLI-MODE performance analysis — 2026-09-22

Source baseline: `a80b29a`. Windows, installed ACPX 0.18.0, default provider
models/access. The measurements below describe the initial investigation before
the v0.1.9 patch. The patch implements recommendations 1 and 2; its production
validation is recorded in [v0.1.9 validation](v0.1.9-validation.md).

## Measurement boundaries

The live probe creates isolated ownership and workspaces, activates each provider,
then alternates two Passthrough and two Direct requests: a short OK response with
no tools or edits. It closes each owned session in a finally block. Claude's first
attempt encountered an OAuth refresh conflict. After signing in again, a fresh
Claude run completed activation and three requests per mode. All owned test
sessions were closed, including the failed attempt.

- Activation: controller activate call to verified ready state. This excludes
  installation, human menu interaction, host-model decisions and usage rendering.
- Dispatch: controller send call to its dispatched event (subprocess started).
- ACP handoff: controller send call to observation of outgoing session/prompt in
  the ACPX stream. This is stronger evidence than process creation, but is not an
  independent server receipt timestamp; stream transport/buffering is included.
- First public chunk and first relay are recorded separately from handoff.
- The real Submit-to-controller interval is **not measured** by this
  harness. Consequently these are not claimed as full Desktop end-to-end times.

Each activation is one fresh plugin session, not a guaranteed cold machine,
provider installation or network connection. Warm timings are medians of only two
samples per mode (three for the Claude rerun and Node experiment); they do not
establish p95/p99 behavior. Some lightweight local
startup/hook/usage probes overlapped the baseline run, so small differences should
not be interpreted as routing-mode costs. The Node follow-up ran after baseline.

## Baseline results (seconds)

| Provider | Activation | Passthrough ACP handoff | Direct ACP handoff |
|---|---:|---:|---:|
| Antigravity | 85.40 | 1.176 | 1.174 |
| Claude Code (signed-in rerun) | 22.52 | 1.245 | 1.229 |
| Codex | 27.67 | 1.203 | 1.179 |
| Copilot | 22.29 | 1.161 | 1.161 |
| Cursor | 26.16 | 1.310 | 1.263 |
| Grok Build | 13.80 | 1.237 | 1.268 |

Controller-to-subprocess launch medians were 0.61–0.71 seconds. The difference
between modes has no consistent direction. Direct prefix parsing is not a
meaningful bottleneck in these measurements.

Ten subprocess hook invocations per mode, using isolated synthetic active state,
had medians of 0.0928 seconds (Passthrough) and 0.0927 seconds (Direct). The hook
response contained approximately 4,400–4,500 characters of routing context.
These numbers exclude the host model processing that context and deciding which
tools to invoke. In-process route/read work was roughly 0.001–0.008 seconds.

## Where the time goes

### Windows launcher overhead

The production launcher runs PowerShell, which runs the npm ACPX shim, which runs
Node. Ten alternating --version startup probes measured:

- PowerShell shim median: **0.592 seconds** (range 0.574–0.943).
- Direct installed Node entrypoint median: **0.303 seconds** (range 0.284–0.492).

Every ordinary send starts a metadata subprocess before the prompt subprocess.
Metadata after completion adds another subprocess to total turn completion time,
although it does not delay the initial handoff. This explains why a small
per-process startup saving matters repeatedly.

A live Codex follow-up used the direct Node launcher only inside the benchmark
process, with three requests per mode:

| Mode | Production launcher handoff | Direct Node handoff | Reduction |
|---|---:|---:|---:|
| Passthrough | 1.203 s | 0.683 s | 43.2% |
| Direct | 1.179 s | 0.674 s | 42.9% |

This saves approximately half a second before handoff. Activation was 25.59 seconds
versus 27.67 in the baseline, but single sequential activation trials cannot
attribute that entire difference to the launcher. Provider response variability
also prevents attributing all completion-time differences to this change.
At this experimental stage production code was unchanged; the subsequent v0.1.9
validation covers the production launcher across all six providers.

### Activation connection/control work

Antigravity's session ensure, model set and access set took **18.46, 20.60 and
19.45 seconds** respectively: **58.51 seconds** of the 85.40-second activation.
Each metadata read took approximately 0.60 seconds; the remainder was primarily
the readiness prompt and its transport lifecycle.

Codex's corresponding ensure/model/effort/access operations took approximately
3.60/2.46/2.76/2.76 seconds. Cursor's ensure/model/mode operations took
4.91/4.64/4.36 seconds. Provider startup/control behavior dominates the difference
between providers; it is not Python routing time.

Claude's signed-in ensure/model/effort/access operations took approximately
2.15/3.76/3.48/3.39 seconds. Its handoff medians were nearly identical between
modes, although first public response medians differed (7.93 seconds Passthrough,
3.03 seconds Direct). With three alternating samples, this does not establish
a routing-mode effect on provider generation time.

Installed ACPX code first attempts settings controls against a running owner and
falls back to a direct connected control session when no owner handles them.
This supports investigating a persistent activation connection. The timing data
alone does not prove which connection path every measured operation took.

### Usage and presentation

A separate native Antigravity usage lookup succeeded in **5.86 seconds**. The
current activation-message command waits for usage before producing the complete
confirmation. This can add visible delay after the provider is already ready.
It is a separate observation, not included in the activation table. Claude usage
was not measured in this investigation.

The public relay can coalesce text for up to one second. That affects visible
response timing, not when the prompt is handed to the provider. Private reasoning
and tool-event filtering should remain intact in any optimization.

## Recommended order

1. **Use a verified direct Node launcher on Windows, retaining the shim fallback.**
   Resolve the installed ACPX package and the Node executable the shim would use;
   preserve custom launchers, argv, cwd and permission flags. Benchmarking a known
   local package is not sufficient reason to hardcode an npm-global path in the
   product. This is the smallest measurable performance improvement.
2. **Reuse metadata already fetched during activation.** The final capability
   capture and final verification currently perform separate metadata reads.
   Let both consume one final record after readiness. Keep verification and
   command-capability checks; do not substitute stale capability caches merely
   to remove pre-dispatch work.
3. **Apply activation settings on one owned connection.** Prototype through a
   supported ACPX runtime interface, preserve model-dependent effort ordering,
   verify accepted values and keep the fresh readiness challenge. Skip a setting
   only when fresh runtime evidence proves the requested value is already applied.
   This is the largest potential activation gain, especially for Antigravity;
   the saved time is not yet measured.
4. **Decouple readiness feedback from quota latency.** Show verified readiness
   promptly, then update usage, or use a short bounded usage lookup with the
   existing unavailable fallback. Preserve a consistent activation layout and
   clearly distinguish unavailable usage from a provider activation failure.
5. **Instrument the host interval before restructuring it.** Record timestamps
   and one correlation ID at UserPromptSubmit, controller entry, dispatch,
   outgoing ACP prompt, first public chunk and first rendered relay. Store no
   prompt/reasoning text in timing telemetry. Repeated real Desktop trials will
   establish whether host planning/tool calls dominate the perceived delay.
   If they do, a structured send tool and shorter per-turn routing instructions
   are candidates; a persistent transport service is a larger follow-up.

Do not remove readiness checks, permission enforcement, ownership checks,
private-event filtering or command validation to obtain faster numbers.

## Reproduction and evidence

```powershell
python checks/performance_probe.py --output <new-absolute-directory>
python checks/performance_probe.py --agents codex --rounds 3 --node-launcher --output <another-new-directory>
python checks/performance_probe.py --agents claude --rounds 3 --output <new-claude-directory>
```

In v0.1.9 the benchmark uses the production launcher; `--node-launcher` additionally
requires that the verified direct Node path is available. To reproduce the earlier
PowerShell baseline, use the original launcher from `a80b29a`. Prompts consume real
account quota. Each result includes operation timings and shutdown status. Detailed local
evidence is under `%USERPROFILE%/.codex/validation/cli-mode/2026-09-22/`:
`performance-baseline`, `performance-node`, `performance-claude-signed-in`, `launcher-performance.json`,
`hook-performance.json` and `usage-performance.json`.
