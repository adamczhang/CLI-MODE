/** Bounded public activity projection. Never forward tool payloads or thoughts. */
const kinds = new Set(['read', 'edit', 'delete', 'move', 'search', 'execute', 'fetch', 'switch_mode', 'other', 'think']);
const statuses = new Set(['pending', 'in_progress', 'completed', 'failed']);
const terminal = status => status === 'completed' || status === 'failed';
const clean = (value, limit = 240) => typeof value === 'string'
  ? [...value.replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, '').replace(/[\p{Cc}\p{Cf}\p{Cs}]/gu, ' ')
      .replace(/\s+/g, ' ').trim()].slice(0, limit).join('') : '';
const count = value => Number.isSafeInteger(value) && value >= 0;

// Project only fixed labels, never arguments (which may contain credentials).
function executionTitle(update) {
  // Adapters commonly prefix the workspace. Discard that prefix, never emit it.
  const command = clean(update.rawInput?.command || update.title, 2048).replace(
    /^cd(?:\s+\/d)?\s+(?:"[^"]*"|'[^']*'|[^&;|]+?)\s*&&\s*/i, '');
  if (/^(?:python(?:3(?:\.\d+)?)?|py)(?:\.exe)?\s+-m\s+unittest(?:\s|$)/i.test(command)) return 'Run Python tests (unittest)';
  if (/^(?:python(?:3(?:\.\d+)?)?|py)(?:\.exe)?\s+-m\s+pytest(?:\s|$)/i.test(command) || /^pytest(?:\s|$)/i.test(command)) return 'Run Python tests (pytest)';
  if (/^npm(?:\.cmd)?\s+test(?:\s|$)/i.test(command)) return 'Run npm tests';
  const programs = {python: 'Python', python3: 'Python', py: 'Python', node: 'Node.js', npm: 'npm', git: 'Git', powershell: 'PowerShell', bash: 'Bash', sh: 'shell'};
  const program = command.match(/^([a-z0-9]+)(?:\.(?:exe|cmd))?(?:\s|$)/i)?.[1]?.toLowerCase();
  return programs[program] ? 'Run ' + programs[program] + ' command' : undefined;
}

export function createProgressProjector(enabled = true) {
  const tools = new Map();
  return update => {
    if (!enabled || !update || typeof update !== 'object') return;
    if (['tool_call', 'tool_call_update'].includes(update.sessionUpdate)) {
      const id = update.toolCallId;
      if (typeof id !== 'string' || !id || id.length > 200) return;
      const previous = tools.get(id);
      if (!previous && tools.size >= 4096) return;
      const kind = kinds.has(update.kind) ? update.kind : previous?.kind ?? 'other';
      if (previous?.kind === 'think' || kind === 'think') {
        tools.set(id, {kind: 'think'});
        return;
      }
      const status = statuses.has(update.status) ? update.status : previous?.status ?? 'pending';
      // Late notifications cannot reopen or reverse a settled tool.
      if (terminal(previous?.status) && status !== previous.status) return;
      if (previous?.status === 'in_progress' && status === 'pending') return;
      const event = {type: 'activity', toolCallId: id, kind, status};
      if (kind === 'execute') {
        const title = executionTitle(update) || previous?.title;
        if (title) event.title = title;
      } else if (kind !== 'other') {
        const title = clean(update.title) || previous?.title;
        if (title) event.title = title;
      }
      if (Array.isArray(update.locations)) {
        event.locations = update.locations.slice(0, 5).flatMap(location => {
          const path = clean(location?.path);
          return path ? [{path, ...(count(location.line) ? {line: location.line} : {})}] : [];
        });
      } else if (previous?.locations) event.locations = previous.locations;
      tools.set(id, event);
      return event;
    }
    if (update.sessionUpdate === 'usage_update') {
      const event = {type: 'usage'};
      for (const key of ['used', 'size']) if (count(update[key])) event[key] = update[key];
      const cost = update.cost;
      if (cost && typeof cost.amount === 'number' && Number.isFinite(cost.amount) && cost.amount >= 0 &&
          typeof cost.currency === 'string' && /^[A-Z]{3}$/.test(cost.currency)) {
        event.cost = {amount: cost.amount, currency: cost.currency};
      }
      const raw = update._meta?.usage;
      const breakdown = {};
      for (const [key, aliases] of Object.entries({
        inputTokens: ['inputTokens', 'input_tokens'], outputTokens: ['outputTokens', 'output_tokens'],
        cachedReadTokens: ['cachedReadTokens', 'cacheReadInputTokens', 'cache_read_input_tokens'],
        cachedWriteTokens: ['cachedWriteTokens', 'cacheCreationInputTokens', 'cache_creation_input_tokens'],
        totalTokens: ['totalTokens', 'total_tokens'],
      })) {
        for (const alias of aliases) if (count(raw?.[alias])) { breakdown[key] = raw[alias]; break; }
      }
      if (Object.keys(breakdown).length) event.breakdown = breakdown;
      return Object.keys(event).length > 1 ? event : undefined;
    }
  };
}
