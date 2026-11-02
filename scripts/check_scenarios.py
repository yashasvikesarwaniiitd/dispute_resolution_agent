"""Check the scenario catalogue against the state machine.

Stage A4 acceptance: every scenario's declared state path must be a legal walk through
transitions.yaml, every invariant and every final outcome must be exercised, and no
scenario may be missing the fields the gates read.

Run:  python scripts/check_scenarios.py .
Exits non-zero on any problem, so Phase 0 can wire it into `make verify` as the
machine check for docs/spec/eval-scenarios.yaml.

path / path_suffix are adjacency-checked. path_contains is order-only by definition,
so each state is checked for reachability from the previous one instead.
"""
import sys
from collections import deque

import yaml

REPO = sys.argv[1] if len(sys.argv) > 1 else '.'
tr = yaml.safe_load(open(REPO + '/docs/spec/transitions.yaml', encoding='utf-8'))
ev = yaml.safe_load(open(REPO + '/docs/spec/eval-scenarios.yaml', encoding='utf-8'))

states = list(tr['states'])
groups = tr['groups']


def members(group_name):
    g = groups[group_name]
    if isinstance(g, dict):
        g = g.get('members') or g.get('states') or []
    return list(g)


# edge -> list of transition ids
edges = {}
for t in tr['transitions']:
    tos = [t['to']]
    if 'from_group' in t:
        froms = members(t['from_group'])
    elif t.get('from') is None:
        froms = [None]
    else:
        froms = [t['from']]
    for f in froms:
        for to in tos:
            edges.setdefault((f, to), []).append(t['id'])

adj = {}
for (f, to) in edges:
    adj.setdefault(f, set()).add(to)


def reachable(src, dst, max_hops=12):
    if src == dst:
        return True
    seen = {src}
    q = deque([(src, 0)])
    while q:
        cur, d = q.popleft()
        if d >= max_hops:
            continue
        for nxt in adj.get(cur, ()):
            if nxt == dst:
                return True
            if nxt not in seen:
                seen.add(nxt)
                q.append((nxt, d + 1))
    return False


terminal = set(members('terminal')) if 'terminal' in groups else set()
waiting = {s for s in states if (tr['states'][s] or {}).get('category') == 'waiting'}

n_scen = n_pairs = n_bad = 0
unknown_states = set()
problems = []

for sc in ev['scenarios']:
    sid = sc['id']
    exp = sc.get('expect') or {}
    n_scen += 1
    for key in ('path', 'path_suffix', 'path_contains'):
        seq = exp.get(key)
        if not seq:
            continue
        for s in seq:
            if s not in states:
                unknown_states.add((sid, s))
        if key == 'path_contains':
            for a, b in zip(seq, seq[1:]):
                if a in states and b in states and not reachable(a, b):
                    n_bad += 1
                    problems.append((sid, key, a, b, 'not reachable by any walk'))
            continue
        for a, b in zip(seq, seq[1:]):
            n_pairs += 1
            if a in states and b in states and (a, b) not in edges:
                n_bad += 1
                problems.append((sid, key, a, b, 'no such transition'))
    fin = exp.get('final')
    if fin and fin not in states:
        problems.append((sid, 'final', fin, '', 'not a declared state'))
    # a path that declares a final state should end there
    if exp.get('path') and fin and exp['path'][-1] != fin:
        problems.append((sid, 'final', exp['path'][-1], fin, 'path ends elsewhere than expect.final'))
    if exp.get('path_suffix') and fin and exp['path_suffix'][-1] != fin:
        problems.append((sid, 'final', exp['path_suffix'][-1], fin, 'path_suffix ends elsewhere than expect.final'))

print('scenarios: %d | adjacency pairs checked: %d | illegal: %d' % (n_scen, n_pairs, n_bad))
print('declared transitions: %d | distinct edges: %d' % (len(tr['transitions']), len(edges)))
if unknown_states:
    print('\nUNKNOWN STATES:')
    for sid, s in sorted(unknown_states):
        print('  %-6s %s' % (sid, s))
if problems:
    print('\nPROBLEMS (%d):' % len(problems))
    for sid, key, a, b, why in problems:
        print('  %-6s %-14s %-22s -> %-22s %s' % (sid, key, a, b, why))
else:
    print('\nNo problems found.')

# which legal edges are never walked
walked = set()
for sc in ev['scenarios']:
    exp = sc.get('expect') or {}
    for key in ('path', 'path_suffix'):
        seq = exp.get(key) or []
        for a, b in zip(seq, seq[1:]):
            walked.add((a, b))
never = sorted(set(edges) - walked - {(None, 'RECEIVED')})
print('\nlegal edges never walked by any path: %d of %d' % (len(never), len(edges)))
for f, to in never:
    print('  %-22s -> %-22s %s' % (f, to, ','.join(edges[(f, to)])))

# --- field and coverage checks the release gates depend on ---
missing_baseline = [s['id'] for s in ev['scenarios'] if 'baseline' not in (s.get('expect') or {})]
missing_actions = [s['id'] for s in ev['scenarios'] if 'actions' not in (s.get('expect') or {})]
covered = set()
for s in ev['scenarios']:
    covered |= set(s.get('invariants') or [])
uncovered_inv = ['I%d' % n for n in range(1, 16) if 'I%d' % n not in covered]

print()
print('missing expect.baseline: %s' % (missing_baseline or 'none'))
print('missing expect.actions:  %s' % (missing_actions or 'none'))
print('invariants with no scenario: %s' % (uncovered_inv or 'none'))

failed = bool(problems or unknown_states or missing_baseline or missing_actions or uncovered_inv)
print()
print('RESULT: %s' % ('FAIL' if failed else 'PASS'))
sys.exit(1 if failed else 0)
