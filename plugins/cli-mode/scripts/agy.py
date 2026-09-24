"""Antigravity-specific ACPX adapter. All untrusted prompt prose travels in files."""
import json
from pathlib import Path

import acpx
import native_agy
# Re-exported so existing callers and tests keep one ACP translation path.
from acpx import executable, public_event, PublicRelay

CATALOG = Path(__file__).resolve().parents[1] / 'backends/agy/assets/models.json'

ID = 'agy'
DISPLAY_NAME = 'Antigravity CLI'
LABEL = 'Antigravity'
PASSING = 'Passing to ' + LABEL + '...'
DEFAULTS = dict(model='gemini-3.8-flash-high', access='allow')
AUTO_EDIT_UNSUPPORTED = ('Antigravity auto-edit is blocked by ACPX client file permissions. '
                         'Use Prompt or Allow; edits-only access cannot be enforced by this transport.')
# Provider slash commands leave ACP for the native CLI, which expands them.
NATIVE_HANDOFF = True
command_request = native_agy.command_request


def provider_identity(record):
    return record.get('agentSessionId') or record.get('acpSessionId')


def catalog(root):
    cached = Path(root) / 'catalogs/agy.json'
    data = json.loads((cached if cached.exists() else CATALOG).read_text(encoding='utf-8-sig'))
    access = data['accessControl']
    access['options'] = [item for item in access['options'] if item['access'] != 'auto-edit']
    access['unsupported'] = [item for item in access.get('unsupported', []) if item['access'] != 'auto-edit']
    access['unsupported'].append(dict(access='auto-edit', reason=AUTO_EDIT_UNSUPPORTED))
    return data


def selection(root, model, access, effort=None):
    if access == 'auto-edit':
        raise ValueError(AUTO_EDIT_UNSUPPORTED)
    data = catalog(root)
    matches = [(family['name'], item['name'])
               for family in data['modelFamilies'] for item in family['efforts']
               if item['modelId'] == model]
    if len(matches) != 1:
        raise ValueError('Model/effort is not unambiguously advertised; refresh the catalog.')
    if effort is not None and effort.casefold() != matches[0][1].casefold():
        raise ValueError('Antigravity encodes effort in the model ID; select the matching model variant.')
    choices = [option for option in data['accessControl']['options'] if option['access'] == access]
    if len(choices) != 1 or access not in ('allow', 'auto-edit', 'prompt'):
        raise ValueError('Unsupported access mapping; refresh and implement a verified policy.')
    return dict(model=model, modelName=matches[0][0], effort=matches[0][1], access=access,
                accessName=choices[0]['nativeName'],
                mode=choices[0]['nativeValue'], modeKey=data['accessControl']['key'])


def setting_steps(settings):
    """Owned-session settings to apply and verify. Effort is encoded in the model ID."""
    return [['set', 'model', settings['model']],
            ['set', settings['modeKey'], settings['mode']]]


class Backend(acpx.AcpxBackend):
    profile = 'antigravity'

    def validate_prompt(self, owned):
        if owned['settings']['access'] == 'auto-edit':
            raise RuntimeError(AUTO_EDIT_UNSUPPORTED)

    def validate_command(self, owned, text):
        return native_agy.validate_command(text, owned['workspace'])

    def start(self, owned, args, timeout=60):
        if owned.get('transport') == 'native':
            if '--file' not in args:
                raise RuntimeError('Native teamwork uses its saved conversation, not ACP controls.')
            return native_agy.start(owned, args[args.index('--file') + 1], timeout)
        return super().start(owned, args, timeout)

    def control(self, owned, args):
        if owned.get('transport') == 'native':
            if args[0] == 'cancel':
                native_agy.close(owned)
                return {'canceled': True}
            raise RuntimeError('Unsupported native teamwork control.')
        return super().control(owned, args)

    def metadata(self, owned):
        if owned.get('transport') == 'native':
            return {'agentSessionId': owned.get('providerSession')}
        return super().metadata(owned)

    def verify(self, owned, record=None):
        record = self.metadata(owned) if record is None else record
        settings, meta = owned['settings'], record['acpx']
        modes = [c for c in meta.get('config_options', []) if c['id'] == settings['modeKey']]
        if meta.get('current_model_id') != settings['model'] or len(modes) != 1 or modes[0].get('currentValue') != settings['mode']:
            raise RuntimeError('Runtime did not accept the requested model/access. Routing remains gated.')
        return provider_identity(record)

    def close(self, owned):
        if owned.get('transport') == 'native':
            native_agy.close(owned)
            return
        super().close(owned)
