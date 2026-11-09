"""Run an experiment in the background; monitor it in foreground SOP recalls."""
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time


INTERVALS = (450, 900, 1800, 3600)
IMPORTANT = re.compile(rb'\b(?:WARNING|ERROR)\b', re.I)
LOGS = ('stdout.log', 'stderr.log')
PROCESSES = {}


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value))
    temporary.replace(path)


def start(argv, cwd, logs):
    logs = Path(logs).resolve()
    logs.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='test-', dir=logs))
    for name in LOGS:
        (directory / name).touch()
    write(directory / 'job.json', {'argv': list(argv), 'cwd': str(Path(cwd).resolve())})
    with (directory / 'stderr.log').open('ab') as errors:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), str(directory)],
                                   stdin=subprocess.DEVNULL, stdout=errors, stderr=errors,
                                   start_new_session=True)
    state = read(directory / 'job.json')
    state['pid'] = process.pid
    write(directory / 'job.json', state)
    PROCESSES[str(directory)] = process
    return str(directory)


def run(directory):
    directory = Path(directory)
    state = read(directory / 'job.json')
    with (directory / 'stdout.log').open('ab') as output, (directory / 'stderr.log').open('ab') as errors:
        try:
            result = subprocess.run(state['argv'], cwd=state['cwd'], stdout=output, stderr=errors,
                                    env={**os.environ, 'PYTHONUNBUFFERED': '1'})
            code = result.returncode
        except OSError as error:
            errors.write(('ERROR: ' + str(error) + '\n').encode())
            code = 1
    write(directory / 'result.json', {'returncode': code})


def appended(directory, state):
    output, important = {}, False
    for name in LOGS:
        with (directory / name).open('rb') as stream:
            stream.seek(state['offsets'].get(name, 0))
            tail, boundary = b'', b''
            while chunk := stream.read(65536):
                important |= bool(IMPORTANT.search(boundary + chunk))
                boundary = chunk[-16:]
                tail = (tail + chunk)[-2000:]
            state['offsets'][name] = stream.tell()
        if tail:
            output[name] = tail.decode(errors='replace')
    return output, important


def wait(directory, state):
    directory = Path(directory)
    if state.get('job') != str(directory):
        state.clear()
        state.update(job=str(directory), offsets={}, checkpoint=0)
    deadline = time.monotonic() + INTERVALS[min(state['checkpoint'], len(INTERVALS) - 1)]
    collected = {}
    while True:
        output, important = appended(directory, state)
        for name, text in output.items():
            collected[name] = (collected.get(name, '') + text)[-2000:]
        ended = (directory / 'result.json').exists()
        elapsed = time.monotonic() >= deadline
        if ended or important or elapsed:
            if ended and (process := PROCESSES.pop(str(directory), None)) is not None:
                process.wait()
            if elapsed and not ended:
                state['checkpoint'] += 1
            return {'state': 'ended' if ended else 'running',
                    'reason': 'exit' if ended else 'important_log' if important else 'checkpoint',
                    'logs': {name: str(directory / name) for name in LOGS},
                    'new_output': collected,
                    **(read(directory / 'result.json') if ended else {})}
        time.sleep(min(1, max(0, deadline - time.monotonic())))


def stop(directory):
    directory = Path(directory)
    if not (directory / 'result.json').exists():
        pid = read(directory / 'job.json')['pid']
        try:
            os.killpg(pid, signal.SIGTERM)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                os.killpg(pid, 0)
                time.sleep(.05)
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        write(directory / 'result.json', {'returncode': None, 'exception': True})
    if (process := PROCESSES.pop(str(directory), None)) is not None:
        process.wait()


def delivery(directory):
    return json.loads((Path(directory) / 'stdout.log').read_text())


if __name__ == '__main__':
    run(sys.argv[1])
