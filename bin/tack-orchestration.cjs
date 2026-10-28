#!/usr/bin/env node
'use strict';
const { spawnSync } = require('node:child_process');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const candidates = process.platform === 'win32' ? ['python', 'python3'] : ['python3', 'python'];
const python = candidates.find(command => {
  const result = spawnSync(command, ['-c', 'import sys; sys.exit(sys.version_info < (3, 11))'], { stdio: 'ignore' });
  return result.status === 0;
});
if (!python) {
  console.error('Tack 간편 설치에는 Python 3.11 이상이 필요합니다. Python 설치 후 같은 명령을 다시 실행하세요.');
  process.exit(1);
}
const args = process.argv.slice(2);
const result = spawnSync(python, ['-c', 'import sys; sys.path.insert(0, sys.argv.pop(1)); from agent_work.harness_cli import main; main()', root, ...args], { stdio: 'inherit' });
if (result.error) console.error(result.error.message);
process.exit(result.status ?? 1);
