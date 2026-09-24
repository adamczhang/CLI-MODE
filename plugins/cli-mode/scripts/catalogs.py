"""Conservative refresh from owned-session metadata; never changes a runtime setting."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile

import adapters


def options(control):
    values = []
    for item in control.get('options') or []:
        if 'options' in item:
            values.extend(options(item))
        elif 'value' in item:
            values.append(item)
    return values


def from_metadata(backend, previous, record):
    descriptor = adapters.descriptor(backend)
    meta = record.get('acpx') or {}
    if not meta:
        raise ValueError('Provider returned no ACP metadata.')
    data = deepcopy(previous)
    controls = {item['id']: item for item in meta.get('config_options') or []}
    model_options = options(controls.get('model', {}))
    ids = meta.get('available_models')
    names = meta.get('available_model_names') or {}
    if model_options:
        ids = [item['value'] for item in model_options if item['value'] != 'default']
        names = {item['value']: item.get('name', item['value']) for item in model_options}
    if not ids:
        if data.get('modelControl', True) is not None:
            raise ValueError('No advertised model list; previous choices retained.')
    else:
        families = []
        old = {item.get('modelId'): item for item in data['modelFamilies']}
        for model in ids:
            if not isinstance(model, str):
                raise ValueError('Invalid advertised model ID.')
            name = names.get(model, model)
            if descriptor['effortRepresentation'] == 'combined':
                match = re.fullmatch(r'(.+) \((High|Medium|Low)\)', name)
                label, effort = match.groups() if match else (name, 'Provider default')
                family = next((f for f in families if f['name'] == label), None)
                if family is None:
                    family = {'name': label, 'efforts': []}
                    families.append(family)
                if any(e['name'] == effort for e in family['efforts']):
                    raise ValueError('Ambiguous combined model/effort metadata.')
                family['efforts'].append({'name': effort, 'modelId': model})
            else:
                # A current model's effort list must never be assigned to another model.
                family = deepcopy(old.get(model, {'modelId': model, 'efforts': []}))
                family['name'] = name
                if descriptor['effortRepresentation'] == 'separate':
                    if model == meta.get('current_model_id'):
                        key = data['effortControl']['key']
                        advertised = options(controls.get(key, {}))
                        if not advertised and not (backend == 'claude' and key not in controls):
                            raise ValueError('Current model has no supported advertised effort selector.')
                        family['efforts'] = [{'name': e.get('name', e['value']), 'value': e['value']}
                                             for e in advertised if e['value'] != 'default']
                        family['effortSource'] = 'session-metadata'
                        if backend == 'claude':
                            family['effortSupported'] = bool(advertised)
                    elif model not in old:
                        # Discoverable, but cannot offer invented efforts before they are observed.
                        continue
                    else:
                        family['effortSource'] = 'cached-model-specific'
                families.append(family)
        if not families:
            raise ValueError('No usable models in advertised metadata.')
        data['modelFamilies'] = families
    access = data['accessControl']
    key = access.get('key')
    if key:
        advertised = {item['value'] for item in options(controls.get(key, {}))}
        if not advertised:
            raise ValueError('No advertised access selector; previous choices retained.')
        access['options'] = [item for item in access['options'] if item['nativeValue'] in advertised]
        if not access['options']:
            raise ValueError('Provider no longer advertises an implemented access mapping.')
    data.update(fetchedAt=datetime.now(timezone.utc).isoformat(),
                source='Owned ACP session metadata; no reconnect or model change. Other models retain their model-specific cached effort observations.',
                advertisedCurrentModelId=meta.get('current_model_id'))
    return data


def save(root, backend, data):
    adapters.descriptor(backend)
    folder = Path(root) / 'catalogs'
    folder.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=folder, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, indent=2)
        os.replace(temporary, folder / (backend + '.json'))
    finally:
        Path(temporary).unlink(missing_ok=True)
