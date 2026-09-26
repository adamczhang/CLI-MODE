/** CLI-MODE's thin client of the pinned ACPX shared owner.
 * ACPX owns dispatch, strict resume and settlement. The journal supplies only
 * public content (including media omitted by ACPX 0.18's typed event stream).
 *
 * One process serves newline-delimited JSON requests from its parent, one at a
 * time, and ends each with {type: 'bridge_end', exitCode}. The loaded runtime,
 * shared runtimes and `acpx config show` output are reused across requests;
 * stdin EOF detaches from every owner and exits. Nothing is cancelled by that.
 */
import { readFile, rename, stat, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import { pathToFileURL } from 'node:url';
import { join } from 'node:path';
import { createInterface } from 'node:readline';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { createProgressProjector } from './public-progress.mjs';
import { sharedConfig } from './acpx-config.mjs';

const write = event => process.stdout.write(JSON.stringify(event) + '\n');
const run = promisify(execFile);
const modules = new Map();   // ACPX package directory -> loaded acpx/runtime module
const runtimes = new Map();  // resolved settings -> shared runtime client
const configs = new Map();   // workspace + package -> {stamps, config}
let active;                  // cancellation of the request in flight

async function runtimeModule(install) {
  const manifest = JSON.parse(await readFile(join(install.package, 'package.json'), 'utf8'));
  if (manifest.name !== 'acpx' || manifest.version !== '0.18.0') {
    throw new Error('The bound ACPX installation changed; restore acpx@0.18.0.');
  }
  if (!modules.has(install.package)) {
    const requirePinned = createRequire(join(install.package, 'package.json'));
    modules.set(install.package, import(pathToFileURL(requirePinned.resolve('acpx/runtime')).href));
  }
  return modules.get(install.package);
}

async function stamp(path) {
  if (!path) return null;
  try { const info = await stat(path); return [info.mtimeMs, info.size]; }
  catch (error) { if (error.code === 'ENOENT') return null; throw error; }
}

const stampsOf = config => Promise.all(['global', 'project'].map(scope => stamp(config.paths?.[scope])));

/** `acpx config show`, reused while the files ACPX reported are unchanged. */
async function acpxConfig(input) {
  const key = JSON.stringify([input.workspace, input.install.package, homedir()]);
  let cached = configs.get(key);
  if (!cached && input.configCache) {
    cached = await readFile(input.configCache, 'utf8').then(JSON.parse).catch(() => undefined);
    if (cached?.key !== key) cached = undefined;
  }
  if (cached && JSON.stringify(await stampsOf(cached.config)) === JSON.stringify(cached.stamps)) {
    configs.set(key, cached);
    return cached.config;
  }
  const {stdout} = await run(input.install.node,
    [join(input.install.package, 'dist/cli.js'), '--cwd', input.workspace, '--format', 'json', 'config', 'show'],
    {windowsHide: true, timeout: 15000, maxBuffer: 4 * 1024 * 1024}).catch(() => {
      throw new Error('Unable to load ACPX configuration; run setup diagnostics.');
    });
  const config = JSON.parse(stdout);
  // Stamp after reading: a file edited in between is re-read next time.
  const entry = {key, stamps: await stampsOf(config), config};
  configs.set(key, entry);
  if (input.configCache) {
    const temporary = input.configCache + '.' + process.pid + '.tmp';
    await writeFile(temporary, JSON.stringify(entry)).then(() => rename(temporary, input.configCache)).catch(() => {});
  }
  return config;
}

/** What the agent asked permission for: kind, title and the command or file, for CLI-MODE's approval question. */
function permissionRequest(message) {
  const call = message.params?.toolCall;
  if (message.method !== 'session/request_permission' || !call) return;
  const input = call.rawInput && typeof call.rawInput === 'object' ? call.rawInput : {};
  const detail = [input.command, input.cmd, input.file_path, input.path, input.url]
    .find(value => typeof value === 'string' && value.trim());
  return {type: 'permission', ...(typeof call.kind === 'string' ? {kind: call.kind} : {}),
    ...(typeof call.title === 'string' ? {title: call.title.slice(0, 300)} : {}),
    ...(detail ? {detail: detail.slice(0, 300)} : {})};
}

/** A request the turn's rule escalated (refused, not approved): the same question, from ACPX's reply. */
function escalation(message) {
  const found = message.result?._meta?.acpx?.permissionEscalation;
  if (!found || typeof found !== 'object') return;
  return permissionRequest({method: 'session/request_permission', params: {toolCall: {
    kind: found.toolKind, title: found.toolTitle, rawInput: found.toolInput}}});
}

function publicUpdate(message) {
  if (message.method === 'session/request_permission') return permissionRequest(message);
  if (message.method !== 'session/update') return;
  const update = message.params?.update;
  if (update?.sessionUpdate === 'agent_message_chunk') {
    const content = update.content;
    if (content?.type === 'text' && typeof content.text === 'string' && content.text) {
      return {type: 'message', text: content.text,
        ...(typeof update.messageId === 'string' ? {messageId: update.messageId} : {})};
    }
    if (['image', 'audio', 'resource', 'resource_link'].includes(content?.type)) {
      return {type: 'artifact', content};
    }
  }
  if (update?.sessionUpdate === 'plan' && Array.isArray(update.entries) &&
      update.entries.every(entry => entry && typeof entry.content === 'string' &&
        ['pending', 'in_progress', 'completed'].includes(entry.status))) {
    return {type: 'plan', entries: update.entries.map(({content, status}) => ({content, status}))};
  }
}

async function sharedRuntime(input) {
  const [{createSharedAcpRuntime, createAgentRegistry}, config] =
    await Promise.all([runtimeModule(input.install), acpxConfig(input)]);
  const {authCredentials} = await sharedConfig(config);
  // A turn `/cli approve` sent on carries a rule (dispatch.approval_policy): the approved kinds and reads pass,
  // everything else escalates. ACPX sends mode and rule with each prompt, so they hold for this turn only. The
  // mode is approve-all because ACPX's own file-write and terminal checks read only the mode, never the rule:
  // under approve-reads an approved write would still fail there. The rule keeps everything else refused.
  const options = {cwd: input.workspace,
    permissionMode: input.access === 'allow' || input.permissionPolicy ? 'approve-all' : 'approve-reads',
    nonInteractivePermissions: 'fail', authPolicy: 'skip', authCredentials,
    ...(input.permissionPolicy ? {permissionPolicy: input.permissionPolicy} : {}),
    timeoutMs: input.timeout * 1000, ttlMs: (input.ttl ?? 1800) * 1000};
  const key = JSON.stringify([input.install.package, config.agents, options]);
  const create = () => createSharedAcpRuntime({...options, agentRegistry: createAgentRegistry({
    overrides: Object.fromEntries(Object.entries(config.agents).map(([name, value]) => [name, value.argv ?? value.command]))})});
  // A turn's own approval rule makes a one-off client (main shuts it down after the turn): cached, every
  // different approval would leave another client in this long-lived bridge.
  if (input.permissionPolicy) return create();
  if (!runtimes.has(key)) runtimes.set(key, create());
  return runtimes.get(key);
}

async function main(input, cancellation, checkCancellation, progress) {
  const publicProgress = createProgressProjector(input.progressMode !== 'quiet');
  const runtime = await sharedRuntime(input);
  const observer = new AbortController();
  let turn, watchTask, lastCursor, observedResult = false, watchError;
  let sawStart = false, observationGap = false, repairedCursor = false, escalated = false;
  try {
    const locate = () => runtime.findSession({sessionKey: input.session, agent: input.profile, cwd: input.workspace});
    const handle = await locate();
    if (input.action === 'cancel') {
      // Session-wide cancel, like `acpx cancel -s`: best effort, by name.
      if (handle) await runtime.cancel({handle, reason: 'CLI-MODE cancel'});
      write({canceled: Boolean(handle)});
      return;
    }
    if (input.action === 'close') {
      // `acpx cancel`, `sessions close` and `status` in one warm request. The
      // owner stops and the record closes; history is kept.
      if (handle) {
        await runtime.cancel({handle, reason: 'CLI-MODE stop'}).catch(() => {});
        await runtime.close({handle, reason: 'CLI-MODE stop'});
      }
      write({status: (await locate()) ? 'open' : 'no-session'});
      return;
    }
    if (!handle || (input.recordId && handle.acpxRecordId !== input.recordId)) {
      throw new Error('The owned ACPX session is unavailable or was replaced; no prompt was submitted.');
    }
    if (input.action === 'control') {
      const [command, key, value] = input.control;
      checkCancellation();
      try {
        if (command === 'set-mode') await runtime.setMode({handle, mode: key, signal: cancellation.signal});
        else if (command === 'set' && key === 'model') await runtime.setModel({handle, model: value, signal: cancellation.signal});
        else if (command === 'set') await runtime.setConfigOption({handle, key, value, signal: cancellation.signal});
        else throw new Error('Unsupported shared runtime control.');
      } catch (error) {
        // With no running owner, ACPX 0.18.0 refuses an owner-only control before sending it
        // (requireOwnerDispatch). Nothing reached the agent: say so plainly, so the caller can wake
        // the owner and apply it, instead of treating the control as possibly delivered.
        if (error?.code === 'ACP_BACKEND_UNAVAILABLE' && /was not sent/.test(error.message ?? '')) {
          write({accepted: false, ownerRunning: false});
          return;
        }
        throw error;
      }
      write({accepted: true});
      return;
    }
    if (input.expectedSession && (handle.agentSessionId || handle.backendSessionId) !== input.expectedSession) {
      throw new Error('Bound provider conversation changed before dispatch; inspect it and stop/rebind. No prompt was sent.');
    }
    write({type: 'runtime_session', recordId: handle.acpxRecordId});
    const text = await readFile(input.promptFile, 'utf8');
    // Observe rejection immediately. Waiting for turn_result is bounded below:
    // rejected or queued-cancelled requests need not have a journal marker.
    watchTask = (async () => {
      let cursor = input.cursor;
      while (true) {
        try {
          for await (const event of runtime.watchSession({handle, ...(cursor ? {cursor} : {}), signal: observer.signal})) {
            if (event.requestId !== input.requestId) continue;
            if (event.type === 'turn_started') sawStart = true;
            if (event.type === 'message') {
              if (!sawStart) { observationGap = true; continue; }
              const refused = input.permissionPolicy && !escalated ? escalation(event.message) : undefined;
              if (refused) {
                // A request the approval didn't cover: stop here and ask, as an unapproved turn would.
                escalated = true;
                write(refused);
                runtime.cancel({handle, reason: 'CLI-MODE: approval needed'}).catch(() => {});
              }
              const update = publicUpdate(event.message) ??
                (event.message.method === 'session/update' ? publicProgress(event.message.params?.update) : undefined);
              if (update) write(update);
            }
            lastCursor = event.cursor;
            if (event.type === 'turn_result') { observedResult = true; return; }
          }
          return;
        } catch (error) {
          if (cursor && !sawStart && !repairedCursor &&
              ['WATCH_CURSOR_INVALID', 'WATCH_CURSOR_FOREIGN', 'WATCH_CURSOR_EXPIRED', 'WATCH_CURSOR_FUTURE'].includes(error.code)) {
            // Restart only observation, never the submitted turn. A complete
            // start marker is required so a truncated journal cannot look whole.
            cursor = undefined;
            repairedCursor = true;
            continue;
          }
          throw error;
        }
      }
    })().catch(error => { if (!observer.signal.aborted) watchError = error; });
    checkCancellation();
    progress.dispatchStarted = true;
    turn = runtime.startTurn({handle, text, requestId: input.requestId, mode: 'prompt', timeoutMs: input.timeout * 1000,
      signal: cancellation.signal});
    let promptStarted = false;
    const started = turn.promptStarted.then(() => { promptStarted = true; write({type: 'prompt_started'}); }, () => {});
    const drain = (async () => { for await (const ignored of turn.events) { /* journal owns presentation */ } })();
    const result = await turn.result;
    await Promise.all([started, drain]);
    if (result.status === 'cancelled' && !promptStarted) observer.abort();
    let timer;
    await Promise.race([watchTask, new Promise(resolve => { timer = setTimeout(resolve, 3000); })]);
    clearTimeout(timer);
    observer.abort();
    await watchTask;
    // The journal result is only an output-drain marker. ACPX's canonical
    // result may still fail during final checkpoint/cleanup after that marker.
    const outputComplete = observedResult && sawStart && !observationGap && !watchError;
    // Report the conversation identity after the turn, so the caller does not
    // need a separate `sessions show` process to notice a provider change.
    const after = await runtime.findSession({sessionKey: input.session, agent: input.profile, cwd: input.workspace})
      .catch(() => undefined);
    const providerSession = after && (after.agentSessionId || after.backendSessionId);
    // Stopped for an approval: reported as ACPX reports a turn stopped by a permission request, also when the
    // agent ended its turn itself before the cancel arrived (it was still refused, and the user decides).
    const reported = escalated && result.status !== 'failed' ? {...result, status: 'failed', error: {
      code: 'PERMISSION_PROMPT_UNAVAILABLE', message: 'The agent asked for a permission this turn does not allow.'}}
      : result;
    write({type: 'runtime_result', result: reported, outputComplete,
      ...(providerSession ? {providerSession} : {}),
      settled: observedResult || result.status === 'completed' || result.status === 'cancelled',
      ...(watchError ? {observationError: {code: watchError.code, message: watchError.message}} : {}),
      ...(outputComplete ? {cursor: lastCursor} : {resetCursor: repairedCursor})});
  } finally {
    observer.abort();
    if (turn) {
      await turn.closeStream().catch(() => {});
    }
    if (watchTask) await watchTask;
    // Detaches this turn's one-off client; the owner keeps the session, as for any other detach.
    if (input.permissionPolicy) await runtime.shutdown().catch(() => {});
  }
}

async function serve(request) {
  const cancellation = new AbortController();
  const progress = {dispatchStarted: false};
  active = cancellation;
  const checkCancellation = () => {
    if (request.cancelFile && existsSync(request.cancelFile)) cancellation.abort();
  };
  let exitCode = 0;
  checkCancellation();
  const timer = setInterval(checkCancellation, 40);
  try {
    await main(request, cancellation, checkCancellation, progress);
  } catch (error) {
    // This envelope is a bridge failure, never evidence that an accepted prompt
    // stopped. Keep raw diagnostics and arbitrary private protocol traffic out.
    write({type: 'runtime_failure', dispatchStarted: progress.dispatchStarted,
      message: error.message ?? 'ACPX bridge failed.'});
    exitCode = 1;
  } finally {
    clearInterval(timer);
    active = undefined;
  }
  write({type: 'bridge_end', exitCode});
}

process.stdout.on('error', () => active?.abort());
for await (const line of createInterface({input: process.stdin, crlfDelay: Infinity})) {
  if (!line.trim()) continue;
  let request;
  try { request = JSON.parse(line); }
  catch {
    write({type: 'runtime_failure', dispatchStarted: false, message: 'Malformed bridge request.'});
    write({type: 'bridge_end', exitCode: 1});
    continue;
  }
  await serve(request);
}
// Detach only: shared owners keep any accepted turn running.
await Promise.allSettled([...runtimes.values()].map(runtime => runtime.shutdown()));
