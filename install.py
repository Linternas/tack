#!/usr/bin/env python3
"""Install Tack source and a launcher without touching project data."""
import argparse
import os
import shutil
import sys
from pathlib import Path

def install(source,destination,bin_dir):
    source=Path(source).resolve();destination=Path(destination).expanduser().resolve();bin_dir=Path(bin_dir).expanduser().resolve()
    launcher=bin_dir/'tack'
    if launcher.exists() and 'agent_work.cli' not in launcher.read_text():
        raise RuntimeError(f'기존 명령을 덮어쓰지 않습니다: {launcher}')
    if destination.exists() and destination!=source:
        raise RuntimeError(f'기존 설치 폴더를 덮어쓰지 않습니다: {destination}')
    if destination!=source:
        shutil.copytree(source,destination,ignore=shutil.ignore_patterns('__pycache__','*.pyc','.git','prepare.py','legacy_server.py','cutover.py','instructions','test-results*','prepare_instructions.py','skill-validation'))
    bin_dir.mkdir(parents=True,exist_ok=True)
    launcher.write_text(f'#!{sys.executable}\nimport sys\nsys.path.insert(0, {str(destination)!r})\nfrom agent_work.cli import main\nmain()\n')
    launcher.chmod(0o755)
    legacy=bin_dir/'work'
    if not legacy.exists() or 'from agent_work.cli import main' in legacy.read_text():
        legacy.write_text(launcher.read_text())
        legacy.chmod(0o755)
    return str(launcher)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,default=Path(__file__).resolve().parent)
    p.add_argument('--destination',type=Path,default=Path.home()/'Develop/tack')
    p.add_argument('--bin-dir',type=Path,default=Path.home()/'.local/bin')
    a=p.parse_args()
    print(install(a.source,a.destination,a.bin_dir))
