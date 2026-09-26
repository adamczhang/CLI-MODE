"""Codex ACP adapter with separately advertised model, effort and access controls."""
from copy import deepcopy
import json
from pathlib import Path

import acpx
import native_commands
# Re-exported so every backend shares one ACP translation path.
from acpx import executable, public_event, PublicRelay

CATALOG = Path(__file__).resolve().parents[1] / 'backends/codex/assets/models.json'

ID = 'codex'
DISPLAY_NAME = 'Codex CLI'
LABEL = 'Codex'
PASSING = 'Passing to ' + LABEL + '...'
DEFAULTS = dict(model='gpt-6-sol', effort='high', access='allow')
# Codex expands advertised slash commands in-session: no transport switch, no
# closed session and no lost history.
NATIVE_HANDOFF = False
command_request = native_commands.command_request


def model_label(name):
    """Codex's ACP catalog names models by version alone ("6 Sol")."""
    return 'GPT-' + name if name[:1].isdigit() else name


def provider_identity(record):
    """Conversation identity is distinct from ACPX's durable storage record."""
    return record.get('agentSessionId') or record.get('acpSessionId')


def catalog(root):
    cached = Path(root) / 'catalogs/codex.json'
    data = json.loads((cached if cached.exists() else CATALOG).read_text(encoding='utf-8-sig'))
    if cached.exists():
        # A previous release may have cached its model list before Sol became
        # the initial default. Keep the other cached choices, but make the new
        # bundled default selectable until an owned-session refresh replaces
        # the snapshot. Activation still verifies what this account accepts.
        bundled = json.loads(CATALOG.read_text(encoding='utf-8-sig'))
        default = next(f for f in bundled['modelFamilies']
                       if f['modelId'] == DEFAULTS['model'])
        family = next((f for f in data['modelFamilies']
                       if f.get('modelId') == DEFAULTS['model']), None)
        if family is None:
            data['modelFamilies'].insert(1, deepcopy(default))
        elif not any(e.get('value') == DEFAULTS['effort'] for e in family.get('efforts') or []):
            family.setdefault('efforts', []).append(deepcopy(next(
                e for e in default['efforts'] if e['value'] == DEFAULTS['effort'])))
        if 'models' in data and not any(m.get('id') == DEFAULTS['model'] for m in data['models']):
            data['models'].insert(1, dict(id=DEFAULTS['model'], name=default['name']))
        data.update(requestedDefaultModelId=DEFAULTS['model'], requestedDefaultEffort=DEFAULTS['effort'])
    data['accessControl']['options'] = [item for item in data['accessControl']['options']
        if item['access'] == 'allow' and item['nativeValue'] == 'agent-full-access']
    return data


def selection(root, model, access, effort=None):
    data = catalog(root)
    families = [family for family in data['modelFamilies'] if family['modelId'] == model]
    if len(families) != 1:
        raise ValueError('Model is not unambiguously advertised; refresh the catalog.')
    family = families[0]
    options = family.get('efforts') or []
    if not options:
        raise ValueError('This model advertises no effort control; refresh the catalog.')
    if effort is None:
        effort = data.get('requestedDefaultEffort')
    wanted = str(effort).casefold()
    matches = [item for item in options
               if wanted in (item['value'].casefold(), item['name'].casefold())]
    if len(matches) != 1:
        raise ValueError('Effort is not unambiguously advertised for this model; refresh the catalog.')
    choices = [option for option in data['accessControl']['options'] if option['access'] == access]
    if len(choices) != 1 or access != 'allow':
        raise ValueError('Codex currently supports Full access only; Prompt and Auto-edit are not enforceable by this integration.')
    return dict(model=model, modelName=model_label(family['name']), effort=matches[0]['name'],
                effortValue=matches[0]['value'], effortKey=data['effortControl']['key'],
                access=access, accessName=choices[0]['nativeName'],
                mode=choices[0]['nativeValue'], modeKey=data['accessControl']['key'])


def setting_steps(settings):
    """ACPX resolves the model selector; reasoning effort and mode use config options."""
    return [['set', 'model', settings['model']],
            ['set', settings['effortKey'], settings['effortValue']],
            ['set', settings['modeKey'], settings['mode']]]


class Backend(acpx.AcpxBackend):
    profile = 'codex'

    def validate_prompt(self, owned):
        if owned['settings']['access'] != 'allow' or owned['settings']['mode'] != 'agent-full-access':
            raise RuntimeError('Codex supports Full access only. Stop and select supported access before sending work.')

    def validate_command(self, owned, text):
        """Codex expands advertised slash commands inside the same ACP session."""
        return native_commands.validate(owned.get('advertisedCommands'), text, LABEL, ())

    def verify(self, owned, record=None):
        record = self.metadata(owned) if record is None else record
        settings, meta = owned['settings'], record['acpx']
        options = {item['id']: item.get('currentValue') for item in meta.get('config_options', [])}
        # Config options are rebuilt on a model switch, so check only what was requested.
        expected = {'model': settings['model'], settings['effortKey']: settings['effortValue'],
                    settings['modeKey']: settings['mode']}
        if meta.get('current_model_id') != settings['model'] or any(
                options.get(key) != value for key, value in expected.items()):
            raise RuntimeError('Runtime did not accept the requested model/effort/access. Routing remains gated.')
        return provider_identity(record)
