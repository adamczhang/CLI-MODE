"""Backend adapter registry.

A registry record in backends.json is discovery metadata only. A backend is
implemented when it appears here with a real runtime adapter. Never fall back
to another provider's adapter for an unknown or future backend.
"""
import agy
import claude_code
import codex_cli
import copilot_cli
import cursor_agent
import grok_build

MODULES = {codex_cli.ID: codex_cli, agy.ID: agy, claude_code.ID: claude_code, copilot_cli.ID: copilot_cli,
           cursor_agent.ID: cursor_agent, grok_build.ID: grok_build}


def module(backend):
    if backend not in MODULES:
        raise ValueError('No runtime adapter implemented for backend ' + repr(backend) +
                         '; do not fall back to another provider.')
    return MODULES[backend]


def implemented():
    return sorted(MODULES)


def descriptor(backend):
    from state import backend_records
    records = {item['id']: item for item in backend_records()}
    if set(records) != set(MODULES):
        raise ValueError('Backend registry and executable adapters differ.')
    record = records[backend]
    adapter = module(backend)
    for name in ('catalog', 'selection', 'setting_steps', 'provider_identity', 'command_request', 'Backend'):
        if not callable(getattr(adapter, name, None)):
            raise ValueError(backend + ' is missing adapter capability ' + name)
    if record['displayName'] != adapter.DISPLAY_NAME:
        raise ValueError('Backend display name differs from registry: ' + backend)
    if record['effortRepresentation'] not in ('combined', 'separate', 'none'):
        raise ValueError('Invalid effort representation: ' + backend)
    if record['commandTransport'] != ('native-handoff' if adapter.NATIVE_HANDOFF else 'acp'):
        raise ValueError('Backend command transport differs from registry: ' + backend)
    if not record.get('prerequisites') or record.get('catalogRefresh') != 'owned-session-metadata':
        raise ValueError('Missing prerequisite/refresh contract: ' + backend)
    return record
