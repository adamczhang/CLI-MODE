"""Cursor ACPX adapter.

This runtime advertises a long model catalog with opaque bracketed IDs, no
reasoning-effort selector, and no permission mode. Native writes can bypass ACP
permission callbacks, so only allow access is supported. Its `mode` selector chooses an interaction
style (agent/plan/ask) rather than a permission level, so CLI-MODE pins it to
`agent` and does not expose it as an access choice.
"""
import json
from pathlib import Path

import acpx
import native_commands
# Re-exported so every backend shares one ACP translation path.
from acpx import executable, public_event, PublicRelay

CATALOG = Path(__file__).resolve().parents[1] / 'backends/cursor/assets/models.json'

ID = 'cursor'
DISPLAY_NAME = 'Cursor CLI'
LABEL = 'Cursor'
PASSING = 'Passing to ' + LABEL + '...'
DEFAULTS = dict(model='composer-2.5[fast=true]', access='allow')
PROMPT_UNSUPPORTED = ('Cursor has no permission-mode selector and its native writes bypass ACP permission '
                      'requests. Prompt access cannot be enforced; only allow is supported.')
# Cursor expands its own slash commands in-session; no transport switch.
NATIVE_HANDOFF = False
command_request = native_commands.command_request


def provider_identity(record):
    """Conversation identity is distinct from ACPX's durable storage record."""
    return record.get('agentSessionId') or record.get('acpSessionId')


def catalog(root):
    cached = Path(root) / 'catalogs/cursor.json'
    data = json.loads((cached if cached.exists() else CATALOG).read_text(encoding='utf-8-sig'))
    # Old refreshed catalogs must not reintroduce a policy the runtime bypasses.
    access = data['accessControl']
    access['options'] = [item for item in access['options'] if item['access'] == 'allow']
    access['unsupported'] = [item for item in access.get('unsupported', []) if item['access'] != 'prompt']
    access['unsupported'].append(dict(access='prompt', reason=PROMPT_UNSUPPORTED))
    return data


def selection(root, model, access, effort=None):
    data = catalog(root)
    families = [family for family in data['modelFamilies'] if family['modelId'] == model]
    if len(families) != 1:
        # Advertised IDs are opaque and can carry bracketed settings. An exact
        # match is required; never reconstruct or trim one.
        raise ValueError('Model is not unambiguously advertised; refresh the catalog '
                         'and choose a full advertised ID.')
    family = families[0]
    if data.get('effortControl') or family.get('efforts'):
        raise ValueError('Catalog advertises an effort control this adapter does not apply; refresh it.')
    if effort is not None and str(effort).casefold() not in ('', 'none', 'provider default'):
        raise ValueError('Cursor advertises no reasoning effort control; it cannot be set.')
    choices = [option for option in data['accessControl']['options'] if option['access'] == access]
    if len(choices) != 1:
        blocked = next((item for item in data['accessControl'].get('unsupported', [])
                        if item['access'] == access), None)
        if blocked:
            raise ValueError(blocked['reason'])
        raise ValueError('Unsupported access mapping; refresh and implement a verified policy.')
    interaction = data['interactionMode']
    return dict(model=model, modelName=family['name'], effort='Provider default',
                effortValue=None, effortKey=None,
                access=access, accessName=choices[0]['nativeName'],
                mode=interaction['pinnedValue'], modeKey=interaction['key'],
                modeName=interaction['pinnedName'],
                accessEnforcedBy=data['accessControl']['enforcedBy'])


def setting_steps(settings):
    """Apply the model and pin the interaction mode. There is no effort control."""
    return [['set', 'model', settings['model']],
            ['set', settings['modeKey'], settings['mode']]]


class Backend(acpx.AcpxBackend):
    profile = 'cursor'

    def validate_prompt(self, owned):
        # Block saved restricted sessions, but allow status/cancel/close cleanup.
        if owned['settings']['access'] != 'allow':
            raise RuntimeError(PROMPT_UNSUPPORTED + ' Stop and select supported access before sending work.')

    def validate_command(self, owned, text):
        return native_commands.validate(owned.get('advertisedCommands'), text, LABEL, ())

    def verify(self, owned, record=None):
        record = self.metadata(owned) if record is None else record
        settings, meta = owned['settings'], record['acpx']
        options = {item['id']: item.get('currentValue') for item in meta.get('config_options', [])}
        expected = {'model': settings['model'], settings['modeKey']: settings['mode']}
        if meta.get('current_model_id') != settings['model'] or any(
                options.get(key) != value for key, value in expected.items()):
            raise RuntimeError('Runtime did not accept the requested model/mode. Routing remains gated.')
        return provider_identity(record)
