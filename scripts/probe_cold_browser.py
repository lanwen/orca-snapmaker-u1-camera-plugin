"""Temporary Linux first-launch measurement; the shared launcher stays unchanged."""

import json
import os
from pathlib import Path
import signal
import subprocess
import time
from types import SimpleNamespace
from unittest.mock import patch

import review_preview
from prepare_screenshots import find_browser


clock = time.monotonic
popen = subprocess.Popen
stop_browser = review_preview._stop_browser
state = dict(process=None, started=None, fd=None, profile=None, port_elapsed=None, cdp_elapsed=None)


def emit(event, **fields):
    print(json.dumps(dict(event=event, **fields)), flush=True)


def output():
    fd = state['fd']
    if fd is None:
        return ''
    size = os.fstat(fd).st_size
    return os.pread(fd, min(size, 2000), max(0, size - 2000)).decode(errors='replace')


def owned_states():
    root = state['process'].pid
    rows = []
    ticks = os.sysconf('SC_CLK_TCK')
    uptime = float(Path('/proc/uptime').read_text().split()[0])
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
            if int(fields[2]) != root:
                continue
            io = {}
            try:
                for line in (proc / 'io').read_text().splitlines():
                    key, value = line.split(':', 1)
                    if key in {'rchar', 'wchar', 'syscr', 'syscw', 'read_bytes', 'write_bytes'}:
                        io[key] = int(value)
            except OSError:
                pass
            rows.append(dict(pid=int(proc.name), ppid=int(fields[1]), state=fields[0],
                             name=(proc / 'comm').read_text().strip(),
                             wait=(proc / 'wchan').read_text().strip(),
                             elapsed_seconds=round(uptime - int(fields[19]) / ticks, 3),
                             user_cpu_seconds=int(fields[11]) / ticks,
                             system_cpu_seconds=int(fields[12]) / ticks, io=io))
        except (OSError, ValueError, IndexError):
            continue
        if len(rows) >= 64:
            break
    return rows


def snapshot(label):
    emit('snapshot', label=label, elapsed=round(clock() - state['started'], 3),
         exit_code=state['process'].poll(), processes=owned_states(), stderr_tail=output())


def deadline(signum, frame):
    elapsed = clock() - state['started']
    if elapsed < 29:
        if state['cdp_elapsed'] is None:
            snapshot('original_10s_budget')
        signal.setitimer(signal.ITIMER_REAL, max(.001, 30 - elapsed))
    else:
        snapshot('30s_observation_limit')
        raise TimeoutError('Cold launch observation reached 30 seconds')


def launch(args, **kwargs):
    state['profile'] = Path(next(arg.split('=', 1)[1] for arg in args if arg.startswith('--user-data-dir=')))
    state['fd'] = os.dup(kwargs['stderr'].fileno())
    state['started'] = clock()
    state['process'] = popen(args, **kwargs)
    signal.setitimer(signal.ITIMER_REAL, max(.001, 10 - (clock() - state['started'])))
    emit('launch', executable=args[0], args=args, start_new_session=kwargs['start_new_session'])
    return state['process']


path_exists = Path.exists


def exists(path):
    found = path_exists(path)
    if found and path.name == 'DevToolsActivePort' and path.parent == state['profile'] and state['port_elapsed'] is None:
        state['port_elapsed'] = clock() - state['started']
        emit('port_ready', elapsed=round(state['port_elapsed'], 3))
    return found


def stop(process, connection):
    signal.setitimer(signal.ITIMER_REAL, 0)
    snapshot('before_cleanup')
    review_preview.time = time
    stop_browser(process, connection)


def main():
    executable = find_browser()  # Path lookup only; do not execute Chrome before browser().
    emit('environment', executable=executable, desktop={name: os.environ.get(name) for name in (
        'DBUS_SESSION_BUS_ADDRESS', 'DISPLAY', 'XDG_RUNTIME_DIR', 'XDG_CURRENT_DESKTOP', 'XDG_SESSION_TYPE')})
    previous = signal.signal(signal.SIGALRM, deadline)
    result = dict(ready=False)
    try:
        # Scale only the launcher's clock to observe up to 30s; preserve its command and I/O.
        observation_time = SimpleNamespace(monotonic=lambda: clock() / 3, sleep=time.sleep)
        with patch.object(review_preview.subprocess, 'Popen', launch), \
                patch.object(review_preview, 'time', observation_time), \
                patch.object(Path, 'exists', exists), \
                patch.object(review_preview, '_stop_browser', stop):
            with review_preview.browser() as client:
                result['ready'] = client.evaluate('1 + 1') == 2
                state['cdp_elapsed'] = clock() - state['started']
                emit('cdp_ready', elapsed=round(state['cdp_elapsed'], 3))
    except Exception as error:
        result['error'] = str(error)[-2000:]
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        for key in ('port_elapsed', 'cdp_elapsed'):
            result[key] = round(state[key], 3) if state[key] is not None else None
        result['within_original_budget'] = result['ready'] and state['cdp_elapsed'] <= 10
        result['stderr_tail'] = output()
        if state['process'] is not None:
            result['exit_code_after_cleanup'] = state['process'].returncode
            result['owned_processes_after_cleanup'] = owned_states()
        result['profile_removed'] = state['profile'] is not None and not state['profile'].exists()
        if state['fd'] is not None:
            os.close(state['fd'])
        emit('result', **result)
    # The measured browser and profile have closed before this second Chrome execution.
    version = subprocess.run([executable, '--version'], capture_output=True, text=True, timeout=5)
    emit('version_after_cleanup', version=version.stdout.strip(), exit_code=version.returncode)


if __name__ == '__main__':
    main()
