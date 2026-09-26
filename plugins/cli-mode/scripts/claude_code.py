"""Claude Code ACPX adapter.

Model and effort are separate advertised selectors here, unlike Antigravity's
combined model IDs. Provider slash commands stay in the same ACP session: the
adapter advertises its own command list, so CLI-MODE never switches transport
and never claims a fresh conversation.
"""
import json
from pathlib import Path

import acpx
import native_commands
# Re-exported so every backend shares one ACP translation path.
from acpx import executable, public_event, PublicRelay

CATALOG = Path(__file__).resolve().parents[1] / 'backends/claude/assets/models.json'

ID = 'claude'
DISPLAY_NAME = 'Claude Code CLI'
LABEL = 'Claude'
PASSING = 'Passing to ' + LABEL + '...'
DEFAULTS = dict(model='opus[1m]', effort='high', access='allow')
# Claude expands its own slash commands in-session: no transport switch, no
# closed session and no lost history.
NATIVE_HANDOFF = False
command_request = native_commands.command_request


def provider_identity(record):
    """Conversation identity is distinct from ACPX's durable storage record."""
    return record.get('agentSessionId') or record.get('acpSessionId')


def catalog(root):
    cached = Path(root) / 'catalogs/claude.json'
    data = json.loads((cached if cached.exists() else CATALOG).read_text(encoding='utf-8-sig'))
    # Upgrade the two obsolete observations in saved catalogs as well as the
    # bundled one. Do not discard other model-specific metadata from refresh.
    canonical = any(f['modelId'] == 'opus[1m]' for f in data['modelFamilies'])
    data['modelFamilies'] = [f for f in data['modelFamilies']
                             if not (canonical and f['modelId'] == 'opus')]
    for family in data['modelFamilies']:
        if family['modelId'] == 'opus':
            family.update(modelId='opus[1m]', name='Opus 5.5')
        if family['modelId'] == 'haiku':
            family.update(efforts=[], effortSupported=False)
    if not any(f['modelId'] == DEFAULTS['model'] for f in data['modelFamilies']):
        # An older refresh could omit the newly named Opus model because it had
        # no model-specific effort history. Retain the verified bundled default.
        bundled = json.loads(CATALOG.read_text(encoding='utf-8-sig'))
        data['modelFamilies'].append(next(f for f in bundled['modelFamilies']
                                          if f['modelId'] == DEFAULTS['model']))
    for model in data.get('models', []):
        if model['id'] == 'opus':
            model.update(id='opus[1m]', name='Opus 5.5')
    if data.get('requestedDefaultModelId') == 'opus':
        data['requestedDefaultModelId'] = 'opus[1m]'
    return data


def selection(root, model, access, effort=None):
    data = catalog(root)
    # Old saved settings and explicit /cli model opus remain a supported alias.
    model = 'opus[1m]' if model == 'opus' else model
    families = [family for family in data['modelFamilies'] if family['modelId'] == model]
    if len(families) != 1:
        raise ValueError('Model is not unambiguously advertised; refresh the catalog.')
    family = families[0]
    options = family.get('efforts') or []
    if not options and family.get('effortSupported') is not False:
        raise ValueError('This model advertises no effort control; refresh the catalog.')
    if options:
        effort = data.get('requestedDefaultEffort') if effort is None else effort
        wanted = str(effort).casefold()
        matches = [item for item in options
                   if wanted in (item['value'].casefold(), item['name'].casefold())]
        if len(matches) != 1:
            raise ValueError('Effort is not unambiguously advertised for this model; refresh the catalog.')
        effort_name, effort_value, effort_key = matches[0]['name'], matches[0]['value'], data['effortControl']['key']
    else:
        if effort is not None and str(effort).casefold() != 'provider default':
            raise ValueError('This model has no effort control; use Provider default.')
        effort_name, effort_value, effort_key = 'Provider default', None, None
    choices = [option for option in data['accessControl']['options'] if option['access'] == access]
    if len(choices) != 1 or access not in ('allow', 'auto-edit', 'prompt'):
        raise ValueError('Unsupported access mapping; refresh and implement a verified policy.')
    return dict(model=model, modelName=family['name'], effort=effort_name,
                effortValue=effort_value, effortKey=effort_key,
                access=access, accessName=choices[0]['nativeName'],
                mode=choices[0]['nativeValue'], modeKey=data['accessControl']['key'])


def setting_steps(settings):
    """Model uses the runtime's model_set action; effort and mode are config_set."""
    steps = [['set', 'model', settings['model']]]
    if settings['effortKey'] is not None:
        steps.append(['set', settings['effortKey'], settings['effortValue']])
    return steps + [['set', settings['modeKey'], settings['mode']]]


class Backend(acpx.AcpxBackend):
    profile = 'claude'

    def validate_command(self, owned, text):
        """Claude expands its own slash commands inside the same ACP session."""
        return native_commands.validate(owned.get('advertisedCommands'), text, LABEL, native_commands.UNSUPPORTED)

    def verify(self, owned, record=None):
        record = self.metadata(owned) if record is None else record
        settings, meta = owned['settings'], record['acpx']
        options = {item['id']: item.get('currentValue') for item in meta.get('config_options', [])}
        # Config options are rebuilt on a model switch, so check only what was requested.
        expected = {'model': settings['model'], settings['modeKey']: settings['mode']}
        if settings['effortKey'] is not None:
            expected[settings['effortKey']] = settings['effortValue']
        if meta.get('current_model_id') != settings['model'] or any(
                options.get(key) != value for key, value in expected.items()):
            raise RuntimeError('Runtime did not accept the requested model/effort/access. Routing remains gated.')
        return provider_identity(record)
