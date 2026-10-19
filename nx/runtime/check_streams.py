#!/usr/bin/env python3
"""Read-only 30-second streaming verification; never opens V4L2 devices."""
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

root = Path(__file__).parent
mapping = json.loads((root / 'cfg/mapping.json').read_text())

def snapshot():
    frames = {c['name']: json.loads(Path('/dev/shm/nx-gmsl/%s.json' % c['name']).read_text()) for c in mapping}
    with urllib.request.urlopen('http://127.0.0.1:9997/v3/paths/list', timeout=5) as response:
        paths = {p['name']: p for p in json.load(response)['items']}
    return {'time': time.time(), 'frames': frames, 'paths': paths}

try:
    before = snapshot()
    print('Measuring %d streams for 30 seconds...' % len(mapping), flush=True)
    time.sleep(30)
    after = snapshot()
    results = []
    for name, last in after['frames'].items():
        first = before['frames'][name]
        interval = last['updated'] - first['updated']
        if interval <= 0:
            raise RuntimeError('Stale stream metrics: ' + name)
        fps = (last['frames'] - first['frames']) / interval
        encoded_fps = (last['encoded'] - first['encoded']) / interval
        path = after['paths'].get(name, {})
        byte_delta = path.get('bytesReceived', 0) - before['paths'].get(name, {}).get('bytesReceived', 0)
        age = after['time'] - (last['last_frame'] or 0)
        ok = (first['started'] == last['started'] and not last['error'] and age < 3
              and 14.5 <= fps <= 15.5 and encoded_fps >= 14.5 and path.get('ready')
              and byte_delta > 0)
        results.append(dict(camera=name, device=last['device'], fps=round(fps, 3),
                            encoded_fps=round(encoded_fps, 3), frame_age_s=round(age, 3),
                            bytes_received_delta=byte_delta, ok=bool(ok)))
    logs = subprocess.check_output(['journalctl', '-k', '--since=@%d' % before['time'],
                                    '--no-pager', '-o', 'cat'], text=True)
    errors = [line for line in logs.splitlines() if re.search(r'corr_err:|uncorr_err:|VI channel not found|NULL VI channel|CHANSEL_', line)]
    report = dict(started=before['time'], finished=after['time'], streams=results,
                  kernel_camera_errors=errors, ok=all(r['ok'] for r in results) and not errors)
    (root / 'logs/stream-check-latest.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    sys.exit(0 if report['ok'] else 1)
except Exception as exc:
    print('FAIL:', exc, file=sys.stderr)
    sys.exit(1)
