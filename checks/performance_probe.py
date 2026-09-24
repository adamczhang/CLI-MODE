"""Opt-in latency probe. Uses isolated live sessions and small quota-consuming prompts."""
import argparse
from pathlib import Path
import json
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/cli-mode/scripts'))
import acpx
import adapters
from controller import Controller
from state import Store, route


def run(agent, folder, rounds):
    folder.mkdir(parents=True)
    workspace = folder / 'workspace'; workspace.mkdir()
    adapter = adapters.module(agent)
    timings = []
    class Timed(adapter.Backend):
        def start(self, owned, args, timeout=60):
            start = time.perf_counter()
            process = super().start(owned, args, timeout)
            timings.append({'operation': 'prompt-launch' if '--file' in args else ' '.join(args),
                            'spawn_s': time.perf_counter()-start, 'started': start})
            return process
        def collect(self, process, *args, **kwargs):
            start = time.perf_counter()
            try: return super().collect(process, *args, **kwargs)
            finally: timings[-1]['collect_s'] = time.perf_counter()-start
    c = Controller(Store('latency-'+uuid.uuid4().hex, workspace, folder/'state'), Timed(), agent)
    result = {'agent':agent,'activation_s':None,'turns':[], 'host_ui_measured':False}
    active = None
    try:
        c.frontend(agent)
        started = time.perf_counter()
        c.activate(agent=agent, **adapter.DEFAULTS)
        result['activation_s'] = time.perf_counter()-started
        result['activation_operations'] = [dict(x) for x in timings]
        print(agent,'activation',round(result['activation_s'],3),flush=True)
        for index in range(rounds*2):
            mode = 'passthrough' if index%2==0 else 'direct'
            c.mode(mode)
            text = ('/d ' if mode=='direct' else '') + 'Reply with exactly OK. Do not use tools or edit files.'
            route_start = time.perf_counter(); decision=route(text,c.store.read()); route_s=time.perf_counter()-route_start
            start_count=len(timings)
            active={'mode':mode,'start':time.perf_counter(),'route_s':route_s,'route':decision['route']}
            def output(event):
                if event.get('type')=='dispatched': active.setdefault('dispatch_s',time.perf_counter()-active['start'])
                if event.get('type')=='message': active.setdefault('first_relay_s',time.perf_counter()-active['start'])
            c.send(text,output=output,timeout=90)
            active['total_s']=time.perf_counter()-active.pop('start')
            active['operations']=[dict(x) for x in timings[start_count:]]
            result['turns'].append(active)
            print(agent,json.dumps({k:v for k,v in active.items() if k!='operations'}),flush=True)
            active=None
    except Exception as exc:
        result['error']=str(exc)[:600]
        print(agent,'blocked',result['error'],flush=True)
    finally:
        try: result['shutdown']=c.off()['shutdownComplete']
        except Exception as exc: result['shutdown_error']=str(exc)[:300]
        (folder/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agents',nargs='+',default=adapters.implemented(),choices=adapters.implemented())
    parser.add_argument('--rounds',type=int,default=2)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--node-launcher',action='store_true',help='Require the verified direct Node launcher')
    args=parser.parse_args()
    if args.rounds < 1: parser.error('--rounds must be positive')
    args.output.mkdir(parents=True,exist_ok=False)
    executable=acpx.executable()
    if args.node_launcher:
        if len(executable) != 2 or Path(executable[-1]).name != 'cli.js':
            raise RuntimeError('Verified direct Node launcher unavailable')
    results=[]
    for agent in args.agents:
        results.append(run(agent,args.output/agent,args.rounds))
        (args.output/'summary.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
    if any(result.get('error') or result.get('shutdown') is not True or
           len(result['turns']) != args.rounds * 2 for result in results):
        raise SystemExit(1)

if __name__=='__main__': main()
