"""Run one owned execution in Herdr while preserving the existing event parser."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time


class HerdrProcess:
    def __init__(self, argv, cwd, env, prompt, title, labels, parent=None):
        self.folder = Path(tempfile.mkdtemp(prefix='tack-herdr-'))
        self.address = None
        self.title = title
        self.returncode = None
        self.stop_requested = False
        self.stdout = self
        self.reader = None
        try:
            (self.folder/'input.json').write_text(json.dumps(dict(argv=argv, cwd=str(cwd), env=env, title=title)))
            (self.folder/'prompt').write_text(prompt or '')
            (self.folder/'events').touch()
            (self.folder/'lease').touch()
            worker = [sys.executable, str(Path(__file__).with_name('herdr_worker.py')), str(self.folder)]
            if parent:
                layout = subprocess.run(['herdr-app','ls'],capture_output=True,text=True,timeout=5,check=True)
                space = next((space['id'] for space in json.loads(layout.stdout)['spaces']
                              if any(t['terminal']==parent for tab in space['tabs'] for t in tab.get('terminals',[]))),None)
                if not space:
                    raise ValueError('Herdr 원본 터미널의 프로젝트 공간을 찾을 수 없습니다.')
                args = ['herdr-app','do','tab.new','--space',space,'--folder',str(cwd),'--name',title[:60],'--argv',json.dumps(worker)]
                result = subprocess.run(args,capture_output=True,text=True,timeout=15,check=True)
                self.address = json.loads(result.stdout)['terminal']
                subprocess.run(['hc','label',self.address,*[key+'='+value for key,value in labels.items()]],capture_output=True,timeout=5,check=True)
            else:
                args = ['hc','run','--bg','--json','--no-parent','--no-notify','--cwd',str(cwd)]
                for key,value in labels.items():
                    args += ['--label',key+'='+value]
                result = subprocess.run([*args,'--',*worker],capture_output=True,text=True,timeout=15,check=True)
                self.address = json.loads(result.stdout)['address']
            self.reader = (self.folder/'events').open('rb')
        except BaseException:
            try:
                self.stop()
            except (OSError,subprocess.SubprocessError):
                pass
            self.close()
            raise

    def poll(self):
        if self.returncode is not None:
            return self.returncode
        (self.folder/'lease').touch()
        result = self.folder/'exit.json'
        if result.exists():
            self.returncode = json.loads(result.read_text())['exit_code']
        elif time.monotonic() - getattr(self, 'checked_at', 0) > 1:
            self.checked_at = time.monotonic()
            info = subprocess.run(['hc', 'get', self.address, '--json'], capture_output=True, text=True, timeout=5, check=True)
            if json.loads(info.stdout)['state'] != 'live':
                self.returncode = 1  # A lost worker without an exit receipt cannot establish success.
        return self.returncode

    def readline(self):
        while True:
            offset = self.reader.tell()
            line = self.reader.readline()
            if line.endswith(b'\n'):
                return line
            if self.poll() is not None:
                return line
            self.reader.seek(offset)
            time.sleep(.05)

    def wait(self, timeout=None):
        started = time.monotonic()
        while self.poll() is None:
            if timeout is not None and time.monotonic() - started >= timeout:
                raise subprocess.TimeoutExpired(self.address, timeout)
            time.sleep(.05)
        return self.returncode

    def stop(self):
        if self.address and self.returncode is None and not self.stop_requested:
            subprocess.run(['hc', 'kill', self.address, '--json'], capture_output=True, timeout=8, check=True)
            self.stop_requested = True

    def close(self):
        if self.reader:
            self.reader.close()
        # Keep execution logs only in Tack. Wait for the worker's final receipt before removing its files.
        if not self.address or (self.folder/'exit.json').exists():
            import shutil
            shutil.rmtree(self.folder)
