"""The `multi` task set for checks/claude_usage_live.py: work orders of independent parts, built to draw out parallel
handoffs. Each part has its own module, its own data to save exactly, its own tests and its own exact answers, and
shares no file with another part. M2 is about 2.5k tokens of prompt (3 parts), M12 about 12.5k (4 parts), M50 about
50k (5 parts: long enough that CLI-MODE saves it for the agents). Hidden acceptance tests check each part's function."""
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
import heapq
from pathlib import Path
import random
import re
import statistics
import subprocess
import sys
import tempfile

NAMES = ('M2', 'M12', 'M50')  # L5 (the sequential control) is checked the same way: ALL_NAMES.
NO_QUESTIONS = ' Work without asking me questions: make reasonable assumptions and say what they were. Do not commit.'
CENT = Decimal('0.01')


def cents(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------- the parts: data, exact answers, spec, hidden test

def routes(rng, chars):
    depots = ['D%02d' % n for n in range(1, max(41, chars // 60) + 1)]
    edges, seen, size = [], set(), 0
    for a, b in zip(depots, depots[1:]):  # A chain first, so every depot is reachable.
        edges.append((a, b, rng.randint(20, 60)))
        seen.add((a, b))
    while size < chars or len(edges) < 45:
        a, b = rng.sample(depots, 2)
        if (a, b) in seen or (b, a) in seen:
            continue
        seen.add((a, b))
        edges.append((a, b, rng.randint(5, 90)))
        size += len('%s,%s,%d' % edges[-1]) + 1
    graph = {}
    for a, b, minutes in edges:
        graph.setdefault(a, []).append((b, minutes))
        graph.setdefault(b, []).append((a, minutes))

    def shortest(start, end):
        best, queue = {start: 0}, [(0, start)]
        while queue:
            cost, node = heapq.heappop(queue)
            if node == end:
                return cost
            for other, minutes in graph[node]:
                if cost + minutes < best.get(other, 1e9):
                    best[other] = cost + minutes
                    heapq.heappush(queue, (cost + minutes, other))
    pairs = [('D01', 'D40'), ('D07', 'D33'), ('D12', 'D25')]
    data = 'a,b,minutes\n' + '\n'.join('%s,%s,%d' % edge for edge in edges) + '\n'
    answers = {'route %s-%s' % pair: str(shortest(*pair)) for pair in pairs}
    spec = ('Routes: fleetlog/routes.py with load_edges(path), reading data/routes.csv (undirected roads between '
            'depots: a,b,minutes), and shortest_minutes(edges, start, end), the fewest minutes from start to end '
            '(0 from a depot to itself, None when it cannot be reached), edges being (a, b, minutes) tuples. Tell me '
            'the shortest minutes ' + ', '.join('from %s to %s' % pair for pair in pairs) + '.')
    test = ('def test_routes():\n    from fleetlog.routes import shortest_minutes\n'
            '    edges = [("A", "B", 5), ("B", "C", 7), ("A", "C", 20), ("D", "E", 1)]\n'
            '    assert shortest_minutes(edges, "A", "C") == 12 and shortest_minutes(edges, "C", "A") == 12\n'
            '    assert shortest_minutes(edges, "A", "D") is None and shortest_minutes(edges, "A", "A") == 0\n')
    return 'routes', data, answers, spec, test


BANDS = [('00:00', '06:00', Decimal('1.20')), ('06:00', '10:00', Decimal('3.50')), ('10:00', '16:00', Decimal('2.40')),
         ('16:00', '19:00', Decimal('3.80')), ('19:00', '24:00', Decimal('1.60'))]


def tolls(rng, chars):
    vehicles, gantries = ['V%02d' % n for n in range(1, 13)], ['G%d' % n for n in range(1, 7)]
    rows, size, clock = [], 0, datetime(2026, 9, 14, 0, 0)
    while size < chars:
        clock += timedelta(minutes=rng.randint(1, 9))
        vehicle, gantry = rng.choice(vehicles), rng.choice(gantries)
        rows.append((vehicle, gantry, clock))
        if rng.random() < .12:  # A second read of the same passage, a few minutes later.
            rows.append((vehicle, gantry, clock + timedelta(minutes=rng.randint(1, 4))))
        size = len(rows) * 27  # vehicle,gantry,ts: 26 characters and a newline.
    rows.sort(key=lambda row: row[2])
    charged, totals = {}, {}
    for vehicle, gantry, ts in rows:
        last = charged.get((vehicle, gantry))
        if last is not None and ts - last <= timedelta(minutes=5):
            continue
        charged[(vehicle, gantry)] = ts
        clock_text = ts.strftime('%H:%M')
        price = next(p for start, end, p in BANDS if start <= clock_text < end)
        totals[vehicle] = totals.get(vehicle, Decimal(0)) + price
    top = max(totals, key=totals.get)
    if sorted(totals.values())[-2] == totals[top]:
        return tolls(rng, chars + 200)  # One vehicle must pay the most.
    data = 'vehicle,gantry,ts\n' + '\n'.join('%s,%s,%s' % (v, g, t.strftime('%Y-%m-%d %H:%M')) for v, g, t in rows) + '\n'
    answers = {'tolls total': str(sum(totals.values(), Decimal(0))), 'tolls top vehicle': top,
               'tolls top amount': str(totals[top])}
    spec = ('Tolls: fleetlog/tolls.py with toll_totals(events, bands): events are (vehicle, gantry, datetime) '
            'passages (data/tolls.csv: vehicle,gantry,ts); bands are (start "HH:MM", end "HH:MM", price as a Decimal), '
            'start included, end not, covering the day: ' + '; '.join('%s-%s %s' % band for band in BANDS) + '. A '
            'passage costs the band price at its time, but a passage of the same vehicle at the same gantry within 5 '
            'minutes of that pair\'s last charged passage is a second read and costs nothing. Return {vehicle: total} '
            'as Decimals. Tell me the total of all vehicles, and the vehicle that pays the most with its total.')
    test = ('def test_tolls():\n    from decimal import Decimal\n    from fleetlog.tolls import toll_totals\n'
            '    bands = ' + repr([(s, e, str(p)) for s, e, p in BANDS]).replace("'", '"') + '\n'
            '    bands = [(s, e, Decimal(p)) for s, e, p in bands]\n    at = lambda h, m: datetime(2026, 9, 14, h, m)\n'
            '    events = [("V1", "G1", at(7, 0)), ("V1", "G1", at(7, 3)), ("V1", "G1", at(11, 0)),\n'
            '              ("V2", "G2", at(5, 59)), ("V2", "G2", at(6, 0))]\n    totals = toll_totals(events, bands)\n'
            '    assert totals["V1"] == Decimal("5.90") and totals["V2"] == Decimal("1.20")\n')
    return 'tolls', data, answers, spec, test


ZONES = {'north_yard': (40.60, -3.80, 40.75, -3.60), 'city_core': (40.38, -3.75, 40.48, -3.62),
         'airport': (40.46, -3.62, 40.53, -3.52), 'south_hub': (40.25, -3.75, 40.35, -3.60)}


def zones(rng, chars):
    rows, size, n = [], 0, 0
    while size < chars:
        n += 1
        lat, lon = round(rng.uniform(40.20, 40.80), 4), round(rng.uniform(-3.85, -3.45), 4)
        rows.append(('P%05d' % n, lat, lon))
        size += len('%s,%.4f,%.4f' % rows[-1]) + 1
    counts = {name: sum(1 for _, lat, lon in rows if a <= lat <= c and b <= lon <= d)
              for name, (a, b, c, d) in ZONES.items()}
    data = 'id,lat,lon\n' + '\n'.join('%s,%.4f,%.4f' % row for row in rows) + '\n'
    answers = {'zone ' + name: str(count) for name, count in counts.items()}
    spec = ('Zones: fleetlog/zones.py with zone_counts(points, zones): points are (lat, lon) pairs (data/zones.csv: '
            'id,lat,lon), zones are {name: (min_lat, min_lon, max_lat, max_lon)} rectangles, edges included, and a '
            'point may be in several zones; return {name: count} with every zone, 0 included. The zones: ' +
            '; '.join('%s %s' % (name, box) for name, box in ZONES.items()) + '. Tell me the count for each zone.')
    test = ('def test_zones():\n    from fleetlog.zones import zone_counts\n'
            '    zones = {"north": (10, 0, 20, 10), "core": (0, 0, 10, 10), "empty": (50, 50, 60, 60)}\n'
            '    counts = zone_counts([(10, 5), (5, 5), (30, 30)], zones)\n'
            '    assert (counts["north"], counts["core"], counts["empty"]) == (1, 2, 0)\n')
    return 'zones', data, answers, spec, test


def fuel(rng, chars):
    stations = {'S%d' % n: Decimal(rng.randint(150, 190)) / 100 for n in range(1, 7)}
    cards = ['C%03d' % n for n in range(1, 31)]
    rows, size, clock, n = [], 0, datetime(2026, 9, 1, 6, 0), 0
    while size < chars:
        n += 1
        clock += timedelta(minutes=rng.randint(3, 40))
        station, card = rng.choice(list(stations)), rng.choice(cards)
        price = cents(stations[station] * Decimal(rng.uniform(.93, 1.07)))
        litres = Decimal(rng.randint(150, 950)) / 10
        roll = rng.random()
        if roll < .05:
            litres = Decimal(rng.randint(1250, 1800)) / 10  # Over capacity.
        elif roll < .09:
            price = cents(stations[station] * Decimal(rng.uniform(1.35, 1.6)))  # Price outlier.
        rows.append(['T%05d' % n, card, station, clock, litres, price])
        if rng.random() < .05:  # The same fill again, minutes later.
            n += 1
            rows.append(['T%05d' % n, card, station, clock + timedelta(minutes=rng.randint(1, 9)), litres, price])
        size = len(rows) * 45  # About 45 characters a transaction.
    rows.sort(key=lambda row: row[3])
    medians = {s: statistics.median([r[5] for r in rows if r[2] == s]) for s in stations}
    flagged, last = set(), {}
    for i, card, station, ts, litres, price in rows:
        before = last.get((card, station))
        if before is not None and ts - before <= timedelta(minutes=10):
            flagged.add(i)
        last[(card, station)] = ts
        if litres > 120 or price > medians[station] * Decimal('1.2'):
            flagged.add(i)
    spend = cents(sum((l * p for i, c, s, t, l, p in rows if i not in flagged), Decimal(0)))
    data = 'id,card,station,ts,litres,price_per_litre\n' + '\n'.join(
        '%s,%s,%s,%s,%s,%s' % (i, c, s, t.strftime('%Y-%m-%d %H:%M'), l, p) for i, c, s, t, l, p in rows) + '\n'
    answers = {'fuel flagged': str(len(flagged)), 'fuel clean spend': str(spend)}
    spec = ('Fuel cards: fleetlog/fuelcards.py with flag(rows): rows are (id, card, station, datetime, litres, '
            'price_per_litre) transactions, litres and price as Decimals (data/fuel.csv); return the set of ids flagged by any rule: a repeat (the '
            'same card at the same station within 10 minutes of that pair\'s previous transaction, in time order; '
            'the later one is flagged), over capacity (more than 120 litres), or a price outlier (more than 20% above '
            'the median price of that station\'s transactions). Tell me how many transactions are flagged and the '
            'spend (litres x price, to the cent) of the rest.')
    test = ('def test_fuel():\n    from decimal import Decimal as D\n    from fleetlog.fuelcards import flag\n'
            '    at = lambda h, m: datetime(2026, 9, 1, h, m)\n'
            '    rows = [("t1", "C1", "S1", at(8, 0), D("40"), D("1.50")), ("t2", "C1", "S1", at(8, 5), D("30"), D("1.50")),\n'
            '            ("t3", "C2", "S1", at(9, 0), D("130"), D("1.50")), ("t4", "C3", "S2", at(9, 0), D("40"), D("1.60")),\n'
            '            ("t5", "C4", "S2", at(10, 0), D("40"), D("2.10")), ("t6", "C5", "S2", at(11, 0), D("40"), D("1.62"))]\n'
            '    assert set(flag(rows)) == {"t2", "t3", "t5"}\n')
    return 'fuel', data, answers, spec, test


INTERVALS = {'oil': (10000, 180), 'tyres': (30000, 730), 'brakes': (20000, 365)}
TODAY = date(2026, 9, 15)


def service(rng, chars):
    vehicles = ['V%03d' % n for n in range(1, 61)]
    rows, size, odometers = [], 0, {v: rng.randint(40000, 160000) for v in vehicles}
    while size < chars:
        vehicle, kind = rng.choice(vehicles), rng.choice(list(INTERVALS))
        day = TODAY - timedelta(days=rng.randint(20, 900))
        km = max(1000, odometers[vehicle] - rng.randint(1000, 40000))
        rows.append((vehicle, day, km, kind))
        size += len('%s,%s,%d,%s' % (vehicle, day.isoformat(), km, kind)) + 1
    latest = {}
    for vehicle, day, km, kind in rows:
        if (vehicle, kind) not in latest or day > latest[(vehicle, kind)][0]:
            latest[(vehicle, kind)] = (day, km)
    overdue = sorted(key for key, (day, km) in latest.items()
                     if odometers[key[0]] >= km + INTERVALS[key[1]][0] or TODAY >= day + timedelta(days=INTERVALS[key[1]][1]))
    data = 'vehicle,date,km,kind\n' + '\n'.join('%s,%s,%d,%s' % (v, d.isoformat(), k, t) for v, d, k, t in rows) + '\n'
    answers = {'service overdue': str(len(overdue)),
               **{'service overdue ' + kind: str(sum(1 for _, k in overdue if k == kind)) for kind in INTERVALS}}
    spec = ('Service: fleetlog/service.py with overdue(services, odometers, today): services are (vehicle, date, km, '
            'kind) rows (data/service.csv), odometers {vehicle: current km}; for each vehicle and kind, its latest '
            'service by date is due again after ' + ', '.join('%s %d km or %d days' % (k, a, b) for k, (a, b) in
                                                                  INTERVALS.items()) + ', whichever comes first; '
            'return the sorted (vehicle, kind) pairs whose odometer has reached the due km or whose due date is on or '
            'before today. The current odometers: ' + ', '.join('%s %d' % item for item in sorted(odometers.items())) +
            '. Tell me how many pairs are overdue on ' + TODAY.isoformat() + ', in all and for each kind.')
    test = ('def test_service():\n    from datetime import date\n    from fleetlog.service import overdue\n'
            '    services = [("V1", date(2026, 1, 1), 10000, "oil"), ("V1", date(2026, 6, 1), 15000, "oil"),\n'
            '                ("V2", date(2025, 1, 1), 5000, "brakes")]\n'
            '    assert overdue(services, {"V1": 25100, "V2": 6000}, date(2026, 6, 15)) == [("V1", "oil"), ("V2", "brakes")]\n')
    return 'service', data, answers, spec, test


PARTS = {'M2': (routes, tolls, zones), 'M12': (routes, tolls, zones, fuel), 'M50': (routes, tolls, zones, fuel, service)}
TARGET = {'M2': 10000, 'M12': 50000, 'M50': 200000}  # Characters: about 2.5k, 12.5k and 50k tokens.


def build(name):
    """The work order for `name`: its prompt, the answers and files to check, and its hidden tests."""
    rng, parts = random.Random(len(name) * 7919 + sum(map(ord, name))), PARTS[name]
    specs_only = sum(len(part(random.Random(1), 3000)[3]) for part in parts)
    budget = max(400, (TARGET[name] - specs_only - 900) // len(parts))
    built = [part(rng, budget) for part in parts]
    sections = []
    for number, (key, data, answers, spec, test) in enumerate(built, 1):
        sections.append('Part %d. %s Save the data below exactly to data/%s.csv, and write tests for this part in '
                        'tests/test_%s.py.\n----- BEGIN %s DATA -----\n%s----- END %s DATA -----'
                        % (number, spec, key, key, key.upper(), data, key.upper()))
    prompt = ('A work order for fleetlog in %d independent parts. Each part has its own module, data file and test '
              'file, and shares no file with another part, so they can be done in any order or at the same time. '
              'Every existing test must still pass, and each part\'s new tests too. When done, give me every answer '
              'asked for below.\n\n' % len(built)) + '\n\n'.join(sections) + '\n' + NO_QUESTIONS
    answers, files, tests = {}, {}, []
    for key, data, part_answers, _, test in built:
        answers.update(part_answers)
        files['data/%s.csv' % key] = data
        tests.append(test)
    return prompt, dict(answers=answers, files=files, test='\n\n'.join(tests))


def pipeline_order():
    """L5, the sequential control: one pipeline in one module, each step built on the one before, about 5k tokens
    of prompt. It should go out whole (Claude's or one agent's), never split."""
    rng = random.Random(505050)
    drivers = ['Ana', 'Ben', 'Caro', 'Dev', 'Eli', 'Fay', 'Gus', 'Hal', 'Ivy', 'Jon']
    economy = {d: rng.uniform(7.0, 13.0) for d in drivers}
    rows, n, clock = [], 0, datetime(2026, 9, 1, 6, 0)
    while sum(len(','.join(map(str, r))) + 1 for r in rows) < 15500:
        n += 1
        driver = rng.choice(drivers)
        start = clock + timedelta(minutes=rng.randint(10, 90))
        clock = start
        km = Decimal(rng.randint(80, 4200)) / 10
        end = start + timedelta(minutes=max(5, int(float(km) / rng.uniform(35, 80) * 60)))
        fuel = (km * Decimal(economy[driver] * rng.uniform(.9, 1.1)) / 100).quantize(Decimal('0.1'))
        roll = rng.random()
        if roll < .04:
            end = start - timedelta(minutes=rng.randint(1, 30))  # Ends before it starts.
        elif roll < .07:
            km = Decimal(0)
        elif roll < .09:
            fuel = Decimal('-' + str(rng.randint(1, 20)))
        trip = 'TR%05d' % n
        rows.append((trip, 'V%02d' % rng.randint(1, 15), start.strftime('%Y-%m-%d %H:%M'),
                     end.strftime('%Y-%m-%d %H:%M'), str(km), str(fuel), driver))
        if rng.random() < .05:  # The same trip exported twice.
            rows.append(rows[-1])
    seen, valid = set(), []
    for trip, vehicle, start, end, km, fuel, driver in rows:
        if trip in seen:
            continue
        seen.add(trip)
        if end > start and Decimal(km) > 0 and Decimal(fuel) >= 0:
            valid.append((driver, Decimal(km), Decimal(fuel)))
    stats = {}
    for driver, km, fuel in valid:
        entry = stats.setdefault(driver, dict(trips=0, km=Decimal(0), rates=[]))
        entry['trips'] += 1
        entry['km'] += km
        entry['rates'].append(fuel / km * 100)
    ranked = sorted((d for d, e in stats.items() if e['trips'] >= 5),
                    key=lambda d: (statistics.median(stats[d]['rates']), d))
    data = 'trip_id,vehicle,start,end,km,fuel_l,driver\n' + '\n'.join(','.join(r) for r in rows) + '\n'
    answers = {'valid trips': str(len(valid)), 'total km': str(sum((km for _, km, _ in valid), Decimal(0))),
               'best driver': ranked[0], 'worst driver': ranked[-1]}
    prompt = (
        'Build our trip-report pipeline in fleetlog/pipeline.py. It is one pipeline: each step uses the one before, '
        'so build and check them in order.\n'
        '1. load_trips(path): read data/trips.csv (trip_id,vehicle,start,end,km,fuel_l,driver; times as '
        'YYYY-MM-DD HH:MM) into dicts with start and end as datetimes and km and fuel_l as Decimals.\n'
        '2. clean(trips): keep, in order, the trips whose end is after their start, whose km is above 0 and whose '
        'fuel_l is 0 or more; a trip_id seen before is a repeat and is dropped, whether or not the first was kept.\n'
        '3. economy(trip): its litres per 100 km (fuel_l / km x 100), as a Decimal.\n'
        '4. summary(trips): for the cleaned trips, {driver: {"trips": count, "km": total km, "median": the median '
        'economy of that driver\'s trips}}.\n'
        '5. ranking(summary, min_trips=5): the drivers with at least min_trips trips, from the lowest median economy '
        'to the highest, ties by name.\n'
        '6. Write reports/drivers.md: a table of every driver\'s trips, km and median economy, then the ranking.\n'
        'Save the trips below exactly to data/trips.csv first; tests for every step go in tests/test_pipeline.py, and '
        'every existing test must still pass. Tell me how many trips are valid after cleaning, their total km, and '
        'the best and the worst driver in the ranking.\n----- BEGIN TRIPS DATA -----\n' + data +
        '----- END TRIPS DATA -----\n' + NO_QUESTIONS)
    test = ('def test_pipeline():\n    from decimal import Decimal as D\n    from fleetlog import pipeline\n'
            '    at = lambda h: datetime(2026, 9, 1, h, 0)\n'
            '    t = lambda i, s, e, km, fuel, d: dict(trip_id=i, vehicle="V1", start=at(s), end=at(e), km=D(km), '
            'fuel_l=D(fuel), driver=d)\n'
            '    trips = [t("a", 8, 9, "100", "8", "Ann"), t("b", 9, 8, "50", "4", "Ann"), t("a", 10, 11, "100", "9", '
            '"Ann"), t("c", 10, 11, "0", "1", "Bo"), t("d", 10, 11, "50", "-1", "Bo"), t("e", 10, 12, "200", "12", "Bo")]\n'
            '    kept = pipeline.clean(trips)\n    assert [x["trip_id"] for x in kept] == ["a", "e"]\n'
            '    s = pipeline.summary(kept)\n'
            '    assert s["Ann"]["trips"] == 1 and s["Bo"]["km"] == D("200") and s["Bo"]["median"] == D("6")\n'
            '    assert pipeline.ranking(s, min_trips=1) == ["Bo", "Ann"] and pipeline.ranking(s) == []\n')
    return prompt, dict(answers=answers, files={'data/trips.csv': data}, test=test)


SEQUENTIAL = ('L5',)
ALL_NAMES = NAMES + SEQUENTIAL


def tasks():
    built = {name: build(name) for name in NAMES}
    built['L5'] = pipeline_order()
    return {name: (prompt, expected) for name, (prompt, expected) in built.items()}


HELPER = 'from datetime import datetime, timedelta\n\nimport pytest\n\n\n'


def numbers(text):
    return {Decimal(found.replace(',', '')) for found in re.findall(r'\d[\d,]*(?:\.\d+)?', text or '')}


def check(name, workspace, reply, expected, pytest_counts):
    tests = pytest_counts(workspace)
    checks = {'tests pass': tests['ok']}
    with tempfile.TemporaryDirectory(prefix='cli-mode-hidden-') as folder:
        hidden = Path(folder) / ('test_accept_' + name.lower() + '.py')
        hidden.write_text(HELPER + expected['test'], encoding='utf-8')
        run = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', str(hidden)],
                             cwd=workspace, capture_output=True, text=True, timeout=300)
    tail = (run.stdout or run.stderr).strip().splitlines()[-1:] or ['']
    passed = int((re.search(r'(\d+) passed', tail[0]) or [0, 0])[1])
    failed = int((re.search(r'(\d+) (?:failed|error)', tail[0]) or [0, 0])[1])
    checks['hidden'] = '%d/%d' % (passed, passed + failed)
    if failed or run.returncode:
        checks['hiddenOutput'] = '\n'.join((run.stdout + run.stderr).strip().splitlines()[-25:])
    saved = [path for path, data in expected['files'].items() if (workspace / path).exists()
             and (workspace / path).read_text(encoding='utf-8').strip() == data.strip()]
    checks['data saved exactly'] = '%d/%d' % (len(saved), len(expected['files']))
    found = numbers(reply)
    hits = {key: (Decimal(value) in found) if re.fullmatch(r'[\d.]+', value) else value in (reply or '')
            for key, value in expected['answers'].items()}
    checks['answers'] = '%d/%d' % (sum(hits.values()), len(hits))
    checks['missed'] = [key + '=' + expected['answers'][key] for key, hit in hits.items() if not hit][:10]
    return tests, checks
