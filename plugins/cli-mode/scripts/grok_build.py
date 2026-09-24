"""Grok Build ACPX adapter.

This runtime advertises only `model` and `reasoning_effort`. It exposes no
permission-mode selector and no ACP session modes, so access is enforced
entirely by the ACPX client-side approval policy: nothing native is set or
verified, and `auto-edit` is not offered because no such native setting exists.
Provider slash commands stay in the same ACP session.
"""
import json
from pathlib import Path

import acpx
import native_commands
# Re-exported so every backend shares one ACP translation path.
from acpx import executable, public_event, PublicRelay

CATALOG = Path(__file__).resolve().parents[1] / 'backends/grok-build/assets/models.json'

ID = 'grok-build'
DISPLAY_NAME = 'Grok Build CLI'
LABEL = 'Grok'
PASSING = 'Passing to ' + LABEL + '...'
DEFAULTS = dict(model='grok-4.7', effort='high', access='allow')
# Grok expands its own slash commands in-session; no transport switch.
NATIVE_HANDOFF = False
command_request = native_commands.command_request


def provider_identity(record):
    """Conversation identity is distinct from ACPX's durable storage record."""
    return record.get('agentSessionId') or record.get('acpSessionId')


def catalog(root):
    cached = Path(root) / 'catalogs/grok-build.json'
    return json.loads((cached if cached.exists() else CATALOG).read_text(encoding='utf-8-sig'))


def selection(root, model, access, effort=None):
    data = catalog(root)
    families = [family for family in data['modelFamilies'] if family['modelId'] == model]
    if len(families) != 1:
        raise ValueError('Model is not unambiguously advertised; refresh the catalog.')
    family = families[0]
    options = family.get('efforts') or []
    if not options:
        raise ValueError('This model advertises no reasoning effort control; refresh the catalog.')
    if effort is None:
        effort = data.get('requestedDefaultEffort')
    wanted = str(effort).casefold()
    matches = [item for item in options
               if wanted in (item['value'].casefold(), item['name'].casefold())]
    if len(matches) != 1:
        raise ValueError('Effort is not unambiguously advertised for this model; refresh the catalog.')
    choices = [option for option in data['accessControl']['options'] if option['access'] == access]
    if len(choices) != 1:
        blocked = next((item for item in data['accessControl'].get('unsupported', [])
                        if item['access'] == access), None)
        if blocked:
            raise ValueError(blocked['reason'])
        raise ValueError('Unsupported access mapping; refresh and implement a verified policy.')
    # No native mode key: access is applied by the ACPX policy on every call.
    return dict(model=model, modelName=family['name'], effort=matches[0]['name'],
                effortValue=matches[0]['value'], effortKey=data['effortControl']['key'],
                access=access, accessName=choices[0]['nativeName'],
                mode=None, modeKey=None, accessEnforcedBy=data['accessControl']['enforcedBy'])


def setting_steps(settings):
    """Only advertised selectors are applied. There is no native mode to set."""
    return [['set', 'model', settings['model']],
            ['set', settings['effortKey'], settings['effortValue']]]


class Backend(acpx.AcpxBackend):
    profile = 'grok-build'

    def validate_command(self, owned, text):
        return native_commands.validate(owned.get('advertisedCommands'), text, LABEL, ())

    def verify(self, owned, record=None):
        record = self.metadata(owned) if record is None else record
        settings, meta = owned['settings'], record['acpx']
        options = {item['id']: item.get('currentValue') for item in meta.get('config_options', [])}
        expected = {'model': settings['model'], settings['effortKey']: settings['effortValue']}
        if meta.get('current_model_id') != settings['model'] or any(
                options.get(key) != value for key, value in expected.items()):
            raise RuntimeError('Runtime did not accept the requested model/effort. Routing remains gated.')
        return provider_identity(record)
