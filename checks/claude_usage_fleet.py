"""The `fleet` task set for checks/claude_usage_live.py: a vehicle-telemetry package with planted bugs and five
prompts: T1 (about 1k tokens of work), T5 (about 5k), and three 15k workloads of different kinds: F15 builds a
feature, D15 pastes about 15k tokens of data with exact answers computed in advance, R15 repairs reported bugs and
refactors. Hidden acceptance tests, never shown to the agents, check each task's work afterwards."""
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import random
import re
import subprocess
import sys
import tempfile

NO_QUESTIONS = ' Work without asking me questions: make reasonable assumptions and say what they were. Do not commit.'

# Planted bugs: trip_distance ignores time order, average_speed counts stops, fuel_per_100km is upside down,
# speeding counts the limit itself, idle_minutes goes negative past midnight, parse_ts takes one format only,
# parse_file fails on blank lines, and vehicle names keep their spaces. alerts.since parses timestamps again.
SEED = {
    'README.md': '# fleetlog\n\nReads vehicle telemetry (CSV) and reports on it.\n',
    'conftest.py': '',
    '.gitignore': '__pycache__/\n.pytest_cache/\nAgent_Working_Folder/\n',
    'fleetlog/__init__.py': '',
    'fleetlog/records.py': (
        'from dataclasses import dataclass\nfrom datetime import datetime\n\n\n@dataclass\nclass Reading:\n'
        '    vehicle: str\n    ts: datetime\n    speed_kmh: float\n    fuel_l: float\n    odometer_km: float\n'
        '    lat: float\n    lon: float\n'),
    'fleetlog/parse.py': (
        'from datetime import datetime\n\nfrom fleetlog.records import Reading\n\n\ndef parse_ts(text):\n'
        '    return datetime.strptime(text, \'%Y-%m-%d %H:%M\')\n\n\ndef parse_line(line):\n'
        '    parts = line.split(\',\')\n'
        '    return Reading(parts[0], parse_ts(parts[1]), float(parts[2]), float(parts[3]), float(parts[4]),\n'
        '                   float(parts[5]), float(parts[6]))\n\n\ndef parse_file(path):\n'
        '    with open(path, encoding=\'utf-8\') as handle:\n        lines = handle.read().splitlines()\n'
        '    return [parse_line(line) for line in lines[1:]]  # The first line is the header.\n'),
    'fleetlog/stats.py': (
        'def trip_distance(readings):\n    return readings[-1].odometer_km - readings[0].odometer_km\n\n\n'
        'def average_speed(readings):\n    speeds = [r.speed_kmh for r in readings]\n'
        '    return sum(speeds) / len(speeds)\n\n\n'
        'def fuel_per_100km(readings):\n    used = readings[0].fuel_l - readings[-1].fuel_l\n'
        '    return trip_distance(readings) / used * 100\n'),
    'fleetlog/alerts.py': (
        'from datetime import datetime\n\n\ndef speeding(readings, limit):\n'
        '    return [r for r in readings if r.speed_kmh >= limit]\n\n\ndef minute_of_day(ts):\n'
        '    return ts.hour * 60 + ts.minute\n\n\ndef idle_minutes(readings):\n    total = 0\n'
        '    for a, b in zip(readings, readings[1:]):\n        if a.speed_kmh == 0:\n'
        '            total += minute_of_day(b.ts) - minute_of_day(a.ts)\n    return total\n\n\n'
        'def since(readings, text):\n    start = datetime.strptime(text, \'%Y-%m-%d %H:%M\')\n'
        '    return [r for r in readings if r.ts >= start]\n'),
    'fleetlog/report.py': (
        'from fleetlog import stats\n\n\ndef summary(readings):\n    by_vehicle = {}\n    for r in readings:\n'
        '        by_vehicle.setdefault(r.vehicle, []).append(r)\n'
        '    return {vehicle: {\'distance_km\': round(stats.trip_distance(rs), 1),\n'
        '                      \'avg_speed\': round(stats.average_speed(rs), 1)} for vehicle, rs in by_vehicle.items()}\n'),
    'tests/test_parse.py': (
        'from datetime import datetime\n\nfrom fleetlog.parse import parse_line\n\n\ndef test_a_plain_line():\n'
        '    r = parse_line(\'V1,2026-09-01 08:00,42,51.5,1200.5,40.1,-3.2\')\n'
        '    assert (r.vehicle, r.ts, r.speed_kmh, r.odometer_km) == (\'V1\', datetime(2026, 9, 1, 8, 0), 42.0, 1200.5)\n'),
    'tests/test_stats.py': (
        'from datetime import datetime\n\nfrom fleetlog import stats\nfrom fleetlog.records import Reading\n\n\n'
        'def r(minute, speed, odo):\n    return Reading(\'V1\', datetime(2026, 9, 1, 8, minute), speed, 50.0, odo, 0.0, 0.0)\n\n\n'
        'def test_distance_uses_time_order():\n'
        '    assert stats.trip_distance([r(10, 50, 120.0), r(0, 0, 100.0), r(5, 40, 110.0)]) == 20.0\n\n\n'
        'def test_average_speed_counts_moving_readings_only():\n'
        '    assert stats.average_speed([r(0, 0, 100), r(1, 60, 101), r(2, 40, 102)]) == 50.0\n'),
}

HELPER = (
    'from datetime import datetime, timedelta\n\nimport pytest\n\nfrom fleetlog.records import Reading\n\n\n'
    'def r(vehicle, minute, speed, odo, fuel=50.0, lat=0.0, lon=0.0):\n'
    '    return Reading(vehicle, datetime(2026, 9, 1, 8, 0) + timedelta(minutes=minute), speed, fuel, odo, lat, lon)\n\n\n')

ACCEPT = {
    'T1': (
        'def test_max_speed():\n    from fleetlog import stats\n    assert stats.max_speed([]) == 0.0\n'
        '    assert stats.max_speed([r("V", 0, 10, 1), r("V", 1, 95.5, 2), r("V", 2, 3, 3)]) == 95.5\n\n\n'
        'def test_the_fixes_hold():\n    from fleetlog import stats\n'
        '    assert stats.trip_distance([r("V", 9, 1, 30.0), r("V", 0, 0, 10.0)]) == 20.0\n'
        '    assert stats.average_speed([r("V", 0, 0, 1), r("V", 1, 30, 2)]) == 30.0\n'),
    'T5': (
        'def test_round_trip(tmp_path):\n    from fleetlog import jsonl\n'
        '    rows = [r("V1", 0, 10.5, 100.0), r("V2", 3, 0.0, 7.25)]\n    path = tmp_path / "x.jsonl"\n'
        '    jsonl.export_readings(rows, path)\n    assert jsonl.import_readings(path) == rows\n\n\n'
        'def test_a_bad_line_is_named(tmp_path):\n    from fleetlog import jsonl\n    path = tmp_path / "x.jsonl"\n'
        '    jsonl.export_readings([r("V1", 0, 1, 1), r("V1", 1, 2, 2), r("V1", 2, 3, 3)], path)\n'
        '    lines = path.read_text(encoding="utf-8").splitlines()\n    lines[2] = "{not json"\n'
        '    path.write_text("\\n".join(lines) + "\\n", encoding="utf-8")\n'
        '    with pytest.raises(ValueError, match="3"):\n        jsonl.import_readings(path)\n\n\n'
        'def test_vehicles_summary():\n    from fleetlog import report\n'
        '    out = report.vehicles([r("V1", 5, 20, 110.0), r("V1", 0, 0, 100.0), r("V2", 1, 0, 50.0)])\n'
        '    assert out["V1"]["readings"] == 2 and out["V1"]["distance_km"] == 10.0\n'
        '    assert out["V1"]["first_ts"] < out["V1"]["last_ts"] and out["V2"]["readings"] == 1\n'),
    'F15': (
        'def test_segment_trips_by_gap():\n    from fleetlog.trips import segment_trips\n'
        '    rows = [r("A", 0, 30, 100.0, 40.0), r("A", 1, 60, 101.0, 39.9), r("A", 2, 40, 102.0, 39.8),\n'
        '            r("A", 30, 50, 102.5, 39.7), r("A", 31, 70, 104.0, 39.5), r("B", 0, 20, 5.0), r("B", 1, 20, 5.5)]\n'
        '    trips = segment_trips(rows, gap_minutes=10)\n'
        '    a = sorted((t for t in trips if t["vehicle"] == "A"), key=lambda t: t["start"])\n'
        '    assert len(a) == 2 and len([t for t in trips if t["vehicle"] == "B"]) == 1\n'
        '    assert a[0]["distance_km"] == pytest.approx(2.0) and a[1]["distance_km"] == pytest.approx(1.5)\n'
        '    assert a[0]["max_speed"] == 60 and a[0]["fuel_used_l"] == pytest.approx(0.2) and a[0]["duration_min"] == 2\n\n\n'
        'def test_point_in_polygon():\n    from fleetlog.geofence import point_in_polygon\n'
        '    square = [(0, 0), (0, 10), (10, 10), (10, 0)]\n'
        '    assert point_in_polygon(5, 5, square) and not point_in_polygon(15, 5, square)\n\n\n'
        'def test_fence_events():\n    from fleetlog.geofence import fence_events\n'
        '    fences = {"yard": [(0, 0), (0, 10), (10, 10), (10, 0)]}\n'
        '    rows = [r("A", 0, 10, 1, lat=-5, lon=5), r("A", 1, 10, 2, lat=5, lon=5), r("A", 2, 10, 3, lat=20, lon=5)]\n'
        '    assert [(e["fence"], e["kind"]) for e in fence_events(rows, fences)] == [("yard", "enter"), ("yard", "exit")]\n\n\n'
        'def test_geojson():\n    import json\n    from fleetlog.export import trips_to_geojson\n'
        '    from fleetlog.trips import segment_trips\n'
        '    rows = [r("A", 0, 30, 100.0, lat=1, lon=2), r("A", 1, 60, 101.0, lat=1.1, lon=2.1)]\n'
        '    data = trips_to_geojson(segment_trips(rows), rows)\n    data = json.loads(data) if isinstance(data, str) else data\n'
        '    assert data["type"] == "FeatureCollection" and data["features"][0]["geometry"]["type"] == "LineString"\n\n\n'
        'def test_command_line():\n    import subprocess, sys\n'
        '    assert subprocess.run([sys.executable, "-m", "fleetlog", "trips", "--help"], capture_output=True).returncode == 0\n'),
    'R15': (
        'from fleetlog import alerts, stats\nfrom fleetlog.parse import parse_file, parse_line\n\n\n'
        'def test_1_iso_timestamps():\n    assert parse_line("V1,2026-09-01T08:05:30,1,1,1,1,1").ts == datetime(2026, 9, 1, 8, 5, 30)\n'
        '    assert parse_line("V1,2026-09-01 08:05,1,1,1,1,1").ts == datetime(2026, 9, 1, 8, 5)\n\n\n'
        'def test_2_blank_lines(tmp_path):\n    path = tmp_path / "f.csv"\n'
        '    path.write_text("vehicle,ts,speed,fuel,odo,lat,lon\\nV1,2026-09-01 08:00,1,1,1,1,1\\n\\n'
        'V1,2026-09-01 08:01,1,1,2,1,1\\n\\n", encoding="utf-8")\n    assert len(parse_file(path)) == 2\n\n\n'
        'def test_3_names_trimmed():\n    assert parse_line(" V1 ,2026-09-01 08:05,1,1,1,1,1").vehicle == "V1"\n\n\n'
        'def test_4_distance_in_time_order():\n    assert stats.trip_distance([r("V", 9, 1, 30.0), r("V", 0, 0, 10.0)]) == 20.0\n\n\n'
        'def test_5_fuel_per_100km():\n    rows = [r("V", 0, 50, 100.0, fuel=40.0), r("V", 60, 50, 200.0, fuel=32.0)]\n'
        '    assert stats.fuel_per_100km(rows) == pytest.approx(8.0)\n\n\n'
        'def test_6_at_the_limit_is_not_speeding():\n'
        '    assert [x.speed_kmh for x in alerts.speeding([r("V", 0, 90, 1), r("V", 1, 91, 2)], 90)] == [91]\n\n\n'
        'def test_7_idle_past_midnight():\n    a, b = r("V", 0, 0, 1), r("V", 0, 5, 1)\n'
        '    a.ts, b.ts = datetime(2026, 9, 1, 23, 50), datetime(2026, 9, 2, 0, 10)\n'
        '    assert alerts.idle_minutes([a, b]) == 20\n\n\n'
        'def test_8_average_of_moving_readings():\n'
        '    assert stats.average_speed([r("V", 0, 0, 1), r("V", 1, 30, 2)]) == 30.0\n'),
}

NAMES = ('T1', 'T5', 'F15', 'D15', 'R15')
VEHICLES = ['V%02d' % n for n in range(1, 7)]
START = datetime(2026, 9, 1, 6, 0)
HEADER = 'vehicle,ts,speed_kmh,fuel_l,odometer_km,lat,lon'


def fleet_data(chars):
    """Six vans, one reading each a minute in time order, about `chars` characters of CSV; and the exact answers:
    each vehicle's distance (last minus first odometer), speeding episodes (runs of 3+ readings above 90 km/h) and
    idle minutes (a minute for every reading at 0 km/h that has a later reading), and the fleet's total distance."""
    rng = random.Random(1515)
    vans = {v: dict(speed=0, odo=Decimal(rng.randint(10000, 900000)) / 10, fuel=Decimal(rng.randint(400, 700)) / 10,
                    lat=40 + rng.random(), lon=-3 - rng.random()) for v in VEHICLES}
    lines, speeds, first = [HEADER], {v: [] for v in VEHICLES}, {}
    minute, size = 0, len(HEADER)
    while size < chars:
        for v in VEHICLES:
            van = vans[v]
            if rng.random() < .08:
                van['speed'] = 0
            else:  # Never exactly 90, the limit, so "above 90" is never in doubt.
                van['speed'] = max(0, min(130, van['speed'] + rng.choice([-20, -10, -5, 0, 5, 10, 15, 25])))
                van['speed'] += 5 if van['speed'] == 90 else 0
            van['odo'] += (Decimal(van['speed']) / 60).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)
            van['fuel'] -= Decimal('0.1') if van['speed'] else 0
            van['lat'] += van['speed'] / 60 / 111 * rng.choice([-1, 1]) * .7
            van['lon'] += van['speed'] / 60 / 85 * rng.choice([-1, 1]) * .7
            first.setdefault(v, van['odo'])
            speeds[v].append(van['speed'])
            line = ','.join((v, (START + timedelta(minutes=minute)).strftime('%Y-%m-%d %H:%M'), str(van['speed']),
                             str(van['fuel']), str(van['odo']), '%.5f' % van['lat'], '%.5f' % van['lon']))
            lines.append(line)
            size += len(line) + 1
        minute += 1
    answers = {}
    for v in VEHICLES:
        episodes = run = 0
        for speed in speeds[v]:
            run = run + 1 if speed > 90 else 0
            episodes += run == 3
        answers[v + ' distance'] = str(vans[v]['odo'] - first[v])
        answers[v + ' episodes'] = str(episodes)
        answers[v + ' idle'] = str(sum(1 for speed in speeds[v][:-1] if speed == 0))
    answers['total distance'] = str(sum((vans[v]['odo'] - first[v] for v in VEHICLES), Decimal(0)))
    return '\n'.join(lines) + '\n', answers


def tasks():
    data, answers = fleet_data(58000)
    answers['csv'] = data
    return {
        'T1': ('tests/test_stats.py fails in this project (the tests are right; the code has bugs). Make every test '
               'pass, then add stats.max_speed(readings), returning the highest speed in km/h (0.0 when there are no '
               'readings), with a test for it.' + NO_QUESTIONS, {}),
        'T5': ('Add JSON Lines support to fleetlog. Put export_readings(readings, path) and import_readings(path) in '
               'fleetlog/jsonl.py: one reading per line, timestamps in ISO 8601 (YYYY-MM-DDTHH:MM:SS). Import checks '
               'every line (invalid JSON, a missing field, a value that is not a number, a negative speed or fuel) and '
               'raises ValueError naming the line number, counting from 1; exporting then importing gives equal '
               'readings back. Add report.vehicles(readings), returning for each vehicle a dict with readings (a '
               'count), first_ts, last_ts and distance_km (by odometer, in time order, rounded to 0.1). Make every '
               'existing test pass (the tests are right; the code has bugs), add at least 10 tests, and add a "JSON '
               'Lines" section to README.md.' + NO_QUESTIONS, {}),
        'F15': ('Build trip analysis for fleetlog, end to end.\n'
                '1. fleetlog/trips.py: segment_trips(readings, gap_minutes=10) splits each vehicle\'s readings, sorted '
                'by time, into trips wherever two readings are more than gap_minutes apart. Each trip is a dict with '
                'vehicle, start and end (datetimes), distance_km (last minus first odometer), duration_min (end minus '
                'start, in minutes), avg_speed (of the readings above 0 km/h), max_speed and fuel_used_l (first minus '
                'last fuel).\n'
                '2. fleetlog/geofence.py: point_in_polygon(lat, lon, polygon), polygon being a list of (lat, lon) '
                'corners (ray casting), and fence_events(readings, fences), fences being {name: polygon}: a list of '
                'dicts with vehicle, fence, kind ("enter" or "exit") and ts, in time order.\n'
                '3. fleetlog/export.py: trips_to_geojson(trips, readings), a GeoJSON FeatureCollection with one '
                'LineString per trip and the trip\'s fields as properties, and trips_to_csv(trips, path).\n'
                '4. A command line: python -m fleetlog trips --input FILE [--gap MINUTES] [--format csv|geojson] '
                '[--out FILE], and python -m fleetlog fences --input FILE --fences FILE.json.\n'
                'Make every existing test pass (the tests are right; the code has bugs), write tests for each part '
                'with edge cases (a single reading, a vehicle that never moves, a point on a polygon\'s edge), and '
                'document the command line in README.md.' + NO_QUESTIONS, {}),
        'D15': ('Below is a morning of telemetry from our six vans (CSV, one reading per van per minute). I need it in '
                'the project and audited.\n\nWhat to do:\n- Save it exactly as given to data/fleet.csv.\n'
                '- Add fleetlog/audit.py that loads that file and computes, for each vehicle: distance_km (last '
                'minus first odometer, in time order), speeding episodes (runs of 3 or more consecutive readings '
                'above 90 km/h; a longer run is still one episode), idle minutes (for every reading at 0 km/h, the '
                'minutes until that vehicle\'s next reading), average moving speed (readings above 0 km/h) and fuel '
                'per 100 km ((first minus last fuel) / distance x 100).\n'
                '- Write reports/fleet.md with a table of those numbers per vehicle and the fleet\'s total distance.\n'
                '- Make every existing test pass (the tests are right; the code has bugs) and add tests for the audit '
                'rules.\n- Tell me each vehicle\'s distance, speeding episodes and idle minutes, and the fleet\'s total '
                'distance.\n\nTelemetry:\n' + data + NO_QUESTIONS.strip(), answers),
        'R15': ('Our users report these problems with fleetlog; find the cause of each.\n'
                '1. Timestamps in ISO 8601 form with a T and seconds (2026-09-01T08:05:30) are rejected; that form and '
                '"YYYY-MM-DD HH:MM" must both parse.\n2. parse_file fails on files with blank lines; blank lines '
                'should be skipped.\n3. Vehicle names read with spaces around them (" V1 ") keep the spaces; they '
                'should be trimmed.\n4. trip_distance is wrong when readings are not in time order.\n'
                '5. fuel_per_100km gives impossible figures, such as 1250 l/100 km.\n6. speeding() flags a van '
                'driving exactly at the limit; only speeds above it count.\n7. idle_minutes goes wrong when idling '
                'runs past midnight.\n8. average_speed should count moving readings only.\n'
                'Fix every one with a regression test for each. Then refactor: timestamp parsing lives in one place '
                'only, and stats and alerts share one helper that puts readings in time order. All tests must pass.' +
                NO_QUESTIONS, {}),
    }


# ---------------------------------------------------------------- the `fleet5` set: five 5k tasks of different kinds

NAMES5 = ('S5', 'H5', 'V5', 'C5', 'Q5')

ACCEPT.update({
    'S5': (
        'from datetime import date\n\n\n'
        'SERVICES = ("vehicle,date,odometer_km,kind\\nV1,2026-01-10,10000,oil\\nV1,2026-03-01,20000,oil\\n"\n'
        '            "V1,2026-02-01,15000,tyres\\nV2,2025-01-01,5000,oil\\nV3,2026-05-01,1000,oil\\n")\n\n\n'
        'def services(tmp_path):\n    from fleetlog import maintenance\n    path = tmp_path / "s.csv"\n'
        '    path.write_text(SERVICES, encoding="utf-8")\n    return maintenance.load_services(path)\n\n\n'
        'def test_next_due_uses_the_latest_service(tmp_path):\n    from fleetlog import maintenance\n'
        '    s = services(tmp_path)\n    due = maintenance.next_due(s, "V1", "oil")\n'
        '    assert due["due_km"] == 35000 and due["due_date"] == date(2027, 3, 1)\n'
        '    assert maintenance.next_due(s, "V1", "tyres")["due_date"] == date(2029, 1, 31)\n'
        '    with pytest.raises(KeyError):\n        maintenance.next_due(s, "V1", "brakes")\n\n\n'
        'def test_overdue_by_distance_or_date(tmp_path):\n    from fleetlog import maintenance\n'
        '    readings = [r("V1", 0, 10, 30000.0), r("V1", 5, 10, 35000.0), r("V2", 0, 10, 6000.0), r("V3", 0, 10, 1200.0)]\n'
        '    assert maintenance.overdue(services(tmp_path), readings, date(2026, 6, 1)) == [("V1", "oil"), ("V2", "oil")]\n'),
    'V5': (
        'def codes(rows):\n    from fleetlog.validate import check\n    return [(i["vehicle"], i["code"]) for i in check(rows)]\n\n\n'
        'def test_odometer_back():\n    assert codes([r("V", 0, 10, 100.0), r("V", 1, 10, 99.0)]) == [("V", "ODO_BACK")]\n\n\n'
        'def test_fuel_up_only_when_moving():\n'
        '    assert codes([r("V", 0, 0, 100.0, fuel=40.0), r("V", 1, 30, 100.5, fuel=45.0)]) == [("V", "FUEL_UP")]\n'
        '    assert codes([r("V", 0, 0, 100.0, fuel=40.0), r("V", 1, 0, 100.0, fuel=45.0)]) == []\n\n\n'
        'def test_speed_jump_within_a_minute():\n    assert codes([r("V", 0, 10, 100.0), r("V", 1, 70, 101.0)]) == [("V", "SPEED_JUMP")]\n'
        '    assert codes([r("V", 0, 10, 100.0), r("V", 2, 70, 101.0)]) == []\n\n\n'
        'def test_duplicate_timestamp():\n    assert codes([r("V", 0, 10, 100.0), r("V", 0, 10, 100.0)]) == [("V", "DUP_TS")]\n\n\n'
        'def test_gps_jump():\n    assert codes([r("V", 0, 10, 100.0), r("V", 1, 10, 100.2, lat=0.1)]) == [("V", "GPS_JUMP")]\n'
        '    assert codes([r("V", 0, 10, 100.0), r("V", 1, 10, 100.2, lat=0.01)]) == []\n\n\n'
        'def test_negative_values_and_order():\n'
        '    assert codes([r("B", 1, 10, 100.0), r("A", 0, -5, 100.0)]) == [("A", "NEG")]\n\n\n'
        'def test_command_line(tmp_path):\n    import subprocess, sys\n    bad, good = tmp_path / "bad.csv", tmp_path / "good.csv"\n'
        '    head = "vehicle,ts,speed_kmh,fuel_l,odometer_km,lat,lon\\n"\n'
        '    bad.write_text(head + "V1,2026-09-01 08:00,10,50,100,0,0\\nV1,2026-09-01 08:01,10,50,90,0,0\\n", encoding="utf-8")\n'
        '    good.write_text(head + "V1,2026-09-01 08:00,10,50,100,0,0\\nV1,2026-09-01 08:01,10,50,100.2,0,0\\n", encoding="utf-8")\n'
        '    run = lambda p: subprocess.run([sys.executable, "-m", "fleetlog", "validate", "--input", str(p)], capture_output=True, text=True)\n'
        '    failed = run(bad)\n    assert failed.returncode == 1 and "ODO_BACK" in failed.stdout\n'
        '    assert run(good).returncode == 0\n'),
    'C5': (
        'ROWS = [r("B", 5, 20, 110.0), r("A", 1, 0, 50.0), r("B", 0, 0, 100.0), r("A", 0, 30, 40.0)]\n\n\n'
        'def test_the_class():\n    from fleetlog.fleet import Fleet\n    fleet = Fleet(ROWS)\n'
        '    assert fleet.vehicles() == ["A", "B"]\n    assert [x.odometer_km for x in fleet.readings("B")] == [100.0, 110.0]\n'
        '    assert fleet.distance("B") == 10.0 and fleet.average_speed("B") == 20.0\n\n\n'
        'def test_from_csv(tmp_path):\n    from fleetlog.fleet import Fleet\n    path = tmp_path / "f.csv"\n'
        '    path.write_text("vehicle,ts,speed_kmh,fuel_l,odometer_km,lat,lon\\nV1,2026-09-01 08:00,10,50,100,0,0\\n"\n'
        '                    "V1,2026-09-01 08:05,20,49,105,0,0\\n", encoding="utf-8")\n'
        '    assert Fleet.from_csv(path).distance("V1") == 5.0\n\n\n'
        'def test_the_old_functions_still_work():\n    from fleetlog import report, stats\n    from fleetlog.fleet import Fleet\n'
        '    assert stats.trip_distance([r("V", 9, 1, 30.0), r("V", 0, 0, 10.0)]) == 20.0\n'
        '    summary = report.summary([r("V", 0, 0, 10.0), r("V", 1, 30, 12.0)])\n'
        '    assert summary["V"]["distance_km"] == 2.0 and summary["V"]["avg_speed"] == 30.0\n'
        '    assert Fleet(ROWS).summary() == report.summary(ROWS)\n'),
    'Q5': (
        'def test_idle_past_midnight():\n    from fleetlog import alerts\n    a, b = r("V", 0, 0, 1), r("V", 0, 5, 1)\n'
        '    a.ts, b.ts = datetime(2026, 9, 1, 23, 50), datetime(2026, 9, 2, 0, 10)\n'
        '    assert alerts.idle_minutes([a, b]) == 20\n\n\n'
        'def test_a_blank_line_in_the_middle(tmp_path):\n    from fleetlog.parse import parse_file\n    path = tmp_path / "f.csv"\n'
        '    path.write_text("vehicle,ts,speed,fuel,odo,lat,lon\\nV1,2026-09-01 08:00,1,1,1,1,1\\n\\n'
        'V1,2026-09-01 08:01,1,1,2,1,1\\n", encoding="utf-8")\n    assert len(parse_file(path)) == 2\n'),
})


def shift_data(chars):
    """Drivers' shifts over two weeks (Monday 2026-09-07 to Sunday 2026-09-20), in start order, about `chars`
    characters of CSV; and the exact answers under the H5 rules."""
    rng = random.Random(505)
    rows, size, count = [], 0, 0
    while size < chars:
        count += 1
        driver, prev_end = 'D%02d' % count, None
        for day in range(14):
            if rng.random() < .15:
                continue
            start = datetime(2026, 9, 7) + timedelta(days=day, minutes=15 * rng.randint(16, 88))  # 04:00 to 22:00
            if prev_end and start < prev_end + timedelta(hours=8):
                start = prev_end + timedelta(hours=8, minutes=15 * rng.randint(0, 16))
            if start.date() > (datetime(2026, 9, 7) + timedelta(days=day)).date() or start >= datetime(2026, 9, 21):
                continue
            end = start + timedelta(minutes=15 * rng.randint(24, 50))  # 6 to 12.5 hours
            rows.append((start, driver, 'V%02d' % rng.randint(1, 12), end, 15 * rng.randint(0, 4)))
            size += 52
            prev_end = end
    rows.sort()
    lines = ['driver,vehicle,start,end,break_min'] + [','.join((driver, van, start.strftime('%Y-%m-%d %H:%M'),
                                                                 end.strftime('%Y-%m-%d %H:%M'), str(pause)))
                                                        for start, driver, van, end, pause in rows]
    paid = lambda start, end, pause: Decimal(int((end - start).total_seconds() // 60) - pause) / 60  # noqa: E731
    weeks, long_shifts, rest, last = {}, 0, 0, {}
    for start, driver, van, end, pause in rows:
        hours = paid(start, end, pause)
        key = (driver, start.isocalendar().week)
        weeks[key] = weeks.get(key, Decimal(0)) + hours
        long_shifts += hours > 10
        if driver in last and start - last[driver] < timedelta(hours=11):
            rest += 1
        last[driver] = end
    overtime = {}
    for (driver, _), hours in weeks.items():
        overtime[driver] = overtime.get(driver, Decimal(0)) + max(Decimal(0), hours - 40)
    top = max(overtime, key=overtime.get)
    assert sorted(overtime.values())[-2] < overtime[top], 'the most overtime must be one driver'
    total = sum(weeks.values(), Decimal(0))
    return '\n'.join(lines) + '\n', {
        'total paid hours': str(total), 'total overtime hours': str(sum(overtime.values(), Decimal(0))),
        'long shifts': str(long_shifts), 'rest violations': str(rest), 'most overtime driver': top,
        'their overtime hours': str(overtime[top])}


def tasks5():
    data, answers = shift_data(17500)
    answers.update(csv=data, csvPath='data/shifts.csv')
    return {
        'S5': ('Add service scheduling to fleetlog in fleetlog/maintenance.py. load_services(path) reads a CSV with the '
               'columns vehicle,date (YYYY-MM-DD),odometer_km,kind, where kind is oil, tyres or brakes. '
               'next_due(services, vehicle, kind) uses that vehicle\'s latest service of that kind (by date) and '
               'returns a dict with due_km (its odometer plus the interval) and due_date (a datetime.date, its date '
               'plus the interval); the intervals are oil 15000 km or 365 days, tyres 40000 km or 1095 days, brakes '
               '30000 km or 730 days. A vehicle never serviced for that kind raises KeyError. overdue(services, '
               'readings, today) returns a sorted list of (vehicle, kind) tuples whose vehicle\'s latest reading (by '
               'time) has reached due_km or whose due_date is on or before today. Make every existing test pass (the '
               'tests are right; the code has bugs), add at least 8 tests with edge cases, and add a "Service '
               'schedule" section to README.md.' + NO_QUESTIONS, {}),
        'H5': ('Below is two weeks of driver shifts (CSV). Payroll needs the numbers checked and a tool in the project.'
               '\n\nRules:\n- A shift\'s paid hours are its end minus its start, minus break_min.\n- A shift belongs to '
               'the day and the week it starts in; weeks run Monday to Sunday.\n- Overtime is a driver\'s paid hours '
               'above 40 in a week; a driver\'s overtime is the sum over both weeks.\n- A long shift is one of more '
               'than 10 paid hours.\n- A rest violation is a shift starting less than 11 hours after the end of the '
               'same driver\'s previous shift.\n\nWhat to do:\n- Save the shifts exactly as given to data/shifts.csv.\n'
               '- Add fleetlog/shifts.py that loads that file and computes, per driver, paid hours, overtime, long '
               'shifts and rest violations.\n- Make every existing test pass (the tests are right; the code has bugs) '
               'and add tests for each rule.\n- Tell me the total paid hours of all drivers, the total overtime hours, '
               'the number of long shifts, the number of rest violations, and the driver with the most overtime with '
               'their overtime hours.\n\nShifts:\n' + data + NO_QUESTIONS.strip(), answers),
        'V5': ('Add data validation to fleetlog: fleetlog/validate.py with check(readings), returning a list of issues, '
               'each a dict with vehicle, ts and code, sorted by vehicle and then time. Take each vehicle\'s readings '
               'in time order and compare every reading with the one before it:\n'
               '- ODO_BACK: the odometer is lower than before.\n- FUEL_UP: fuel rose by more than 0.5 l, unless the '
               'speed is 0 at both readings (a refuel).\n- SPEED_JUMP: the speed changed by more than 50 km/h and the '
               'readings are at most 1 minute apart.\n- DUP_TS: the same timestamp as the reading before; flag the '
               'later one and check nothing else on it.\n- GPS_JUMP: it moved more than 5 km (haversine, earth radius '
               '6371 km) and the readings are at most 1 minute apart.\n- NEG: a negative speed, fuel or odometer '
               '(checked on every reading, first ones included).\nAdd a command, python -m fleetlog validate --input '
               'FILE, that prints one line per issue and exits with 1 when there are issues and 0 when there are '
               'none. Make every existing test pass (the tests are right; the code has bugs) and add tests for every '
               'code, including the cases that must not be flagged.' + NO_QUESTIONS, {}),
        'C5': ('Give fleetlog an object API. Add a Fleet class in fleetlog/fleet.py: Fleet(readings) and '
               'Fleet.from_csv(path); vehicles() (the names, sorted), readings(vehicle) (in time order), '
               'distance(vehicle), average_speed(vehicle), fuel_per_100km(vehicle), speeding(vehicle, limit), '
               'idle_minutes(vehicle) and summary() (the same result as report.summary). The existing module '
               'functions keep their names and signatures but become thin wrappers over the same shared code, so no '
               'rule is written twice. Make every existing test pass (the tests are right; the code has bugs), add '
               'tests for the class, and describe the API in README.md.' + NO_QUESTIONS, {}),
        'Q5': ('Nobody has reviewed fleetlog/parse.py and fleetlog/alerts.py. Write a thorough test suite for both '
               '(boundaries, empty input, ordering, formats, whitespace, days that change at midnight), fix every bug '
               'your tests reveal, and list each bug you found with its fix. Make every existing test pass too (the '
               'tests are right; the code has bugs).' + NO_QUESTIONS, {}),
    }


# ---------------------------------------------------------------- the `hot` set: three 1k prompts in one session

NAMES_HOT = ('K1', 'K2', 'K3')

# The project's own tests pass from the start, so the first prompt carries no bug fixing of its own.
HOT_SEED = dict(SEED, **{'fleetlog/stats.py': (
    'def in_time_order(readings):\n    return sorted(readings, key=lambda r: r.ts)\n\n\n'
    'def trip_distance(readings):\n    readings = in_time_order(readings)\n'
    '    return readings[-1].odometer_km - readings[0].odometer_km\n\n\n'
    'def average_speed(readings):\n    speeds = [r.speed_kmh for r in readings if r.speed_kmh > 0]\n'
    '    return sum(speeds) / len(speeds) if speeds else 0.0\n\n\n'
    'def fuel_per_100km(readings):\n    readings = in_time_order(readings)\n'
    '    used = readings[0].fuel_l - readings[-1].fuel_l\n    return used / trip_distance(readings) * 100\n')})

ACCEPT.update({
    'K1': (
        'def test_total_fuel_used():\n    from fleetlog import stats\n    assert stats.total_fuel_used([]) == 0.0\n'
        '    assert stats.total_fuel_used([r("V", 0, 10, 1, fuel=40.0)]) == 0.0\n'
        '    assert stats.total_fuel_used([r("V", 5, 10, 2, fuel=40.0), r("V", 0, 10, 1, fuel=45.5)]) == '
        'pytest.approx(5.5)\n'),
    'K2': (
        'def at(minute):\n    return datetime(2026, 9, 1, 8, 0) + timedelta(minutes=minute)\n\n\n'
        'ROWS = [r("V", 0, 10, 1), r("V", 1, 0, 1), r("V", 20, 0, 1), r("V", 21, 30, 2), r("V", 22, 0, 2),\n'
        '        r("V", 27, 40, 3), r("V", 30, 0, 3), r("V", 50, 0, 3)]\n\n\n'
        'def test_long_stops():\n    from fleetlog import alerts\n'
        '    want = [(at(1), at(21)), (at(30), at(50))]\n'
        '    assert [tuple(pair) for pair in alerts.long_stops(ROWS, minutes=15)] == want\n'
        '    assert [tuple(pair) for pair in alerts.long_stops(ROWS)] == want\n'
        '    assert [tuple(pair) for pair in alerts.long_stops(ROWS, minutes=5)][1] == (at(22), at(27))\n'),
    'K3': (
        'def test_daily():\n    from datetime import date\n    from fleetlog import report\n'
        '    out = report.daily([r("A", 0, 1, 1), r("B", 5, 1, 1), r("A", 10, 1, 2), r("A", 24 * 60, 1, 3)])\n'
        '    assert (out[date(2026, 9, 1)]["vehicles"], out[date(2026, 9, 1)]["readings"]) == (2, 3)\n'
        '    assert (out[date(2026, 9, 2)]["vehicles"], out[date(2026, 9, 2)]["readings"]) == (1, 1)\n\n\n'
        'def test_the_readme_documents_all_three():\n    text = open("README.md", encoding="utf-8").read()\n'
        '    assert all(name in text for name in ("total_fuel_used", "long_stops", "daily"))\n'),
})


def tasks_hot():
    return {
        'K1': ('Add stats.total_fuel_used(readings): the fuel used over the readings, first minus last fuel_l in time '
               'order, and 0.0 when there are fewer than two readings. Add tests for it. All tests must pass.' +
               NO_QUESTIONS, {}),
        'K2': ('Add alerts.long_stops(readings, minutes=15) for one vehicle\'s readings. A stop is a run of readings at '
               '0 km/h; it lasts from its first reading to the next moving reading, or to the last reading if the '
               'vehicle is still stopped then. Return (start, end) timestamp pairs, in time order, for the stops '
               'lasting at least `minutes`. Add tests. All tests must pass.' + NO_QUESTIONS, {}),
        'K3': ('Add report.daily(readings), returning for each date (a datetime.date) a dict with vehicles (how many '
               'different vehicles reported that day) and readings (how many readings). Then add an "API" section to '
               'README.md documenting stats.total_fuel_used, alerts.long_stops and report.daily with one example '
               'each. Add tests. All tests must pass.' + NO_QUESTIONS, {}),
    }


# ---------------------------------------------------------------- the `par` set: parallel work, and its control

# X15: three independent parts, each in new files of its own, about 5k tokens of work each: AUTO should hand them
# out at once, one agent each. Y5: three parts in one module and one test file: they share files, so one task.
NAMES_PAR = ('X15', 'Y5')

ACCEPT.update({
    'X15': (
        'from datetime import date\nfrom decimal import Decimal\n\n\n'
        'def test_geo():\n    from fleetlog import geo\n'
        '    assert geo.haversine_km(51.5074, -0.1278, 48.8566, 2.3522) == pytest.approx(343.5, abs=1.0)\n'
        '    assert geo.bearing_deg(0, 0, 0, 1) == pytest.approx(90, abs=0.01)\n'
        '    assert geo.bearing_deg(0, 0, 1, 0) == pytest.approx(0, abs=0.01)\n'
        '    lat, lon = geo.destination(0, 0, 90, 111.195)\n'
        '    assert (lat, lon) == (pytest.approx(0, abs=0.01), pytest.approx(1.0, abs=0.01))\n'
        '    assert tuple(geo.bounding_box([(1, 2), (3, -1), (0, 5)])) == (0, -1, 3, 5)\n\n\n'
        'def test_units():\n    from fleetlog import units\n'
        '    assert units.km_to_miles(1.609344) == pytest.approx(1)\n'
        '    assert units.l100km_to_mpg_us(10) == pytest.approx(23.5215, abs=0.001)\n'
        '    assert units.l100km_to_mpg_uk(10) == pytest.approx(28.2481, abs=0.001)\n'
        '    assert units.kmh_to_mph(100) == pytest.approx(62.1371, abs=0.001)\n'
        '    assert units.parse_quantity("12.5 mi") == pytest.approx(20.1168, abs=0.001)\n'
        '    assert units.parse_quantity("3 km") == pytest.approx(3)\n'
        '    with pytest.raises(ValueError):\n        units.parse_quantity("5 furlongs")\n\n\n'
        'def test_costs():\n    from fleetlog import costs\n'
        '    day = lambda d, h, fuel: Reading("V", datetime(2026, 9, d, h, 0), 10, fuel, 100, 0, 0)\n'
        '    rows = [day(1, 8, 50), day(1, 9, 48), day(2, 8, 47), day(2, 9, 60), day(2, 10, 59)]\n'
        '    prices = [(date(2026, 9, 1), Decimal("1.50")), (date(2026, 9, 2), Decimal("2.00"))]\n'
        '    assert costs.fuel_cost(rows, prices)["V"] == Decimal("6.50")\n'),
    'Y5': (
        'def test_percentile_speed():\n    from fleetlog import stats\n'
        '    rows = [r("V", m, s, m) for m, s in enumerate([10, 20, 30, 40, 50])]\n'
        '    assert stats.percentile_speed(rows, 50) == 30 and stats.percentile_speed(rows, 100) == 50\n'
        '    assert stats.percentile_speed(rows, 0) == 10\n\n\n'
        'def test_moving_minutes():\n    from fleetlog import stats\n'
        '    rows = [r("V", 0, 10, 1), r("V", 5, 0, 2), r("V", 9, 20, 3), r("V", 10, 20, 4)]\n'
        '    assert stats.moving_minutes(rows) == 6\n\n\n'
        'def test_longest_run_above():\n    from fleetlog import stats\n'
        '    rows = [r("V", m, s, m) for m, s in enumerate([50, 95, 96, 40, 91, 92, 93, 20])]\n'
        '    assert stats.longest_run_above(rows, 90) == 3\n'),
})


def tasks_par():
    return {
        'X15': ('Three independent pieces of work for fleetlog. Each goes in new files of its own and changes no '
                'other file.\n'
                '1. fleetlog/geo.py with tests/test_geo.py: haversine_km(lat1, lon1, lat2, lon2) (earth radius 6371 '
                'km); bearing_deg(lat1, lon1, lat2, lon2), the initial bearing from 0 to 360; destination(lat, lon, '
                'bearing_deg, km) returning (lat, lon); bounding_box(points) returning (min_lat, min_lon, max_lat, '
                'max_lon) for (lat, lon) points; tests for each, with edge cases (the poles, crossing longitude 180, '
                'one point).\n'
                '2. fleetlog/units.py with tests/test_units.py: km_to_miles, miles_to_km, kmh_to_mph, '
                'l100km_to_mpg_us, mpg_us_to_l100km, l100km_to_mpg_uk, mpg_uk_to_l100km, and parse_quantity(text), '
                'which reads a distance such as "12.5 mi", "3 km" or "800 m" and returns kilometres, raising '
                'ValueError for an unknown unit or a malformed text; tests for each.\n'
                '3. fleetlog/costs.py with tests/test_costs.py: fuel_cost(readings, prices), where prices is a list of '
                '(date, price per litre as a Decimal), each in effect from its date on. For each vehicle, in time '
                'order, the fuel used between two consecutive readings (a drop; a rise is a refuel and costs nothing) '
                'costs the price in effect on the earlier reading\'s date. Return {vehicle: total} as Decimals rounded '
                'half-up to cents; tests including a refuel, a price change and a vehicle with one reading.\n'
                'All tests must pass.' + NO_QUESTIONS, {}),
        'Y5': ('Add three functions to fleetlog/stats.py, with tests for each in tests/test_stats.py: '
               'percentile_speed(readings, p), the speed at percentile p (0 to 100) by the nearest-rank method; '
               'moving_minutes(readings), the minutes from each reading above 0 km/h to the next reading, in time '
               'order; and longest_run_above(readings, limit), the most consecutive readings above limit km/h. All '
               'tests must pass.' + NO_QUESTIONS, {}),
    }


def numbers(text):
    """Every number in the text, as a Decimal (so 12.0 and 12 are the same answer)."""
    return {Decimal(found.replace(',', '')) for found in re.findall(r'\d[\d,]*(?:\.\d+)?', text or '')}


def check(name, workspace, reply, answers, pytest_counts):
    """The project's own tests, the task's hidden acceptance tests, and for D15 the saved data and exact answers
    (a vehicle's numbers must be on a line that names it)."""
    tests = pytest_counts(workspace)
    checks = {'tests pass': tests['ok']}
    if name in ACCEPT:
        # Outside the project, so an agent working on a later prompt of the same session never sees them; run from
        # the project, so it imports.
        with tempfile.TemporaryDirectory(prefix='cli-mode-hidden-') as folder:
            hidden = Path(folder) / ('test_accept_' + name.lower() + '.py')
            hidden.write_text(HELPER + ACCEPT[name], encoding='utf-8')
            run = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', str(hidden)],
                                 cwd=workspace, capture_output=True, text=True, timeout=300)
        tail = (run.stdout or run.stderr).strip().splitlines()[-1:] or ['']
        passed = int((re.search(r'(\d+) passed', tail[0]) or [0, 0])[1])
        failed = int((re.search(r'(\d+) (?:failed|error)', tail[0]) or [0, 0])[1])
        checks['hidden'] = '%d/%d' % (passed, passed + failed)
        if failed or run.returncode:  # What failed, for the report (the project is kept too: Run.ask).
            checks['hiddenOutput'] = '\n'.join((run.stdout + run.stderr).strip().splitlines()[-25:])
    if answers:
        saved = workspace / answers.get('csvPath', 'data/fleet.csv')
        checks['data saved exactly'] = saved.exists() and saved.read_text(encoding='utf-8').strip() == \
            answers['csv'].strip()
        if name == 'D15':
            checks['report'] = (workspace / 'reports' / 'fleet.md').exists()
        lines = (reply or '').splitlines()
        hits = {}
        for key, value in answers.items():
            if key in ('csv', 'csvPath'):
                continue
            vehicle = key.split()[0]
            where = [line for line in lines if vehicle in line] if vehicle in VEHICLES else lines
            hits[key] = any(Decimal(value) in numbers(line) if re.fullmatch(r'[\d.]+', value) else value in line
                            for line in where)
        checks['answers'] = '%d/%d' % (sum(hits.values()), len(hits))
        checks['missed'] = [key + '=' + answers[key] for key, hit in hits.items() if not hit][:10]
    return tests, checks
