"""Project-only entry point for the optional orchestration harness."""
import json
import os
from pathlib import Path
import sys

def main():
    if len(sys.argv)>1 and sys.argv[1]=='setup':
        from .harness import install
        try:
            if len(sys.argv)!=3:raise ValueError('사용법: tack-orchestration setup PROJECT_FOLDER')
            print(json.dumps(install(Path(__file__).resolve().parents[1],sys.argv[2]),ensure_ascii=False,indent=2))
        except (ValueError,OSError) as error:
            print(str(error),file=sys.stderr);raise SystemExit(1)
        return
    os.environ['TACK_HARNESS']='1'
    if os.environ.get('TACK_HARNESS_WORKSPACE') and '--workspace' not in sys.argv and '--project' not in sys.argv:
        sys.argv[1:1]=['--workspace',os.environ['TACK_HARNESS_WORKSPACE']]
    from .cli import main as cli
    cli()
