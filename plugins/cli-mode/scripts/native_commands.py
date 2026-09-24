"""In-session slash-command admission.

Shared by every backend whose runtime expands its own slash commands inside the
ACP session, so a command never switches transport, closes the session or loses
history. Each runtime publishes its command list as a live
`available_commands_update` notification. CLI-MODE reads persisted command
metadata when available and caches streamed updates as a fallback. If neither
source provides a list, the runtime answers for unseen commands.

Callers pass the bound agent's label so a refusal names the agent the user
actually chose.
"""
import re

import host

COMMAND_TOKEN = re.compile(r'^(\s*)/([a-z][a-z0-9_.:-]*)(?=\s|$)', re.I)

# Commands the Claude ACP adapter removes from its advertised list: their UX
# lives in the interactive terminal. Rejecting them is a fact about that
# adapter, not a guess, so it is safe before a list has been observed. Other
# runtimes publish their own list and are filtered against it instead.
UNSUPPORTED = {
    'clear': 'clears terminal conversation state',
    'cost': 'is a terminal-only usage view; CLI-MODE reports utilization at activation',
    'keybindings-help': 'is a terminal keybinding view',
    'output-style:new': 'opens an interactive editor',
    'release-notes': 'is a terminal-only view',
    'todos': 'is a terminal-only view',
}


# Provider commands that would change what CLI-MODE owns: sign-in, and the
# model, effort and access it verified at activation. Refused the same way for
# every agent, with the CLI-MODE control to use instead, so no provider can sign
# the user out mid-session or drift from the settings CLI-MODE reports.
HOST_OWNED = {
    'login': 'changes sign-in interactively; sign in with the agent\'s own CLI',
    'logout': 'would sign you out of the agent mid-session; sign out with the agent\'s own CLI',
    'model': 'would change the model behind CLI-MODE; use /cli model',
    'effort': 'would change the effort behind CLI-MODE; use /cli effort',
    'fast': 'would change the model mode behind CLI-MODE; use /cli model',
    'permissions': 'would change access behind CLI-MODE; use /cli access',
    'allow-all': 'would change access behind CLI-MODE; use /cli access',
    'always-approve': 'would change access behind CLI-MODE; use /cli access',
    'reset-allowed-tools': 'would change access behind CLI-MODE; use /cli access',
}


def refuse_host_owned(name, label):
    if name in HOST_OWNED:
        raise RuntimeError('/' + name + ' ' + HOST_OWNED[name] + '. CLI-MODE did not dispatch it to ' + label +
                           ' or change your session.')


def unknown(name, label):
    return RuntimeError('Unknown or unavailable ' + label + ' command /' + name + '. Use ' +
                        ('/cli help' if host.claude() else '/help') +
                        ' for CLI-MODE help. Nothing was forwarded as model text.')


def command_request(text):
    match = COMMAND_TOKEN.match(text)
    return bool(match and match[2].casefold() not in ('cli', 'help'))


def name_of(text):
    match = COMMAND_TOKEN.match(text)
    return match[2].casefold() if match else None


def from_record(record):
    """Newer ACPX records retain the list emitted during session startup."""
    commands = record.get('acpx', {}).get('available_commands')
    if not isinstance(commands, list):
        return None
    return [item['name'] if isinstance(item, dict) else item for item in commands
            if isinstance(item, str) or isinstance(item, dict) and isinstance(item.get('name'), str)]


def validate(advertised, text, label='the agent', terminal_only=()):
    """Reject unsupported commands, and unknown ones once the list is known.

    `advertised` is the cached availableCommands list for this session, or None
    when no notification has been observed yet. `label` names the bound agent
    in any refusal. `terminal_only` lists commands known to be unavailable
    headlessly for this runtime, refused even before a list is observed.
    """
    name = name_of(text)
    if name is None:
        return {}
    refuse_host_owned(name, label)
    if name in terminal_only:
        raise RuntimeError('/' + name + ' ' + terminal_only[name] +
            '. The ' + label + ' ACP adapter does not advertise it. Use it in the native ' +
            label + ' terminal; CLI-MODE did not dispatch it or change your session.')
    if advertised is None:
        # No list observed yet. The runtime expands the command in-session and
        # answers for itself; nothing is rewritten and no session state changes.
        return {}
    names = {item for item in advertised if isinstance(item, str)}
    if name in names:
        return {}
    raise unknown(name, label)
