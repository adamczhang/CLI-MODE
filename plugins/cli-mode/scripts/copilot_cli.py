"""GitHub Copilot ACPX adapter.

This runtime advertises no model catalog and no reasoning-effort selector over
ACP, so both phases show one explicit Provider default row. It does advertise a
real permission control, `allow_all`, which CLI-MODE applies and verifies. Its
`mode` selector uses ACP session-mode URIs and chooses an interaction style
rather than a permission level, so CLI-MODE pins Agent.
"""
import json
from pathlib import Path

import acpx
import native_commands
# Re-exported so every backend shares one ACP translation path.
from acpx import executable, public_event, PublicRelay

CATALOG = Path(__file__).resolve().parents[1] / 'backends/copilot/assets/models.json'

ID = 'copilot'
DISPLAY_NAME = 'GitHub Copilot CLI'
LABEL = 'Copilot'
PASSING = 'Passing to ' + LABEL + '...'
DEFAULTS = dict(model='provider-default', access='allow')
# Copilot expands its own slash commands in-session; no transport switch.
NATIVE_HANDOFF = False
command_request = native_commands.command_request


def provider_identity(record):
    """Conversation identity is distinct from ACPX's durable storage record."""
    return record.get('agentSessionId') or record.get('acpSessionId')


def catalog(root):
    cached = Path(root) / 'catalogs/copilot.json'
    return json.loads((cached if cached.exists() else CATALOG).read_text(encoding='utf-8-sig'))


def selection(root, model, access, effort=None):
    data = catalog(root)
    families = [family for family in data['modelFamilies'] if family['modelId'] == model]
    if len(families) != 1:
        raise ValueError('Copilot advertises no model catalog over ACP; only the provider '
                         'default is selectable. Refresh the catalog if that has changed.')
    if effort is not None and str(effort).casefold() not in ('', 'none', 'provider default'):
        raise ValueError('Copilot advertises no reasoning effort control; it cannot be set.')
    choices = [option for option in data['accessControl']['options'] if option['access'] == access]
    if len(choices) != 1:
        blocked = next((item for item in data['accessControl'].get('unsupported', [])
                        if item['access'] == access), None)
        if blocked:
            raise ValueError(blocked['reason'])
        raise ValueError('Unsupported access mapping; refresh and implement a verified policy.')
    interaction = data['interactionMode']
    return dict(model=model, modelName=families[0]['name'], effort='Provider default',
                effortValue=None, effortKey=None,
                access=access, accessName=choices[0]['nativeName'],
                mode=choices[0]['nativeValue'], modeKey=data['accessControl']['key'],
                interactionValue=interaction['pinnedValue'], interactionKey=interaction['key'],
                interactionName=interaction['pinnedName'],
                accessEnforcedBy=data['accessControl']['enforcedBy'])


def setting_steps(settings):
    """Apply the native permission control and pin the interaction mode.

    There is no model or effort selector to set: the model belongs to the
    Copilot CLI itself, not to the ACP session.
    """
    return [['set', settings['modeKey'], settings['mode']],
            ['set', settings['interactionKey'], settings['interactionValue']]]


class Backend(acpx.AcpxBackend):
    profile = 'copilot'

    def validate_command(self, owned, text):
        return native_commands.validate(owned.get('advertisedCommands'), text, LABEL, ())

    def verify(self, owned, record=None):
        record = self.metadata(owned) if record is None else record
        settings, meta = owned['settings'], record['acpx']
        options = {item['id']: item.get('currentValue') for item in meta.get('config_options', [])}
        expected = {settings['modeKey']: settings['mode'],
                    settings['interactionKey']: settings['interactionValue']}
        # No model is requested, so no model is verified.
        if any(options.get(key) != value for key, value in expected.items()):
            raise RuntimeError('Runtime did not accept the requested access/mode. Routing remains gated.')
        return provider_identity(record)
