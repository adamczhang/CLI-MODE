// Offline ACP provider: never authenticates, networks, or executes user tools.
import {createInterface} from 'node:readline';
import {randomUUID} from 'node:crypto';
import {existsSync, readFileSync, writeFileSync} from 'node:fs';
import {join} from 'node:path';

const statePath = join(process.cwd(), 'fixture-state.json');
const state = () => existsSync(statePath) ? JSON.parse(readFileSync(statePath, 'utf8')) : {};
const send = value => process.stdout.write(JSON.stringify({jsonrpc: '2.0', ...value}) + '\n');
const reply = (id, result) => send({id, result});
const update = (request, value) => send({method: 'session/update', params: {
  sessionId: request.params.sessionId, update: value}});
const finish = (request, stopReason = 'end_turn') => reply(request.id, {stopReason});
const message = (request, text) => update(request, {sessionUpdate: 'agent_message_chunk', content: {type: 'text', text}});
const pending = new Map();
let held;
function config(session) {
  return [
    {id: 'model', name: 'Model', type: 'select', category: 'model', currentValue: session.model,
      options: ['gemini-3.8-flash-high', 'gemini-3.8-flash-low'].map(value => ({value, name: value}))},
    {id: 'mode', name: 'Mode', type: 'select', currentValue: session.mode,
      options: ['default', 'yolo'].map(value => ({value, name: value}))},
  ];
}
createInterface({input: process.stdin}).on('line', line => {
  const request = JSON.parse(line);
  const {id, method, params = {}} = request;
  if (!method) { pending.get(id)?.(request); pending.delete(id); return; }
  if (method === 'initialize') {
    reply(id, {protocolVersion: 1, agentCapabilities: {loadSession: true}, authMethods: [],
      agentInfo: {name: 'cli-mode-offline-fixture', version: '1'}});
  } else if (method === 'session/new') {
    const sessions = state();
    const sessionId = randomUUID();
    sessions[sessionId] = {count: 0, model: 'gemini-3.8-flash-high', mode: 'default'};
    writeFileSync(statePath, JSON.stringify(sessions));
    reply(id, {sessionId, configOptions: config(sessions[sessionId])});
  } else if (method === 'session/load') {
    if (existsSync(join(process.cwd(), 'reject-load')) || !(params.sessionId in state())) {
      send({id, error: {code: -32002, message: 'Session missing'}});
    } else reply(id, {configOptions: config(state()[params.sessionId])});
  } else if (method === 'session/set_config_option' || method === 'session/set_model') {
    const sessions = state();
    sessions[params.sessionId][params.configId ?? 'model'] = params.value ?? params.modelId;
    writeFileSync(statePath, JSON.stringify(sessions));
    reply(id, {configOptions: config(sessions[params.sessionId])});
  } else if (method === 'session/prompt') {
    const sessions = state();
    sessions[params.sessionId].count += 1;
    writeFileSync(statePath, JSON.stringify(sessions));
    // The user's words: CLI-MODE adds a working-folder paragraph to each task (scripts/agent_folder.py).
    const text = params.prompt.map(item => item.text ?? '').join('').split('\n\n---\nCLI-MODE: your working folder is ')[0];
    if (text === 'lose-owner') { process.kill(process.ppid); process.exit(1); }
    if (text === 'hold' || (text.startsWith('Confirm readiness') &&
        existsSync(join(process.cwd(), 'hold-readiness')))) { held = request; return; }
    if (text === 'slow') {
      setTimeout(() => { message(request, 'slow completed'); finish(request); }, 2000);
      return;
    }
    if (text === 'activity') {
      const tool = {sessionUpdate: 'tool_call', toolCallId: 'read-file', kind: 'read',
        title: 'Read fixture source', status: 'pending', locations: [{path: 'src/fixture.js', line: 3}],
        rawInput: {secret: 'PRIVATE TOOL INPUT'}, rawOutput: 'PRIVATE TOOL OUTPUT',
        content: [{type: 'content', content: {type: 'text', text: 'PRIVATE TOOL CONTENT'}}]};
      update(request, tool);
      update(request, tool);
      update(request, {sessionUpdate: 'tool_call_update', toolCallId: 'read-file', status: 'in_progress'});
      update(request, {sessionUpdate: 'tool_call', toolCallId: 'reasoning', kind: 'think',
        title: 'PRIVATE FIXTURE REASONING', status: 'in_progress'});
      update(request, {sessionUpdate: 'tool_call_update', toolCallId: 'reasoning',
        title: 'PRIVATE FIXTURE REASONING', status: 'completed'});
      setTimeout(() => {
        update(request, {sessionUpdate: 'tool_call_update', toolCallId: 'read-file', status: 'completed'});
        update(request, {sessionUpdate: 'tool_call_update', toolCallId: 'read-file', status: 'pending'});
        update(request, {sessionUpdate: 'tool_call', toolCallId: 'command', kind: 'execute',
          title: 'echo PRIVATE COMMAND ARGUMENT', status: 'failed'});
        for (let used of [1024, 1024, 2048]) update(request, {sessionUpdate: 'usage_update',
          used, size: 8192, cost: {amount: .001, currency: 'USD'},
          _meta: {usage: {input_tokens: 120, output_tokens: 40, secret: 'PRIVATE USAGE META'}}});
        message(request, 'Activity fixture complete.');
        finish(request);
      }, 400);
      return;
    }
    if (text === 'recover-terminal') {
      pending.set('terminal-fixture', response => {
        message(request, response.error ? 'Recovered from terminal error.' : 'Unexpected terminal success.');
        finish(request);
      });
      send({id: 'terminal-fixture', method: 'terminal/output',
        params: {sessionId: params.sessionId, terminalId: 'missing'}});
      return;
    }
    if (text === 'request-permission') {
      pending.set('permission-fixture', response => {
        message(request, JSON.stringify(response.result ?? response.error));
        finish(request);
      });
      send({id: 'permission-fixture', method: 'session/request_permission', params: {
        sessionId: params.sessionId, toolCall: {toolCallId: 'edit', title: 'Edit fixture', kind: 'edit', status: 'pending'},
        options: [{optionId: 'allow', name: 'Allow', kind: 'allow_once'}, {optionId: 'deny', name: 'Deny', kind: 'reject_once'}]}});
      return;
    }
    update(request, {sessionUpdate: 'agent_thought_chunk', content: {type: 'text', text: 'PRIVATE FIXTURE REASONING'}});
    message(request, text === 'count' ? String(sessions[params.sessionId].count) : text);
    update(request, {sessionUpdate: 'agent_message_chunk', content: {
      type: 'resource_link', uri: 'https://example.invalid/artifact', name: 'Fixture artifact'}});
    finish(request);
  } else if (method === 'session/cancel') {
    const sessions = state();
    if (sessions[params.sessionId]) {
      sessions[params.sessionId].cancels = (sessions[params.sessionId].cancels ?? 0) + 1;
      writeFileSync(statePath, JSON.stringify(sessions));
    }
    if (held) { finish(held, 'cancelled'); held = undefined; }
  } else if (id !== undefined) reply(id, {});
});
