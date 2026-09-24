/** The two ACPX settings a shared-runtime client must supply itself.
 * `acpx config show` resolves everything else, but omits credential values and
 * MCP servers. Read only those fields, only from the files ACPX reports it
 * loaded, and refuse when ACPX's own authMethods list disagrees.
 */
import { readFile } from 'node:fs/promises';

export async function configFile(path) {
  try { return JSON.parse(await readFile(path, 'utf8')); }
  catch (error) {
    if (error.code === 'ENOENT') return {};
    throw new Error('Unable to read ACPX configuration; repair it before submitting.');
  }
}

export async function sharedConfig(config, read = configFile) {
  const [globalConfig, projectConfig] = await Promise.all(['global', 'project'].map(scope =>
    config.loaded?.[scope] && config.paths?.[scope] ? read(config.paths[scope]) : {}));
  const servers = Object.hasOwn(projectConfig, 'mcpServers') ? projectConfig.mcpServers : globalConfig.mcpServers;
  if (servers?.length) {
    throw new Error('ACPX 0.18 shared sessions cannot forward configured mcpServers. Configure those tools in the provider CLI before using this binding; no prompt was sent.');
  }
  const authCredentials = {...globalConfig.auth, ...projectConfig.auth};
  if (Object.values(authCredentials).some(value => typeof value !== 'string' || !value.trim())) {
    throw new Error('Invalid ACPX auth configuration; repair it before submitting.');
  }
  const reported = Array.isArray(config.authMethods) ? [...config.authMethods].sort() : null;
  if (!reported || JSON.stringify(Object.keys(authCredentials).sort()) !== JSON.stringify(reported)) {
    throw new Error('ACPX reported different auth methods than CLI-MODE read from its configuration; no prompt was sent. Check `acpx config show`.');
  }
  return {authCredentials};
}
