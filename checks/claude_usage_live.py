"""Live usage test (Claude Code): native Claude against AUTO (Claude plus its AUTO agent), on single prompts of
escalating size. It spends the Claude and agent plans it runs on: ask before running it.

Two task sets (--set):
  shop   (default) two kinds of size, three tiers each (1k, 5k, 25k tokens) on an inventory library:
         W<n> the WORK is about n tokens: a short prompt asking for that much finished work (code, tests, docs);
         P<n> the PROMPT is about n tokens: pricing rules and data pasted in, with an exact answer computed in advance;
  fleet  a telemetry library (checks/claude_usage_fleet.py): T1 and T5 (1k and 5k of work), then three 15k tasks of
         different kinds: F15 a feature build, D15 pasted data with exact answers, R15 reported bugs and a refactor;
         hidden acceptance tests the agents never see check the work;
  fleet5 five 5k tasks of different kinds on the same library: S5 a feature, H5 pasted shift data with exact
         answers, V5 exact validation rules, C5 a refactor to a class that keeps the old functions, Q5 test-writing
         that must find the bugs itself.
With --agent claude and the same --model and --host-model, both sides run one model, so AUTO's extra tokens (Claude
plus the agent, against native) are the harness's own cost. Two Claude Code processes then share one sign-in: the
test checks it before each run and stops at the first run that finds it signed out.
Each (task, mode, repeat) runs in its own throwaway project and its own headless Claude Code session (the unpacked
build, dist/claude-dev), one at a time. Measured:
  - Claude: tokens from each result's usage (input, output, cache write and read, turns) and its 5-hour window;
  - the AUTO agent's own tokens, matched by the run's folder: Codex from its session logs (~/.codex/sessions, with
    its weekly window), Antigravity from its conversation stores (~/.gemini/antigravity-acp/conversations), Claude
    Code from ACPX's session records; other agents' counts are not read;
  - time from the prompt to Claude's final answer, and for AUTO its split: Claude before the handoff, the agent
    working, Claude after the wake-up;
  - handoffs, tool calls, subagents, and whether the work is right (tests, files, the exact answers).

    python scripts/package_plugin.py
    python checks/claude_usage_live.py [--set shop|fleet|fleet5] [--only W1,P1] [--modes native,auto] [--repeat 2]
        [--agent codex] [--model gpt-6-sol] [--effort high] [--fast on|off] [--host-model <id>] [--host-effort <level>]
        [--out <folder>] [--dry]

Writes results.json, each run's events and report.md to --out (default: a new folder in the temp folder).
"""
import argparse
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import os
from pathlib import Path
import random
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from claude_background_live import Session, saved_state  # noqa: E402
from claude_live_relay import DEV, claude_binary, claude_data  # noqa: E402
import claude_usage_fleet as fleet  # noqa: E402

CODEX_SESSIONS = Path.home() / '.codex' / 'sessions'
AGY_CONVERSATIONS = Path.home() / '.gemini' / 'antigravity-acp' / 'conversations'
# Relative weights of Claude's token kinds, from API prices (output 20, cache write 5, cache read 0.2, input 4 per
# million): a stand-in for how much of a plan each run uses, to compare runs; never a price.
WEIGHTS = {'output': 20, 'cacheWrite': 5, 'cacheRead': 0.2, 'input': 4}
CENT = Decimal('0.01')
EXTRA = ['--permission-mode', 'acceptEdits', '--allowedTools', 'Bash', 'PowerShell']
WORKING = ('captured', 'submitting')
STOP_AT = 90  # Claude's 5-hour window, percent: no new run starts above it.
TIMEOUT = {'1': 20 * 60, '5': 45 * 60, '15': 75 * 60, '25': 100 * 60}
QUIET = 30  # Seconds Claude must stay idle before a run counts as finished (a wake-up may follow).
NO_QUESTIONS = ' Work without asking me questions: make reasonable assumptions and say what they were. Do not commit.'

# ---------------------------------------------------------------- the seed project

SEED = {
    'README.md': '# Shopkeeper\n\nA small inventory and order library.\n',
    'conftest.py': '',
    '.gitignore': '__pycache__/\n.pytest_cache/\nAgent_Working_Folder/\n',
    'inventory/__init__.py': '',
    'inventory/models.py': (
        'from dataclasses import dataclass, field\n\n\n'
        '@dataclass\nclass Item:\n    sku: str\n    name: str\n    price: float\n    stock: int = 0\n\n\n'
        '@dataclass\nclass OrderLine:\n    sku: str\n    quantity: int\n\n\n'
        '@dataclass\nclass Order:\n    order_id: str\n    lines: list = field(default_factory=list)\n'
        '    discount: float = 0.0  # A fraction: 0.1 is 10% off.\n'),
    'inventory/store.py': (
        'from inventory.models import Item\n\n\n'
        'class Store:\n    def __init__(self):\n        self.items = {}\n        self.orders = []\n\n'
        '    def add_item(self, sku, name, price, stock=0):\n        if sku in self.items:\n'
        '            raise ValueError(\'duplicate sku \' + sku)\n'
        '        self.items[sku] = Item(sku, name, price, stock)\n        return self.items[sku]\n\n'
        '    def restock(self, sku, quantity):\n        self.items[sku].stock += quantity\n\n'
        '    def place_order(self, order):\n        total = 0.0\n        for line in order.lines:\n'
        '            item = self.items[line.sku]\n            item.stock -= line.quantity\n'
        '            total += item.price * line.quantity\n        total = total * (1 - order.discount)\n'
        '        self.orders.append((order, total))\n        return round(total, 1)\n'),
    'inventory/report.py': (
        'def average_order_value(store):\n    totals = [total for _, total in store.orders]\n'
        '    return sum(totals) / len(totals)\n\n\n'
        'def best_seller(store):\n    counts = {}\n    for order, _ in store.orders:\n'
        '        for line in order.lines:\n            counts[line.sku] = counts.get(line.sku, 0) + line.quantity\n'
        '    return max(counts, key=counts.get)\n'),
    'tests/test_store.py': (
        'import pytest\n\nfrom inventory import report\nfrom inventory.models import Order, OrderLine\n'
        'from inventory.store import Store\n\n\n'
        'def make():\n    store = Store()\n    store.add_item(\'A1\', \'Apple\', 0.5, stock=10)\n'
        '    store.add_item(\'B2\', \'Bread\', 2.25, stock=3)\n    return store\n\n\n'
        'def test_duplicate_sku_is_refused():\n    with pytest.raises(ValueError):\n'
        '        make().add_item(\'A1\', \'Again\', 1.0)\n\n\n'
        'def test_order_total_is_in_cents():\n    order = Order(\'o1\', [OrderLine(\'A1\', 3), OrderLine(\'B2\', 1)], '
        'discount=0.1)\n    assert make().place_order(order) == 3.38\n\n\n'
        'def test_stock_never_goes_negative():\n    store = make()\n    with pytest.raises(ValueError):\n'
        '        store.place_order(Order(\'o2\', [OrderLine(\'A1\', 2), OrderLine(\'B2\', 4)]))\n'
        '    assert (store.items[\'A1\'].stock, store.items[\'B2\'].stock) == (10, 3)  # Nothing changed.\n\n\n'
        'def test_reports_on_an_empty_store():\n    assert report.average_order_value(Store()) == 0\n'
        '    assert report.best_seller(Store()) is None\n'),
}


def seed(workspace, files=None):
    for name, text in (files or SEED).items():
        path = workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode('utf-8'))
    for command in (['init', '-q'], ['add', '-A'],
                    ['-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'Seed']):
        subprocess.run(['git', *command], cwd=workspace, check=True)


# ---------------------------------------------------------------- data and exact answers

CATS = ['tools', 'garden', 'kitchen', 'toys', 'books', 'sports']
ADJ = ['Amber', 'Bold', 'Brass', 'Cedar', 'Nimble', 'Quiet', 'Rapid', 'Sturdy', 'Tidy', 'Vivid', 'Woven', 'Zesty']
NOUN = ['Hammer', 'Planter', 'Kettle', 'Kite', 'Atlas', 'Racket', 'Wrench', 'Trowel', 'Skillet', 'Puzzle', 'Novel',
        'Glove']
REGIONS = {'US-CA': Decimal('0.0725'), 'US-NY': Decimal('0.08875'), 'EU-DE': Decimal('0.19'), 'JP': Decimal('0.10')}


def money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def catalog(rng, count):
    rows = []
    for index in range(count):
        rows.append(dict(sku='SKU-%04d' % (index + 1), name=rng.choice(ADJ) + ' ' + rng.choice(NOUN),
                         category=rng.choice(CATS), price=money(Decimal(rng.randint(100, 25000)) / 100),
                         stock=0 if rng.random() < .08 else rng.randint(1, 199)))
    return rows


def floor_minus(price, amount):
    return max(price - amount, Decimal('0.99'))


RULES_1 = [
    ('kitchen items: price times 1.10', lambda i, p: p * Decimal('1.10') if i['category'] == 'kitchen' else p),
    ('toys priced above 50.00 (at this point): times 0.85',
     lambda i, p: p * Decimal('0.85') if i['category'] == 'toys' and p > 50 else p),
    ('books: minus 1.00, but never below 0.99', lambda i, p: floor_minus(p, 1) if i['category'] == 'books' else p),
    ('garden items with stock above 100: times 0.95',
     lambda i, p: p * Decimal('0.95') if i['category'] == 'garden' and i['stock'] > 100 else p),
    ('sports items whose name contains "Glove": times 1.20',
     lambda i, p: p * Decimal('1.20') if i['category'] == 'sports' and 'Glove' in i['name'] else p),
]
RULES_5 = RULES_1 + [
    ('tools priced below 10.00 (at this point): plus 0.50',
     lambda i, p: p + Decimal('0.50') if i['category'] == 'tools' and p < 10 else p),
    ('any item with stock from 1 to 5: times 1.05', lambda i, p: p * Decimal('1.05') if 1 <= i['stock'] <= 5 else p),
    ('items whose SKU number is divisible by 7: times 0.93',
     lambda i, p: p * Decimal('0.93') if int(i['sku'][4:]) % 7 == 0 else p),
    ('names starting with "Amber" or "Bold": minus 2.00, but never below 0.99',
     lambda i, p: floor_minus(p, 2) if i['name'].startswith(('Amber', 'Bold')) else p),
    ('sports items priced above 100.00 (at this point): capped at 100.00',
     lambda i, p: min(p, Decimal(100)) if i['category'] == 'sports' else p),
    ('any price above 200.00 (at this point): capped at 200.00', lambda i, p: min(p, Decimal(200))),
]


def priced(items, rules):
    out = []
    for item in items:
        price = item['price']
        for _, rule in rules:
            price = rule(item, price)
        out.append(dict(item, new=money(price)))
    return out


def rules_text(rules):
    return '\n'.join(str(n) + '. ' + text for n, (text, _) in enumerate(rules, 1)) + (
        '\n' + str(len(rules) + 1) + '. Apply the rules in this order to each item\'s exact price, then round the '
        'result half-up to cents. An item\'s value is its new price times its stock.')


def items_csv(items):
    return 'sku,name,category,price,stock\n' + '\n'.join(
        ','.join((i['sku'], i['name'], i['category'], str(i['price']), str(i['stock']))) for i in items)


def task_p1():
    count = 22
    while True:
        prompt, total = p1_prompt(count)
        if len(prompt) >= 3900:
            return prompt, dict(total=str(money(total)))
        count += 4


def p1_prompt(count):
    items = catalog(random.Random(101), count)
    done = priced(items, RULES_1)
    total = sum((i['new'] * i['stock'] for i in done), Decimal(0))
    prompt = ('Our store is re-pricing its catalogue, and I need this done properly in the project, not just '
              'calculated. Below are ' + str(count) + ' items (CSV) and our pricing rules.\n\nWhat to do:\n'
              '- Save the items exactly as given to data/items.csv.\n'
              '- Add inventory/pricing.py with apply_rules(item) implementing the rules below with decimal.Decimal, '
              'and load_items(path) returning the rows.\n'
              '- Make every existing test pass (the tests are right; the code has bugs).\n'
              '- Add tests for the pricing rules, one per rule at least.\n'
              '- Tell me the total inventory value at the new prices, to the cent.\n\n'
              'Pricing rules:\n' + rules_text(RULES_1) + '\n\nItems:\n' + items_csv(items) + '\n' + NO_QUESTIONS)
    return prompt, total


def task_p5():
    prompt, count = '', 300
    while True:
        items = catalog(random.Random(505), count)
        done = priced(items, RULES_5)
        values = {cat: sum((i['new'] * i['stock'] for i in done if i['category'] == cat), Decimal(0)) for cat in CATS}
        prompt = ('We are re-pricing the whole catalogue before the autumn season. The rules and all ' + str(count) +
                  ' items are below. I need it built into the project and the numbers checked.\n\nWhat to do:\n'
                  '- Save the items exactly as given to data/items.csv.\n'
                  '- Add inventory/pricing.py: apply_rules(item) implementing every rule below with decimal.Decimal, '
                  'load_items(path), and category_values(items) returning each category\'s total value.\n'
                  '- Make every existing test pass (the tests are right; the code has bugs).\n'
                  '- Add tests: at least one per rule, and one for category_values on a small hand-made list.\n'
                  '- Write reports/pricing.md with a table of each category\'s value and the grand total.\n'
                  '- Tell me each category\'s total value and the grand total, to the cent, and the three categories '
                  'with the highest value.\n\nPricing rules:\n' + rules_text(RULES_5) + '\n\nItems:\n' +
                  items_csv(items) + '\n' + NO_QUESTIONS)
        if len(prompt) >= 19000 or count > 2000:
            break
        count += 20
    total = sum(values.values(), Decimal(0))
    top = [cat for cat, _ in sorted(values.items(), key=lambda kv: -kv[1])[:3]]
    return prompt, dict(total=str(money(total)), **{cat: str(money(v)) for cat, v in values.items()},
                        top=','.join(top))


AUDIT_RULES = [
    ('R1', 'quantity must be a whole number of at least 1'),
    ('R2', 'the SKU must be in the catalogue below'),
    ('R3', 'unit_price must equal the catalogue price exactly, to the cent (not checked when R2 fails)'),
    ('R4', 'region must be exactly one of US-CA, US-NY, EU-DE, JP (case matters)'),
    ('R5', 'date must be an ISO date from 2026-08-01 to 2026-08-31 inclusive'),
    ('R6', 'an (order_id, sku) pair may appear only once: every repeat after its first line breaks this rule (the '
           'first line is judged on the other rules alone)'),
    ('R7', 'status must be exactly paid, refunded or pending (lower case)'),
]


def audit(catalog_rows, lines):
    prices = {row['sku']: row['price'] for row in catalog_rows}
    seen, broken, revenue = set(), {rule: 0 for rule, _ in AUDIT_RULES}, {region: Decimal(0) for region in REGIONS}
    bad = 0
    for line in lines:
        fails = []
        qty = line['quantity']
        if not re.fullmatch(r'\d+', qty) or int(qty) < 1:
            fails.append('R1')
        if line['sku'] not in prices:
            fails.append('R2')
        elif Decimal(line['unit_price']) != prices[line['sku']]:
            fails.append('R3')
        if line['region'] not in REGIONS:
            fails.append('R4')
        if not ('2026-08-01' <= line['date'] <= '2026-08-31' and re.fullmatch(r'\d{4}-\d\d-\d\d', line['date'])):
            fails.append('R5')
        pair = (line['order_id'], line['sku'])
        if pair in seen:
            fails.append('R6')
        seen.add(pair)
        if line['status'] not in ('paid', 'refunded', 'pending'):
            fails.append('R7')
        for rule in fails:
            broken[rule] += 1
        if fails:
            bad += 1
        elif line['status'] == 'paid':
            revenue[line['region']] += int(qty) * Decimal(line['unit_price']) * (1 + REGIONS[line['region']])
    return bad, broken, {region: money(value) for region, value in revenue.items()}


def task_p25():
    rng = random.Random(2525)
    cat = catalog(random.Random(2525), 60)
    prices = {row['sku']: row['price'] for row in cat}
    lines, orders = [], []
    spec = ('Our August order log came out of a buggy export. Below are our catalogue (60 SKUs), the audit rules, '
            'and the full log. I need an audit tool in the project and the audited numbers.\n\nWhat to do:\n'
            '- Save the log exactly as given to data/orders.csv and the catalogue to data/catalogue.csv.\n'
            '- Add inventory/audit.py: load both files, check every line against the rules below, and compute the '
            'revenue by region from the lines that pass every rule and have status paid, taxed by region (US-CA '
            '7.25%, US-NY 8.875%, EU-DE 19%, JP 10%; each line\'s taxed amount is quantity x unit_price x (1 + '
            'tax); round each region\'s total half-up to cents at the end).\n'
            '- A line that breaks any rule is anomalous; count each anomalous line once, and also count how many '
            'lines break each rule.\n- Write reports/audit.md with the per-rule counts, the number of anomalous '
            'lines and the revenue by region.\n- Make every existing test pass (the tests are right; the code has '
            'bugs), and add tests for each audit rule.\n- Tell me the number of anomalous lines, the count per '
            'rule, and the revenue for each region, to the cent.\n\nAudit rules:\n' +
            '\n'.join(rule + ': ' + text for rule, text in AUDIT_RULES) + '\n\nCatalogue:\n' + items_csv(cat) +
            '\n\nOrder log:\norder_id,date,sku,quantity,unit_price,region,status\n')
    body = []
    while len(spec) + sum(len(row) + 1 for row in body) < 99000:
        if not orders or rng.random() < .35:
            orders.append('ORD-%05d' % (10000 + len(orders)))
        order_id = rng.choice(orders[-6:])
        sku = rng.choice(cat)['sku']
        line = dict(order_id=order_id, date='2026-08-%02d' % rng.randint(1, 31), sku=sku,
                    quantity=str(rng.randint(1, 12)), unit_price=str(prices[sku]), region=rng.choice(list(REGIONS)),
                    status=rng.choice(['paid'] * 6 + ['refunded', 'pending']))
        roll = rng.random()
        if roll < .03:
            line['quantity'] = rng.choice(['0', '-2', '1.5'])
        elif roll < .06:
            line['sku'] = 'SKU-%04d' % rng.randint(61, 99)
        elif roll < .09:
            line['unit_price'] = str(money(prices[sku] + Decimal(rng.choice(['0.05', '-0.10', '1.00']))))
        elif roll < .12:
            line['region'] = rng.choice(['', 'US-TX', 'eu-de'])
        elif roll < .15:
            line['date'] = rng.choice(['2026-07-31', '2026-09-01', '2026/08/15'])
        elif roll < .17:
            line['status'] = rng.choice(['shipped', 'PAID', ''])
        elif roll < .20 and lines:
            repeat = rng.choice(lines[-30:])
            line = dict(line, order_id=repeat['order_id'], sku=repeat['sku'], unit_price=repeat['unit_price'])
        lines.append(line)
        body.append(','.join(line[key] for key in ('order_id', 'date', 'sku', 'quantity', 'unit_price', 'region',
                                                    'status')))
    prompt = spec + '\n'.join(body) + '\n' + NO_QUESTIONS
    bad, broken, revenue = audit(cat, lines)
    return prompt, dict(anomalous=str(bad), lines=str(len(lines)), **{k: str(v) for k, v in broken.items()},
                        **{k: str(v) for k, v in revenue.items()})


W_TASKS = {
    'W1': ('Fix this project so every test in tests/ passes (the tests are right; the code has bugs), then add '
           'Store.low_stock(threshold), returning the SKUs whose stock is at or below the threshold, sorted, with '
           'tests for it. All tests must pass.' + NO_QUESTIONS),
    'W5': ('Three pieces of work in this project. (1) Make every test in tests/ pass: the tests are right and the '
           'code has bugs. (2) Add inventory/csvio.py with export_items(store, path) and import_items(path, store): '
           'the CSV columns are sku,name,price,stock; import checks every row (a missing field, a price or stock '
           'that is not a number, a negative value, a duplicate SKU) and raises ValueError naming the row number; '
           'export then import gives the same items back. Add Store.low_stock(threshold) too. (3) Write tests for '
           'all of it, at least 12 new ones, and add a Usage section to README.md showing CSV export and import. '
           'All tests must pass.' + NO_QUESTIONS),
    'W25': ('Build order management for this project end to end. First make every test in tests/ pass (the tests '
            'are right; the code has bugs). Then:\n'
            '1. Order lifecycle: pending -> paid -> shipped -> delivered; cancelling a pending or paid order '
            'restocks its items; a refund is allowed only after payment; every invalid transition raises '
            'ValueError.\n'
            '2. Pricing with decimal.Decimal: percentage and fixed discounts, bulk tiers per line (5 or more units: '
            '5% off that line; 20 or more: 10%), tax by region (US-CA 7.25%, US-NY 8.875%, EU-DE 19%, no tax '
            'elsewhere), totals rounded half-up to cents.\n'
            '3. Persistence: save and load the whole store as JSON with atomic writes (write a temporary file, then '
            'replace), a schema version field, and a migration that loads version-1 files (items only, prices as '
            'floats).\n'
            '4. A command-line interface, python -m inventory, with subcommands add-item, restock, order, pay, ship, '
            'deliver, cancel and report, working on the JSON file given by --db.\n'
            '5. Reports: revenue by day and by item, the top 3 sellers, low stock, and the average order value.\n'
            'Write at least 40 new tests across these parts and document the command line in README.md. All tests '
            'must pass.' + NO_QUESTIONS),
}


def tasks():
    p1, a1 = task_p1()
    p5, a5 = task_p5()
    p25, a25 = task_p25()
    return {'W1': (W_TASKS['W1'], {}), 'P1': (p1, a1), 'W5': (W_TASKS['W5'], {}), 'P5': (p5, a5),
            'W25': (W_TASKS['W25'], {}), 'P25': (p25, a25)}


SETS = {'shop': (SEED, tasks), 'fleet': (fleet.SEED, fleet.tasks), 'fleet5': (fleet.SEED, fleet.tasks5)}


# ---------------------------------------------------------------- checking the work

def python(workspace, code):
    run = subprocess.run([sys.executable, '-c', code], cwd=workspace, capture_output=True, text=True, timeout=120)
    return run.returncode == 0, (run.stdout + run.stderr)[-300:]


def pytest_counts(workspace):
    run = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider'], cwd=workspace,
                         capture_output=True, text=True, timeout=600)
    tail = (run.stdout or run.stderr).strip().splitlines()[-1:] or ['']
    passed = int((re.search(r'(\d+) passed', tail[0]) or [0, 0])[1])
    failed = int((re.search(r'(\d+) (failed|error)', tail[0]) or [0, 0])[1])
    return dict(ok=run.returncode == 0, passed=passed, failed=failed, summary=tail[0][:200])


def numbers(text):
    return {found.replace(',', '') for found in re.findall(r'\d[\d,]*\.\d\d(?!\d)|\d[\d,]*', text or '')}


def check(name, workspace, reply, answers):
    if name in fleet.NAMES + fleet.NAMES5:
        return fleet.check(name, workspace, reply, answers, pytest_counts)
    tests = pytest_counts(workspace)
    checks = {'tests pass': tests['ok']}
    if name == 'W1':  # W1 says what low_stock returns: the SKUs at or below the threshold, sorted.
        checks['low_stock'] = python(workspace, 'from inventory.store import Store; s = Store(); '
                                                's.add_item("A", "a", 1.0, stock=2); s.add_item("B", "b", 1.0, stock=9); '
                                                'assert list(s.low_stock(2)) == ["A"]')[0]
    if name == 'W5':  # W5 only asks for low_stock to exist; what it returns is the agent's choice.
        checks['low_stock'] = python(workspace, 'from inventory.store import Store; assert callable(Store().low_stock)')[0]
        checks['csvio'] = python(workspace, 'from inventory import csvio; csvio.export_items; csvio.import_items')[0]
        checks['README Usage'] = 'usage' in (workspace / 'README.md').read_text(encoding='utf-8').casefold()
        checks['12+ new tests'] = tests['passed'] >= 4 + 12
    if name == 'W25':
        checks['CLI'] = subprocess.run([sys.executable, '-m', 'inventory', '--help'], cwd=workspace,
                                       capture_output=True, timeout=60).returncode == 0
        checks['40+ new tests'] = tests['passed'] >= 4 + 40
    if answers:
        found = numbers(reply)
        wanted = {key: value for key, value in answers.items() if key not in ('top', 'lines')}
        hits = {key: value.replace(',', '') in found for key, value in wanted.items()}
        checks['answers'] = str(sum(hits.values())) + '/' + str(len(hits))
        checks['missed'] = [key + '=' + wanted[key] for key, hit in hits.items() if not hit]
    checks = {key: value for key, value in checks.items() if value is not None}
    return tests, checks


# ---------------------------------------------------------------- measuring

def claude_use(events):
    """Claude's tokens over these events, from each result's usage: every model call of that turn, wake-ups
    included (checked against the sum of the calls' own usage, 2026-09-27). A result's modelUsage is the session's
    running total, so adding it up would count earlier turns again: it is not used."""
    total = dict(input=0, output=0, cacheWrite=0, cacheRead=0, turns=0, results=0)
    for event in events:
        if event.get('type') != 'result':
            continue
        usage = event.get('usage') or {}
        total['input'] += usage.get('input_tokens') or 0
        total['output'] += usage.get('output_tokens') or 0
        total['cacheWrite'] += usage.get('cache_creation_input_tokens') or 0
        total['cacheRead'] += usage.get('cache_read_input_tokens') or 0
        total['turns'] += event.get('num_turns') or 0
        total['results'] += 1
    total['weighted'] = round(sum(total[key] * weight for key, weight in WEIGHTS.items()) / 1000, 1)
    return total


def timeline(events, mode, start):
    """Minutes from the prompt to Claude's final answer, and for AUTO its split: Claude before the (first) handoff,
    the agents working (from the first follow's start to the last one's end), Claude after the wake-up."""
    results = [event for event in events if event.get('type') == 'result']
    end = results[-1].get('receivedAt') if results else None
    minutes = lambda a, b: round((b - a) / 60, 2) if a is not None and b is not None else None  # noqa: E731
    out = dict(total=minutes(start, end))
    if mode == 'auto':
        handoff = next((event.get('receivedAt') for event in events if event.get('type') == 'assistant' and any(
            ' handoff ' in str((block.get('input') or {}).get('command') or '') for block in
            (event.get('message') or {}).get('content') or [] if block.get('type') == 'tool_use')), None)
        rows = [event for event in events if event.get('type') == 'system']
        began = next((row.get('receivedAt') for row in rows if row.get('subtype') == 'task_started'), None)
        ended = max((row.get('receivedAt') for row in rows if row.get('subtype') == 'task_notification'), default=None)
        out.update(beforeHandoff=minutes(start, handoff), agentWorking=minutes(began, ended),
                   afterWake=minutes(ended, end))
    return out


def five_hour(events):
    values = []
    for event in events:
        windows = ((event.get('rate_limit_info') or {}).get('unifiedWindows') or {}) if event.get(
            'type') == 'rate_limit_event' else {}
        value = (windows.get('five_hour') or {}).get('utilization')
        if value is not None:
            values.append(round(value * 100 if value <= 1 else value, 1))
    return values


def codex_rollouts(since):
    return [path for path in CODEX_SESSIONS.rglob('rollout-*.jsonl') if path.stat().st_mtime >= since]


def codex_use(workspace, since):
    """Codex's own tokens for sessions whose working folder is this run's project, and its weekly window."""
    total = dict(input=0, cachedInput=0, output=0, reasoning=0, sessions=0)
    weekly = []
    target = os.path.normcase(str(Path(workspace).resolve()))
    for path in codex_rollouts(since):
        rows = []
        for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
        meta = next((row.get('payload') or {} for row in rows if row.get('type') == 'session_meta'), {})
        if os.path.normcase(str(Path(meta.get('cwd') or '.').resolve())) != target:
            continue
        counts = [row['payload'] for row in rows if (row.get('payload') or {}).get('type') == 'token_count']
        last = next((c['info']['total_token_usage'] for c in reversed(counts) if c.get('info')), None)
        if last:
            total['sessions'] += 1
            total['input'] += last.get('input_tokens') or 0
            total['cachedInput'] += last.get('cached_input_tokens') or 0
            total['output'] += last.get('output_tokens') or 0
            total['reasoning'] += last.get('reasoning_output_tokens') or 0
        weekly += [c['rate_limits']['primary']['used_percent'] for c in counts
                   if ((c.get('rate_limits') or {}).get('primary') or {}).get('used_percent') is not None]
    total['uncachedInput'] = total['input'] - total['cachedInput']
    total['weeklyPercent'] = [weekly[0], weekly[-1]] if weekly else None
    return total


def fields(data):
    """A protobuf message's top-level fields as (number, value): varints as ints, the rest as bytes."""
    def varint(at):
        value = shift = 0
        while True:
            byte = data[at]
            at += 1
            value |= (byte & 0x7f) << shift
            shift += 7
            if byte < 0x80:
                return value, at
    at = 0
    while at < len(data):
        key, at = varint(at)
        number, kind = key >> 3, key & 7
        if kind == 0:
            value, at = varint(at)
        elif kind == 2:
            size, at = varint(at)
            value, at = data[at:at + size], at + size
        elif kind in (1, 5):
            size = 8 if kind == 1 else 4
            value, at = data[at:at + size], at + size
        else:
            raise ValueError('protobuf wire type ' + str(kind))
        yield number, value


def agy_use(workspace, since):
    """Antigravity's own tokens for conversations whose working folder is this run's project. Each conversation is
    a SQLite store next to a .meta file naming its folder; each gen_metadata row is one model call, whose field 1.4
    holds its usage (read 2026-09-27): 2 new input, 5 cache read, 3 output, of which 9 is thinking."""
    import sqlite3
    total = dict(input=0, cachedInput=0, output=0, reasoning=0, sessions=0, calls=0)
    target = os.path.normcase(str(Path(workspace).resolve()))
    for meta in AGY_CONVERSATIONS.glob('*.meta'):
        store = meta.with_suffix('.db')
        try:
            folder = json.loads(meta.read_text(encoding='utf-8')).get('cwd') or '.'
            if not store.exists() or store.stat().st_mtime < since or \
                    os.path.normcase(str(Path(folder).resolve())) != target:
                continue
            connection = sqlite3.connect('file:' + store.as_posix() + '?mode=ro', uri=True)
            rows = connection.execute('select data from gen_metadata').fetchall()
            connection.close()
        except (OSError, ValueError, sqlite3.Error):
            continue
        total['sessions'] += 1
        for (data,) in rows:
            try:
                call = dict(fields(data)).get(1)
                usage = dict(fields(dict(fields(call)).get(4) or b'')) if isinstance(call, bytes) else {}
            except (ValueError, IndexError):
                continue
            if not usage:
                continue
            total['calls'] += 1
            total['input'] += usage.get(2, 0) + usage.get(5, 0)
            total['cachedInput'] += usage.get(5, 0)
            total['output'] += usage.get(3, 0)
            total['reasoning'] += usage.get(9, 0)
    total['uncachedInput'] = total['input'] - total['cachedInput']
    return total


def claude_agent_use(workspace, since):
    """A Claude Code AUTO agent's tokens, from ACPX's records of the sessions it ran in this run's project: each
    request's usage, summed (it matched the agent's own transcript to the token, 2026-09-27). The record's
    cumulative_token_usage is only the last request's, despite its name."""
    total = dict(input=0, cachedInput=0, cacheWrite=0, output=0, reasoning=0, sessions=0, calls=0)
    target = os.path.normcase(str(Path(workspace).resolve()))
    for path in (Path.home() / '.acpx' / 'sessions').glob('*.json'):
        try:
            if path.stat().st_mtime < since:
                continue
            record = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if os.path.normcase(str(Path(record.get('cwd') or '.').resolve())) != target or \
                'claude' not in json.dumps(record.get('agent_command') or '').lower():
            continue
        total['sessions'] += 1
        for usage in (record.get('request_token_usage') or {}).values():
            total['calls'] += 1
            total['input'] += usage.get('input_tokens') or 0
            total['cacheWrite'] += usage.get('cache_creation_input_tokens') or 0
            total['cachedInput'] += usage.get('cache_read_input_tokens') or 0
            total['output'] += usage.get('output_tokens') or 0
    # Weighed like Claude's own tokens (same model), before input becomes the total of all three kinds.
    total['weighted'] = round((total['output'] * WEIGHTS['output'] + total['cacheWrite'] * WEIGHTS['cacheWrite'] +
                               total['cachedInput'] * WEIGHTS['cacheRead'] + total['input'] * WEIGHTS['input'])
                              / 1000, 1)
    total['uncachedInput'] = total['input'] + total['cacheWrite']
    total['input'] += total['cacheWrite'] + total['cachedInput']
    return total


AGENT_USE = {'codex': codex_use, 'agy': agy_use, 'claude': claude_agent_use}
SIGNED_OUT = ('Failed to authenticate', 'OAuth session expired', 'Not logged in', 'Please run /login')


# ---------------------------------------------------------------- one run

class SignedOut(RuntimeError):
    """Claude Code could not sign in: every later run would fail the same way, so the test stops."""


def signed_out(reply):
    if any(marker in (reply or '')[:300] for marker in SIGNED_OUT):
        raise SignedOut('Claude Code is signed out: ' + reply[:200])


def claude_signed_in():
    run = subprocess.run([claude_binary(), 'auth', 'status'], capture_output=True, text=True, timeout=60)
    try:
        return bool(json.loads(run.stdout).get('loggedIn'))
    except ValueError:
        return False


class Run:
    def __init__(self, name, mode, prompt, answers, out, options, files=None):
        self.name, self.mode, self.prompt, self.answers, self.out = name, mode, prompt, answers, out
        self.options = options  # The AUTO agent: agent, model, effort, fast.
        self.workspace = Path(tempfile.mkdtemp(prefix='cli-mode-usage-')).resolve()
        seed(self.workspace, files)
        effort = options.get('hostEffort')
        self.host = Session(self.workspace, options.get('hostModel'), EXTRA + (['--effort', effort] if effort else []))
        self.session = None

    def state(self):
        return saved_state(self.session, self.workspace) if self.session else {}

    def idle(self):
        with self.host.lock:
            events = list(self.host.events)
        last_result = max((n for n, e in enumerate(events) if e.get('type') == 'result'), default=-1)
        last_turn = max((n for n, e in enumerate(events) if e.get('type') in ('assistant', 'user')), default=-1)
        running = {e.get('task_id') for e in events if e.get('type') == 'system' and e.get('subtype') == 'task_started'}
        ended = {e.get('task_id') for e in events if e.get('type') == 'system'
                 and e.get('subtype') == 'task_notification'}
        return last_result > last_turn and not (running - ended - {None})

    def settled(self):
        records = [r for r in (self.state().get('requests') or {}).values() if r.get('routingMode') == 'auto']
        return self.idle() and not any(r.get('status') in WORKING or not r.get('hostRead') for r in records)

    def control(self, prompt, timeout=400):
        count = len(self.host.results())
        self.host.send(prompt)
        if not self.host.wait(count + 1, timeout):
            raise RuntimeError(prompt + ': no reply')
        with self.host.lock:
            if self.session is None:
                self.session = next(e.get('session_id') for e in self.host.events if e.get('session_id'))
            reply = next((e.get('result') or '' for e in reversed(self.host.events) if e.get('type') == 'result'), '')
        signed_out(reply)
        return reply

    def start_auto(self):
        sys.path.insert(0, str(DEV / 'scripts'))
        import adapters
        import auto_mode
        agent = self.options['agent']
        adapter = adapters.module(agent)
        selected = adapter.selection(claude_data(), self.options.get('model') or adapter.DEFAULTS['model'],
                                     adapter.DEFAULTS['access'], self.options.get('effort') or
                                     adapter.DEFAULTS.get('effort'))
        chosen = auto_mode.entry_of(agent, selected)
        if self.options.get('fast') is not None:
            chosen['fast'] = self.options['fast']
        auto_mode.save(claude_data(), {'agent': chosen, 'backup': None, 'strength': 'strong'})
        listing = self.control('/cli')
        if '1 starts your saved AUTO agent.' not in listing:
            raise RuntimeError('/cli did not offer the saved AUTO agent: ' + listing[:200])
        card = self.control('1')
        if 'AUTO is on' not in card:
            raise RuntimeError('AUTO did not start: ' + card[:300])

    def go(self, timeout):
        started = time.time()
        if self.mode == 'auto':
            self.start_auto()
        else:
            self.control('Reply with the single word ready.', timeout=120)  # The session exists; nothing else.
        mark, count = self.host.mark(), len(self.host.results())
        clock = time.monotonic()
        self.host.send(self.prompt)
        if not self.host.wait(count + 1, timeout):
            raise RuntimeError('no result within ' + str(timeout) + ' s')
        until = clock + timeout
        while time.monotonic() < until:
            if self.host.wait(None, max(1, until - time.monotonic()), done=self.settled):
                time.sleep(QUIET)
                if self.settled():
                    break
        else:
            raise RuntimeError('did not settle within ' + str(timeout) + ' s')
        with self.host.lock:
            events = list(self.host.events[mark:])
            everything = list(self.host.events)
        times = timeline(events, self.mode, clock)
        replies = [e.get('result') or '' for e in events if e.get('type') == 'result']
        for reply in replies:
            signed_out(reply)
        blocks = [block for e in events if e.get('type') == 'assistant'
                  for block in (e.get('message') or {}).get('content') or [] if block.get('type') == 'tool_use']
        tools = [block.get('name') for block in blocks]
        written = sum(len(str((block.get('input') or {}).get('content') or (block.get('input') or {}).get('new_string')
                              or '')) for block in blocks if block.get('name') in ('Write', 'Edit', 'MultiEdit'))
        records = [r for r in (self.state().get('requests') or {}).values() if r.get('routingMode') == 'auto']
        if self.mode == 'auto':
            self.control('/cli off', timeout=120)
        tests, checks = check(self.name, self.workspace, '\n'.join(replies), self.answers)
        reader = AGENT_USE.get(self.options['agent']) if self.mode == 'auto' else None
        return dict(task=self.name, mode=self.mode, promptChars=len(self.prompt), promptTokensApprox=len(self.prompt) // 4,
                    minutes=times['total'], time=times, claude=claude_use(events), claudeFiveHour=five_hour(everything),
                    agent=self.options['agent'] if self.mode == 'auto' else None,
                    agentTokens=reader(self.workspace, started - 5) if reader else None,
                    tools=len(tools), claudeWroteChars=written,
                    toolNames=sorted(set(tools)), subagents=tools.count('Agent') + tools.count('Task'),
                    handoffs=len(records), handoffFiles=[(r.get('handoff') or {}).get('files') for r in records],
                    tests=tests, checks=checks, reply=replies[-1][-1500:] if replies else '', events=everything)

    def close(self):
        self.host.close()
        if self.session:
            sys.path.insert(0, str(DEV / 'scripts'))
            from controller import Controller
            from state import Store
            store = Store(self.session, self.workspace, claude_data())
            if store.path.exists():
                if store.read().get('owned'):
                    Controller(store).off()
                key = hashlib.sha256(self.session.encode()).hexdigest()
                for path in (claude_data() / 'sessions').glob(key + '*'):
                    path.unlink(missing_ok=True)

        def writable(func, path, exc):
            os.chmod(path, stat.S_IWRITE)
            func(path)
        shutil.rmtree(self.workspace, onexc=writable)


# ---------------------------------------------------------------- the whole test

def mean(values):
    values = [value for value in values if isinstance(value, (int, float))]
    if not values:
        return None
    average = sum(values) / len(values)
    return int(average) if float(average).is_integer() else round(average, 2)


def cells(results):
    """The runs grouped by (task, mode), in run order, with their numbers averaged over repeats."""
    grouped = {}
    for result in results:
        grouped.setdefault((result['task'], result['mode']), []).append(result)
    out = []
    for (task, mode), runs in grouped.items():
        good = [run for run in runs if 'error' not in run]
        cell = dict(task=task, mode=mode, runs=len(runs), errors=[run['error'] for run in runs if 'error' in run])
        if good:
            cell.update(claude={key: mean([run['claude'][key] for run in good]) for key in
                                ('output', 'cacheRead', 'cacheWrite', 'input', 'turns', 'weighted')},
                        time={key: mean([(run.get('time') or {}).get(key) for run in good]) for key in
                              ('total', 'beforeHandoff', 'agentWorking', 'afterWake')},
                        tools=mean([run['tools'] for run in good]), handoffs=mean([run['handoffs'] for run in good]),
                        wrote=mean([run.get('claudeWroteChars') for run in good]),
                        tests=[run['tests']['passed'] for run in good],
                        passed=all(run['tests']['ok'] for run in good),
                        checks=[{k: v for k, v in run['checks'].items() if k != 'tests pass'} for run in good],
                        agent=next((run.get('agent') for run in good if run.get('agent')), None))
            agent = [run['agentTokens'] for run in good if (run.get('agentTokens') or {}).get('sessions')]
            if agent:
                cell['agentTokens'] = {key: mean([item.get(key) for item in agent]) for key in
                                       ('input', 'cachedInput', 'uncachedInput', 'output', 'reasoning', 'weighted',
                                        'calls')}
        out.append(cell)
    return out


def report(results, out, options):
    """report.md: Claude alone, AUTO's Claude and its agent, time, and quality, per task (averaged over repeats)."""
    table = cells(results)
    native = {cell['task']: cell for cell in table if cell['mode'] == 'native' and 'claude' in cell}
    agent = ', '.join(str(options.get(key)) for key in ('agent', 'model', 'effort') if options.get(key))
    if options.get('fast') is not None:
        agent += ', fast mode ' + ('on' if options['fast'] else 'off')
    claude_host = ', '.join(str(options.get(key)) for key in ('hostModel', 'hostEffort') if options.get(key))
    lines = ['# CLI-MODE usage test', '', 'AUTO agent: ' + agent, '',
             'Claude Code (host): ' + (claude_host or 'its configured model and effort'), '',
             '## Claude alone (native)', '',
             '| Task | Output | Cache read | Cache write | Turns | Tool calls | Runs |', '|---|---|---|---|---|---|---|']
    for cell in table:
        if cell['mode'] == 'native' and 'claude' in cell:
            c = cell['claude']
            lines.append('| %s | %s | %s | %s | %s | %s | %d |' % (cell['task'], c['output'], c['cacheRead'],
                                                                c['cacheWrite'], c['turns'], cell['tools'], cell['runs']))
    lines += ['', '## AUTO: Claude, and its agent', '',
              '| Task | Claude output | Claude cache read / write | Claude turns | Claude wrote (chars) | Handoffs | '
              'Agent input (cached / new) | Agent output (of it reasoning) | Claude vs native | Claude + agent vs '
              'native |', '|---|---|---|---|---|---|---|---|---|---|']
    for cell in table:
        if cell['mode'] == 'auto' and 'claude' in cell:
            c, x = cell['claude'], cell.get('agentTokens')
            base = native.get(cell['task'])
            percent = lambda value: (str(round(100 * value / base['claude']['weighted'])) + '%'  # noqa: E731
                                     if base and base['claude']['weighted'] else '-')
            lines.append('| %s | %s | %s / %s | %s | %s | %s | %s | %s | %s | %s |' % (
                cell['task'], c['output'], c['cacheRead'], c['cacheWrite'], c['turns'], cell['wrote'], cell['handoffs'],
                '%s (%s / %s)' % (x['input'], x['cachedInput'], x['uncachedInput']) if x else 'not read',
                '%s (%s)' % (x['output'], x['reasoning']) if x else 'not read', percent(c['weighted']),
                percent(c['weighted'] + x['weighted']) if x and x.get('weighted') is not None else '-'))
    lines += ['', "Claude vs native weighs Claude's tokens by relative API prices (output 20, cache write 5, cache "
              "read 0.2): a stand-in for plan usage, not a price. Claude + agent adds the agent's tokens, weighed "
              "the same way, when the agent is Claude Code too (the same model: the harness's own cost). The "
              "agent's reasoning is part of its output.", '',
              '## Time to completion (minutes, prompt to final answer)', '',
              '| Task | Native | AUTO | AUTO: Claude before handoff / agent working / Claude after wake |',
              '|---|---|---|---|']
    for task in dict.fromkeys(cell['task'] for cell in table):
        n = next((cell for cell in table if cell['task'] == task and cell['mode'] == 'native' and 'time' in cell), None)
        a = next((cell for cell in table if cell['task'] == task and cell['mode'] == 'auto' and 'time' in cell), None)
        split = ' / '.join(str(a['time'][key]) for key in ('beforeHandoff', 'agentWorking', 'afterWake')) if a else '-'
        lines.append('| %s | %s | %s | %s |' % (task, n['time']['total'] if n else '-',
                                                a['time']['total'] if a else '-', split))
    lines += ['', '## Quality', '', '| Task | Mode | Tests passed | All pass | Checks | Errors |',
              '|---|---|---|---|---|---|']
    for cell in table:
        lines.append('| %s | %s | %s | %s | %s | %s |' % (
            cell['task'], cell['mode'], cell.get('tests', '-'), cell.get('passed', '-'),
            json.dumps(cell.get('checks', ''))[:160], '; '.join(error[:80] for error in cell['errors']) or '-'))
    windows = [(r['task'], r['mode'], (r.get('claudeFiveHour') or [])[:1] + (r.get('claudeFiveHour') or [])[-1:],
                (r.get('agentTokens') or {}).get('weeklyPercent')) for r in results if 'error' not in r]
    lines += ['', 'Plan windows (Claude 5-hour %, agent weekly %, first and last seen): ' + json.dumps(windows), '']
    text = '\n'.join(lines)
    (out / 'report.md').write_text(text, encoding='utf-8')
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--set', default='shop', choices=sorted(SETS), help='The task set (default: shop).')
    parser.add_argument('--only', help='Tasks, such as W1,P1 (default: all of the set).')
    parser.add_argument('--modes', default='native,auto')
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--agent', default='codex', help='The AUTO agent.')
    parser.add_argument('--model')
    parser.add_argument('--effort')
    parser.add_argument('--fast', choices=['on', 'off'])
    parser.add_argument('--host-model', help="Claude Code's model for the host, native and AUTO alike (default: "
                                             'the configured one). With --agent claude, give both the same model to '
                                             'measure the harness alone.')
    parser.add_argument('--host-effort', help="Claude Code's effort for the host.")
    parser.add_argument('--out', type=Path)
    parser.add_argument('--dry', action='store_true', help='Write the prompts and answers only.')
    args = parser.parse_args()
    out = args.out or Path(tempfile.gettempdir()) / ('cli-mode-usage-' + time.strftime('%Y%m%d-%H%M%S'))
    out.mkdir(parents=True, exist_ok=True)
    options = dict(agent=args.agent, model=args.model, effort=args.effort,
                   fast=None if args.fast is None else args.fast == 'on', hostModel=args.host_model,
                   hostEffort=args.host_effort)
    files, make = SETS[args.set]
    all_tasks = make()
    for name, (prompt, answers) in all_tasks.items():
        (out / (name + '.prompt.txt')).write_text(prompt, encoding='utf-8')
        (out / (name + '.answers.json')).write_text(json.dumps(
            {key: value for key, value in answers.items() if key != 'csv'}, indent=1), encoding='utf-8')
    print(json.dumps({name: dict(chars=len(p), tokensApprox=len(p) // 4) for name, (p, _) in all_tasks.items()}))
    if args.dry:
        print('RESULTS', out)
        return
    sys.path.insert(0, str(DEV / 'scripts'))
    import host
    host.select(host.CLAUDE)
    import auto_mode
    saved = auto_mode.config_path(claude_data())
    aside = saved.with_suffix('.before-usage-test')
    if saved.exists():
        saved.replace(aside)
    names = args.only.split(',') if args.only else list(all_tasks)
    results, stopped = [], None
    try:
        for name in names:
            prompt, answers = all_tasks[name]
            for mode in args.modes.split(','):
                for repeat in range(1, args.repeat + 1):
                    last = next((r['claudeFiveHour'][-1] for r in reversed(results) if r.get('claudeFiveHour')), None)
                    if last is not None and last >= STOP_AT:
                        stopped = 'the 5-hour window is at ' + str(last) + '%'
                    elif not stopped and not claude_signed_in():
                        stopped = 'Claude Code is signed out'
                    if stopped:
                        results.append(dict(task=name, mode=mode, repeat=repeat, error='skipped: ' + stopped))
                        continue
                    print(time.strftime('%I:%M:%S %p'), 'start', name, mode, repeat, flush=True)
                    run = Run(name, mode, prompt, answers, out, options, files)
                    try:
                        result = run.go(TIMEOUT[name[1:]])
                    except SignedOut as exc:
                        stopped = str(exc)
                        result = dict(task=name, mode=mode, error=str(exc), events=list(run.host.events))
                    except (RuntimeError, ValueError, KeyError, StopIteration, OSError) as exc:
                        with run.host.lock:
                            events = list(run.host.events)
                        result = dict(task=name, mode=mode, error=str(exc)[:500], events=events,
                                      claudeFiveHour=five_hour(events))
                    finally:
                        try:
                            run.close()
                        except OSError as exc:
                            print('cleanup failed:', exc, flush=True)
                    result['repeat'] = repeat
                    events = result.pop('events', [])
                    (out / ('%s-%s-%d.events.jsonl' % (name, mode, repeat))).write_text(
                        '\n'.join(json.dumps(event) for event in events), encoding='utf-8')
                    results.append(result)
                    (out / 'results.json').write_text(json.dumps(results, indent=1, default=str), encoding='utf-8')
                    print(time.strftime('%I:%M:%S %p'), 'done', name, mode, repeat, json.dumps(
                        {k: result.get(k) for k in ('minutes', 'handoffs', 'checks', 'error')}, default=str)[:400],
                        flush=True)
    finally:
        if aside.exists():
            aside.replace(saved)
        elif saved.exists():
            saved.unlink()
    print(report(results, out, options))
    print('RESULTS', out)


if __name__ == '__main__':
    main()
