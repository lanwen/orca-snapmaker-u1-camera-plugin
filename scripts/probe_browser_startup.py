"""Temporary, bounded observation of the shared browser launcher on CI."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from unittest.mock import patch

import review_preview
from prepare_screenshots import find_browser


clock = time.monotonic
popen = subprocess.Popen
path_exists = Path.exists
stop_browser = review_preview._stop_browser
executable = find_browser()


def emit(event, **fields):
    print(json.dumps(dict(event=event, **fields)), flush=True)


def owned_states(root):
    pending, seen, rows = [root], set(), []
    while pending:
        pid = pending.pop()
        if pid in seen:
            continue
        seen.add(pid)
        try:
            if os.getpgid(pid) != root:
                continue
            proc = Path('/proc') / str(pid)
            fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
            rows.append(dict(pid=pid, ppid=int(fields[1]), state=fields[0],
                             name=(proc / 'comm').read_text().strip(),
                             wait=(proc / 'wchan').read_text().strip()))
            pending.extend(map(int, (proc / 'task' / str(pid) / 'children').read_text().split()))
        except (OSError, ValueError):
            continue
    return rows


def output(fd):
    size = os.fstat(fd).st_size
    return os.pread(fd, min(size, 2000), max(0, size - 2000)).decode(errors='replace')


def probe(name, extra=()):
    state = dict(profile=None, fd=None, process=None, started=None, port_at=None)
    checkpoints = set()
    result = dict(name=name, extra=list(extra), ready=False)

    def snapshot(label):
        process = state['process']
        emit('snapshot', name=name, label=label, elapsed=round(clock() - state['started'], 3),
             exit_code=process.poll_original(),
             port_file=path_exists(state['profile'] / 'DevToolsActivePort'),
             processes=owned_states(process.pid), output=output(state['fd']))

    def launch(args, **kwargs):
        args = list(args)
        args[-1:-1] = extra
        state['profile'] = Path(next(arg.split('=', 1)[1] for arg in args if arg.startswith('--user-data-dir=')))
        state['fd'] = os.dup(kwargs['stderr'].fileno())
        state['started'] = clock()
        process = popen(args, **kwargs)
        state['process'] = process
        process.poll_original = process.poll

        def poll():
            elapsed = clock() - state['started']
            for checkpoint in (10, 20):
                if elapsed >= checkpoint and checkpoint not in checkpoints:
                    checkpoints.add(checkpoint)
                    snapshot(str(checkpoint) + 's')
            return process.poll_original()

        process.poll = poll
        return process

    def exists(path):
        found = path_exists(path)
        if found and path.name == 'DevToolsActivePort' and path.parent == state['profile'] and state['port_at'] is None:
            state['port_at'] = clock() - state['started']
        return found

    def stop(process, connection):
        snapshot('before_cleanup')
        stop_browser(process, connection)

    started = clock()
    try:
        # Observe 20 real seconds, but judge readiness against the original 10-second budget.
        with patch.object(review_preview.subprocess, 'Popen', launch), \
                patch.object(review_preview.time, 'monotonic', side_effect=lambda: clock() / 2), \
                patch.object(Path, 'exists', exists), \
                patch.object(review_preview, '_stop_browser', stop):
            with review_preview.browser() as client:
                result['ready'] = client.evaluate('1 + 1') == 2
                result['cdp_elapsed'] = round(clock() - state['started'], 3)
    except Exception as error:
        result['error'] = str(error)
    finally:
        result['elapsed_including_cleanup'] = round(clock() - started, 3)
        result['port_elapsed'] = round(state['port_at'], 3) if state['port_at'] is not None else None
        result['within_original_budget'] = state['port_at'] is not None and state['port_at'] <= 10
        if state['process'] is not None:
            result['exit_code_after_cleanup'] = state['process'].returncode
        if state['fd'] is not None:
            result['output'] = output(state['fd'])
            os.close(state['fd'])
    emit('result', **result)
    return result


def dump_dom():
    profile = Path(tempfile.mkdtemp(prefix='u1-probe-dump-'))
    process = None
    started = clock()
    with tempfile.TemporaryFile() as log:
        try:
            process = popen([
                executable, '--headless', '--enable-logging=stderr', '--no-first-run', '--no-default-browser-check',
                '--disable-background-networking', '--disable-sync', '--hide-scrollbars',
                '--user-data-dir=' + str(profile), '--dump-dom', 'about:blank',
            ], stdout=log, stderr=log, start_new_session=True)
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                pass
            emit('dump_dom', elapsed=round(clock() - started, 3), exit_code=process.poll(),
                 processes=owned_states(process.pid), output=output(log.fileno()))
        finally:
            if process is not None:
                stop_browser(process, None)
            review_preview._remove_profile(profile)


version = subprocess.run([executable, '--version'], capture_output=True, text=True, timeout=5)
emit('environment', executable=executable, version=version.stdout.strip(),
     desktop={name: os.environ.get(name) for name in (
         'DBUS_SESSION_BUS_ADDRESS', 'DISPLAY', 'XDG_RUNTIME_DIR', 'XDG_CURRENT_DESKTOP', 'XDG_SESSION_TYPE')})
baseline = probe('baseline')
basic = probe('password_store_basic', ('--password-store=basic',))
if not baseline['within_original_budget'] and basic['within_original_budget']:
    probe('baseline_after_candidate')
elif not baseline['within_original_budget'] and not basic['within_original_budget']:
    dump_dom()
