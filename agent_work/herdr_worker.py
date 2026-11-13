"""Private Herdr terminal worker. The Tack controller owns all result decisions."""
import base64
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import threading
import time


def main(folder):
    root = Path(folder)
    spec = json.loads((root/'input.json').read_text())
    (root/'input.json').unlink()
    process = None
    stopped = threading.Event()

    def stop(*_args):
        stopped.set()
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def watch():
        while not stopped.wait(1):
            try:
                expired = time.time() - (root/'lease').stat().st_mtime > 30
            except FileNotFoundError:
                expired = True
            if expired:
                stop()
                return

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGHUP, stop)
    def report(state):
        app = 'codex' if Path(spec['argv'][0]).name == 'codex' else Path(spec['argv'][0]).name
        title = base64.b64encode(spec['title'].encode()).decode()
        print('\x1b]7501;state='+state+':app='+app+':title='+title+'\x1b\\',end='',flush=True)

    code = 1
    print(spec['title'], flush=True)
    try:
        with (root/'prompt').open('rb') as prompt, (root/'events').open('ab', buffering=0) as events:
            argv = spec['argv']
            if not shutil.which(argv[0], path=spec['env'].get('PATH')):
                shell = spec['env'].get('SHELL', '/bin/zsh')
                command = 'exec $argv' if Path(shell).name == 'fish' else 'exec "$@"'
                argv = [shell, '-lic', command, 'tack-herdr', *argv] if Path(shell).name != 'fish' else [shell, '-lic', command, *argv]
            process = subprocess.Popen(argv, cwd=spec['cwd'], env=spec['env'], stdin=prompt,
                                       stdout=subprocess.PIPE, start_new_session=True)
            report('working')
            (root/'prompt').unlink()
            threading.Thread(target=watch, daemon=True).start()
            for line in process.stdout:
                events.write(line)
                try:
                    event = json.loads(line)
                    item = event.get('item', {})
                    if event.get('type') == 'item.completed' and item.get('type') == 'agent_message':
                        print(item.get('text', ''), flush=True)
                    elif event.get('type') in {'thread.started', 'turn.completed', 'turn.failed', 'error'}:
                        print(line.decode().strip(), flush=True)
                except (ValueError, UnicodeError):
                    pass
            code = process.wait()
            process.stdout.close()
    finally:
        stop()
        report('done' if code == 0 else 'error')
        (root/'exit.tmp').write_text(json.dumps({'exit_code': code}))
        (root/'exit.tmp').replace(root/'exit.json')
    return code


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))
