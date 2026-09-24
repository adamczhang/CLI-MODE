"""Exercise installed menu transactions for every advertised model/effort.

Does not activate a provider or change a user installation. Both host formats
are checked; backend execution for each model is a separate live concern.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plugin', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    os.environ['CODEX_PERMISSION_PROFILE'] = ':danger-full-access'
    sys.path.insert(0, str(args.plugin.resolve() / 'scripts'))
    from controller import Controller
    from state import Store
    import adapters
    import host
    import menu_view
    rows = []
    args.output.mkdir(parents=True, exist_ok=True)
    for target_host in host.HOSTS:
        host.select(target_host)
        for agent in adapters.implemented():
            folder = args.output / target_host / agent
            folder.mkdir(parents=True, exist_ok=True)
            control = Controller(Store('menu-' + uuid.uuid4().hex, folder, folder / 'state'), agent=agent)
            row = dict(host=target_host, agent=agent, modelEffortTransactions=0, pages=0)
            try:
                control.frontend(agent)
                page = control.options('model')
                models = page['choices']
                for number in range(1, page['pages'] + 1):
                    current = control.options('model', page=number)
                    assert current['page'] == number
                    menu_view.write(current['activationMenu'], folder / ('models-%d.html' % number))
                    row['pages'] += 1
                control.navigate('<')
                refreshed = control.navigate('r')
                assert not refreshed['refreshed'] and refreshed['catalogStatus'] == 'cached'
                try:
                    control.choose(0)
                except ValueError:
                    pass
                else:
                    raise AssertionError('Invalid choice admitted')
                for model_index, model in enumerate(models, 1):
                    control.frontend(agent)
                    control.options('model')
                    efforts = control.choose(model_index)['choices']
                    for effort_index in range(1, len(efforts) + 1):
                        control.frontend(agent)
                        control.options('model')
                        control.choose(model_index)
                        access = control.choose(effort_index)
                        assert access['phase'] == 'access' and access['choices']
                        assert control.navigate('b')['phase'] == 'effort'
                        assert control.navigate('b')['phase'] == 'model'
                        row['modelEffortTransactions'] += 1
                row['models'] = len(models)
                assert not control.store.read()['owned'] and not control.store.read()['active']
                row['passed'] = True
            except Exception as exc:
                row.update(passed=False, error=str(exc))
            finally:
                row['shutdownComplete'] = control.off()['shutdownComplete']
            rows.append(row)
            (args.output / 'results.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')
            print(json.dumps(row), flush=True)
    return 0 if all(row['passed'] and row['shutdownComplete'] for row in rows) else 1


if __name__ == '__main__':
    raise SystemExit(main())
