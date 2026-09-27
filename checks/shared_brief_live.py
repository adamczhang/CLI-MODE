"""Live check: one project worked on from both hosts at once, on the INSTALLED plugins.

A Codex thread (through `codex app-server`, as Desktop runs it) and a Claude Code session (`claude -p` with
bypassPermissions, as the desktop app runs it) share one throwaway git project and its project brief:

S. each host starts an agent and writes its own note; the brief lists both conversations' agents, keeps what was
   written in it by hand, and keeps listing one host's agent while the other host works, and after it closes;
P. approvals on Claude Code with an agent that asks first (Claude): ask, approve, ask again, approve always (kept
   for a kind; refused for a request of no known kind, as Claude's commands on Windows are); it is never marked as
   acting without asking;
U. /cli usage on both hosts;
I. with /cli display instant (restored to chat afterwards), a new agent leaves no unwritten host note.

It spends real quota: Codex and Claude Code turns on your plans, and the agents' own accounts.

    python checks/shared_brief_live.py [--codex-agent grok-build] [--claude-agent claude] [--keep]
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_user_validation as claude_side  # noqa: E402
import codex_user_validation as codex_side  # noqa: E402

sys.path.insert(0, str(codex_side.INSTALLED / 'scripts'))
import agent_folder  # noqa: E402  (the installed copy)
from state import ACTS_WITHOUT_ASKING  # noqa: E402

TAGS = {'grok-build': 'gro', 'codex': 'cod', 'claude': 'cla', 'copilot': 'cop', 'agy': 'agy', 'cursor': 'cur'}
OWN_TEXT = ('We water the beds at dawn.', '## Our own notes', 'Keep the app small.')
WAITING = agent_folder.HOST_NOTE_WAITING[:-2]


def expect(condition, message):
    return None if condition else message


def git(project, *args):
    return subprocess.run(['git', '-C', str(project), *args], capture_output=True, text=True, check=True).stdout


class Run:
    def __init__(self, project):
        self.project, self.rows = project, []

    def step(self, ident, session, prompt, check=None, instant=False):
        turn = session.send(prompt, ident)
        shown = session.shown(turn)
        # An instant reply is the hook's own, in place of the prompt: `claude -p` may then end with no result.
        problems = [problem for problem in turn['problems'] if not (instant and problem.startswith('no result'))]
        try:
            problems += [problem for problem in (check(turn, shown) if check else []) if problem]
        except Exception as exc:
            problems.append('check failed to run: ' + repr(exc)[:300])
        self.rows.append(dict(step=ident, host=session.host, prompt=prompt[:160], status='fail' if problems else 'pass',
                              problems=problems, seconds=turn['seconds'], shown=shown[-1500:]))
        print(('PASS ' if not problems else 'FAIL ') + ident + ' [' + session.host + ']' +
              (': ' + ' | '.join(problems) if problems else ''), flush=True)
        return turn, shown

    def brief(self):
        return agent_folder.read_brief(self.project)

    def text(self):
        path = agent_folder.brief_path(self.project)
        return path.read_text(encoding='utf-8') if path.is_file() else ''

    def team_has(self, *aliases):
        team = self.brief()[2]
        return [expect(len(team) == len(aliases), 'agent list: ' + json.dumps(team))] + [
            expect(any('Agent_Working_Folder/' + alias + '/' in line for line in team), alias + ' is not listed')
            for alias in aliases]

    def notes_written(self):
        notes = self.brief()[1]
        return [expect(not any(line.startswith(WAITING) for line in notes),
                       'a host note is unwritten: ' + json.dumps(notes)[:400])]

    def own_text_kept(self):
        text = self.text()
        return [expect(line in text, 'text written by hand is gone from the brief: ' + line) for line in OWN_TEXT]


def alias_of(session, backend):
    return next((item['alias'] for item in session.state().get('owned') or []
                 if item.get('backend') == backend and item.get('alias')), None)


def entry(session, alias):
    return next((item for item in session.state().get('owned') or [] if item.get('alias') == alias), {})


def run(codex_agent, claude_agent, keep):
    stamp = time.strftime('%Y%m%d-%H%M%S')
    evidence = Path(tempfile.gettempdir()) / 'cmv' / ('shared-brief-' + stamp)
    (evidence / 'claude').mkdir(parents=True)
    project = evidence / 'project'
    project.mkdir()
    git(project, 'init', '-q')
    (project / 'plan.md').write_text('# Garden planner\n\nPlanting dates and watering reminders.\n', encoding='utf-8')
    git(project, 'add', '.')
    git(project, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'start')
    r = Run(project)
    codex = codex_side.Session(project, evidence)
    claude = claude_side.Session(claude_side.desktop_claude(), project, evidence / 'claude')
    display = claude_side.DATA / 'display.json'
    saved_display = display.read_bytes() if display.is_file() else None
    try:
        # S. Both hosts, one brief ------------------------------------------------------------------------------
        r.step('S0', codex, 'We are building a garden-planner app: planting dates and watering reminders. Reply OK.')
        r.step('S1', codex, '/cli spawn ' + TAGS[codex_agent], lambda turn, shown: r.team_has(alias_of(codex, codex_agent))
               + r.notes_written())
        grok = alias_of(codex, codex_agent)
        r.step('S2', claude, 'This session helps with the same garden-planner app. Reply OK.')
        r.step('S3', claude, '/cli bind ' + claude_agent, lambda turn, shown: r.team_has(
            grok, alias_of(claude, claude_agent)) + r.notes_written() + [
            expect(len([line for line in r.brief()[1] if line.startswith('### ')]) == 2,
                   'expected a note from each host: ' + json.dumps(r.brief()[1])[:400])])
        mine = alias_of(claude, claude_agent)
        path = agent_folder.brief_path(project)
        path.write_text(r.text().rstrip('\n') + '\n\n' + OWN_TEXT[0] + '\n\n' + OWN_TEXT[1] + '\n\n' + OWN_TEXT[2] +
                        '\n', encoding='utf-8')
        r.step('S4', claude, '/d Reply with only the word hi.', lambda turn, shown: r.team_has(grok, mine) +
               r.own_text_kept())
        r.step('S5', codex, '/d Reply with only the word hello.', lambda turn, shown: r.team_has(grok, mine) +
               r.own_text_kept())

        # P. Approvals with an agent that asks first, on Claude Code ----------------------------------------------
        r.step('P1', claude, '/cli access prompt')

        def asks(turn, shown):
            # Claude's commands on Windows come as kind `other` (its PowerShell tool): "use this tool".
            return [expect('asks to run commands' in shown or 'asks to use this tool' in shown, 'no question shown'),
                    expect(entry(claude, mine).get('approval'), 'no question kept on the agent')]

        def unmarked(turn, shown):
            return [expect(claude_agent in ACTS_WITHOUT_ASKING or not entry(claude, mine).get('actsWithoutAsking'),
                           'an agent that asks first was marked as acting without asking')]

        def settled(turn, shown):
            return unmarked(turn, shown) + [expect(not entry(claude, mine).get('approval'), 'the question is still waiting')]
        r.step('P2', claude, '/d Run the shell command: git status', asks)
        r.step('P3', claude, '/cli approve', settled)
        r.step('P4', claude, '/d Run the shell command: git log --oneline -1', lambda turn, shown: asks(turn, shown) + [
            expect('without asking' not in shown, 'the question used the wording for agents that don\'t ask')])
        kind = (entry(claude, mine).get('approval') or {}).get('kind')
        if kind in ('execute', 'edit'):  # A kind: approving always keeps it, and it is not asked again.
            r.step('P5', claude, '/cli approve always', lambda turn, shown: settled(turn, shown) + [
                expect(kind in (entry(claude, mine).get('approveAlways') or []), 'the kind is not approved always')])
            r.step('P6', claude, '/d Run the shell command: git branch', lambda turn, shown: settled(turn, shown) + [
                expect('asks to' not in shown, 'it asked again for a kind approved always')])
        else:  # No kind: "always" could only match this one step again, so it is refused and the question waits.
            r.step('P5', claude, '/cli approve always', lambda turn, shown: unmarked(turn, shown) + [
                expect('does not say what kind of tool it is' in shown, '"approve always" was not refused'),
                expect(not entry(claude, mine).get('approveAlways'), 'a rule was kept always'),
                expect(entry(claude, mine).get('approval'), 'the question was lost')])
            r.step('P6', claude, '/cli approve', settled)
        r.step('P7', claude, '/cli access allow')

        # U. /cli usage on both hosts -----------------------------------------------------------------------------
        def usage(alias):
            return lambda turn, shown: [
                expect(alias in shown, '/cli usage did not name ' + alias),
                expect('% used' in shown or 'unlimited' in shown or 'not supported through its CLI' in shown or
                       'can\'t report its usage' in shown, 'no usage line: ' + shown[-300:])]
        r.step('U1', claude, '/cli usage', usage(mine))
        r.step('U2', codex, '/cli usage', usage(grok))

        # I. The instant display leaves no unwritten note ---------------------------------------------------------
        r.step('I1', claude, '/cli display instant', instant=True)
        before = len([line for line in r.brief()[1] if line.startswith('### ')])
        r.step('I2', claude, '/cli spawn cod', lambda turn, shown: r.notes_written() + [
            expect(len([line for line in r.brief()[1] if line.startswith('### ')]) == before,
                   'an entry the instant reply could not write was left in the brief'),
            expect(not turn['modelTurns'], 'the instant reply took a model turn')], instant=True)
        r.step('I3', claude, '/cli display chat', instant=True)
        third = next((item['alias'] for item in claude.state().get('owned') or []
                      if item.get('alias') not in (mine, None)), None)
        r.step('I4', claude, '/cli close ' + (third or 'cod').lower(), lambda turn, shown: r.team_has(grok, mine))

        # S. Closing one host's agents leaves the other's ------------------------------------------------------------
        r.step('S6', claude, '/cli close', lambda turn, shown: r.team_has(grok) + r.own_text_kept() + [
            expect(len([line for line in r.brief()[1] if line.startswith('### ')]) == 2, 'host notes not kept')])
        r.step('S7', codex, '/d Reply with only the word still.', lambda turn, shown: r.team_has(grok))
        r.step('S8', codex, '/cli close', lambda turn, shown: [
            expect(r.brief()[2] == [], 'the agent list is not empty: ' + json.dumps(r.brief()[2])),
            expect(not list((project / 'Agent_Working_Folder' / '.cli-mode').glob('*.json')),
                   'a conversation\'s list was left behind')] + r.own_text_kept())
    except Exception as exc:
        r.rows.append(dict(step='abort', status='fail', problems=['aborted: ' + repr(exc)[:500]]))
        print('ABORT ' + repr(exc)[:500], flush=True)
    finally:
        if saved_display is None:
            display.unlink(missing_ok=True)
        else:
            display.write_bytes(saved_display)
        for session in (claude, codex):
            try:
                if session.state().get('active'):
                    session.send('/cli close', 'cleanup')
            except Exception:
                pass
        codex.close()
    report = dict(evidence=str(evidence), passed=sum(row['status'] == 'pass' for row in r.rows),
                  failed=sum(row['status'] == 'fail' for row in r.rows), steps=r.rows)
    (evidence / 'report.json').write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding='utf-8')
    if not keep and not report['failed']:
        shutil.rmtree(project, ignore_errors=True)
        # Both conversations' CLI-MODE state goes with the project, as the other harnesses do.
        for data, thread in ((claude_side.DATA, claude.id), (codex_side.DATA, codex.thread)):
            for path in (data / 'sessions').glob(hashlib.sha256(thread.encode()).hexdigest() + '*'):
                path.unlink(missing_ok=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--codex-agent', default='grok-build', choices=sorted(TAGS))
    parser.add_argument('--claude-agent', default='claude', choices=sorted(TAGS))
    parser.add_argument('--keep', action='store_true', help='Keep the project even when every step passes.')
    args = parser.parse_args()
    report = run(args.codex_agent, args.claude_agent, args.keep)
    print(json.dumps({key: report[key] for key in ('passed', 'failed', 'evidence')}, indent=1))
    raise SystemExit(1 if report['failed'] else 0)


if __name__ == '__main__':
    main()
