#!/usr/bin/env node
'use strict';

const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const pkg = require('../package.json');

const DEFAULT_REPO_URL = 'https://github.com/Alex-tangt/Agent-Knowledge-Base.git';
const DEFAULT_DIR = 'agent-knowledge-base';
const IS_WINDOWS = process.platform === 'win32';

const HELP = `agent-kb — Agent-Knowledge-Base 的 npx 薄包装安装器（#56）

用法：
  npx github:Alex-tangt/Agent-Knowledge-Base [目标目录] [安装参数...]
  agent-kb [目标目录] [安装参数...]

行为（薄壳，不改 Python 运行时）：
  1) 把源码取到 <目标目录>（git clone；已存在则 fetch / 复用，幂等）;
  2) 在该目录调用与 install.sh / install.ps1 相同的入口：
       <python> -m memory_agent.deploy install <安装参数...>

包装器选项：
  --dir <path>       目标目录（也可用第一个位置参数；默认 ./${DEFAULT_DIR}）
  --repo-url <url>   源码仓库地址（默认官方仓库；env AGENT_KB_REPO）
  --ref <ref>        要检出的分支 / 标签（env AGENT_KB_REF）
  --force            现有 clone 的 origin 与期望来源不一致时仍更新
  -h, --help         显示本帮助
  -v, --version      显示版本
  --                 其后的参数原样透传给安装器

透传的安装参数（memory-agent install）：
  --dry-run · --no-index · --no-daemon · --no-smoke · --no-register · --no-skill
  · --force-index · --with-tests · --skip-deps · --python <解释器>
  · --opencode-home <目录> · --cpu-torch / --no-cpu-torch

环境变量：
  AGENT_KB_REPO          源码仓库地址
  AGENT_KB_REF           默认检出 ref
  AGENT_KB_DIR           默认目标目录
  MEMORY_INSTALL_PYTHON  引导用的系统 Python（默认 python / python3）

前置：node >= 18、git（记忆写入需要）、Python >= 3.10（含 venv）。
示例：
  npx --yes github:Alex-tangt/Agent-Knowledge-Base my-kb --dry-run
  npx --yes github:Alex-tangt/Agent-Knowledge-Base my-kb --no-daemon --opencode-home /tmp/oc
`;

function log(msg) {
  process.stdout.write(`${msg}\n`);
}

function warn(msg) {
  process.stderr.write(`agent-kb: ${msg}\n`);
}

function die(msg, code = 1) {
  process.stderr.write(`agent-kb: ${msg}\n`);
  process.exit(code);
}

function commandOk(cmd, args = ['--version']) {
  const r = spawnSync(cmd, args, { stdio: 'ignore' });
  return !r.error && r.status === 0;
}

function gitVisible(args, cwd) {
  const r = spawnSync('git', args, { cwd, stdio: 'inherit' });
  if (r.error) die(`无法运行 git：${r.error.message}（请先安装 git）`, 2);
  return r.status == null ? 1 : r.status;
}

function gitCapture(args, cwd) {
  const r = spawnSync('git', args, { cwd, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
  return {
    ok: !r.error && r.status === 0,
    status: r.status,
    stdout: (r.stdout || '').trim(),
    stderr: (r.stderr || '').trim(),
  };
}

function parseArgs(argv) {
  const opts = {
    dir: null,
    repoUrl: process.env.AGENT_KB_REPO || DEFAULT_REPO_URL,
    ref: process.env.AGENT_KB_REF || null,
    force: false,
    help: false,
    version: false,
    pass: [],
  };
  const sep = argv.indexOf('--');
  const left = sep >= 0 ? argv.slice(0, sep) : argv;
  const right = sep >= 0 ? argv.slice(sep + 1) : [];
  const rest = [];
  const takeValue = (flag, i) => {
    const v = left[i + 1];
    if (v === undefined) die(`${flag} 缺少取值`);
    return v;
  };
  for (let i = 0; i < left.length; i += 1) {
    const a = left[i];
    if (a === '-h' || a === '--help') opts.help = true;
    else if (a === '-v' || a === '--version') opts.version = true;
    else if (a === '--dir') { opts.dir = takeValue('--dir', i); i += 1; }
    else if (a.startsWith('--dir=')) opts.dir = a.slice('--dir='.length);
    else if (a === '--repo-url') { opts.repoUrl = takeValue('--repo-url', i); i += 1; }
    else if (a.startsWith('--repo-url=')) opts.repoUrl = a.slice('--repo-url='.length);
    else if (a === '--ref') { opts.ref = takeValue('--ref', i); i += 1; }
    else if (a.startsWith('--ref=')) opts.ref = a.slice('--ref='.length);
    else if (a === '--force') opts.force = true;
    else if (!a.startsWith('-') && opts.dir == null) opts.dir = a;
    else rest.push(a);
  }
  opts.pass = rest.concat(right);
  if (opts.dir == null) opts.dir = process.env.AGENT_KB_DIR || DEFAULT_DIR;
  return opts;
}

function isSourceDir(dir) {
  return fs.existsSync(path.join(dir, 'memory_agent', 'pyproject.toml'));
}

function normalizeRepoUrl(url) {
  return String(url)
    .trim()
    .replace(/[/\\]+$/, '')
    .replace(/\.git$/i, '')
    .toLowerCase();
}

function defaultBranch(dir) {
  const sym = gitCapture(['symbolic-ref', '--short', 'refs/remotes/origin/HEAD'], dir);
  if (sym.ok && sym.stdout) return sym.stdout.replace(/^origin\//, '');
  const rev = gitCapture(['rev-parse', '--abbrev-ref', 'origin/HEAD'], dir);
  if (rev.ok && rev.stdout && rev.stdout !== 'origin/HEAD') {
    return rev.stdout.replace(/^origin\//, '');
  }
  return null;
}

function updateExisting(dir, opts) {
  if (!fs.existsSync(path.join(dir, '.git'))) {
    warn(`目标目录不是 git 仓库，跳过源码更新：${dir}`);
    return;
  }
  const remote = gitCapture(['remote', 'get-url', 'origin'], dir);
  if (remote.ok && normalizeRepoUrl(remote.stdout) !== normalizeRepoUrl(opts.repoUrl)) {
    if (!opts.force) {
      warn(`origin 与期望来源不一致（${remote.stdout}）；跳过更新，直接用现有源码（--force 可强制）。`);
      return;
    }
    warn('origin 与期望来源不一致，按 --force 继续更新。');
  }
  log('[source] git fetch origin --tags --prune');
  const fetched = gitCapture(['fetch', 'origin', '--tags', '--prune'], dir);
  if (!fetched.ok) warn(`git fetch 失败，沿用现有源码：${fetched.stderr.split('\n')[0] || ''}`);
  if (opts.ref) {
    log(`[source] git checkout ${opts.ref}`);
    const co = gitCapture(['checkout', opts.ref], dir);
    if (!co.ok) warn(`检出 ${opts.ref} 失败，沿用当前工作区：${co.stderr.split('\n')[0] || ''}`);
    return;
  }
  const branch = defaultBranch(dir) || 'master';
  log(`[source] git checkout ${branch} && git merge --ff-only origin/${branch}`);
  gitCapture(['checkout', branch], dir);
  const merged = gitCapture(['merge', '--ff-only', `origin/${branch}`], dir);
  if (!merged.ok) {
    warn(`未能快进到 origin/${branch}（可能已最新或有本地改动）：${merged.stderr.split('\n')[0] || ''}`);
  }
}

function cloneFresh(dir, opts) {
  fs.mkdirSync(path.dirname(path.resolve(dir)), { recursive: true });
  const ref = opts.ref;
  const isSha = !!ref && /^[0-9a-f]{7,40}$/i.test(ref);
  if (ref && !isSha) {
    log(`[source] git clone --depth 1 --branch ${ref} ${opts.repoUrl} ${dir}`);
    let code = gitVisible(['clone', '--depth', '1', '--branch', ref, opts.repoUrl, dir]);
    if (code !== 0) {
      warn(`--branch ${ref} 浅克隆失败，改用完整克隆后检出。`);
      fs.rmSync(dir, { recursive: true, force: true, maxRetries: 3, retryDelay: 200 });
      code = gitVisible(['clone', opts.repoUrl, dir]);
      if (code === 0) code = gitVisible(['checkout', ref], dir);
    }
    if (code !== 0) die(`克隆源码失败：${opts.repoUrl}`, code || 1);
    return;
  }
  log(`[source] git clone --depth 1 ${opts.repoUrl} ${dir}`);
  let code = gitVisible(['clone', '--depth', '1', opts.repoUrl, dir]);
  if (code === 0 && isSha) {
    gitCapture(['fetch', '--depth', '1', 'origin', ref], dir);
    code = gitVisible(['checkout', ref], dir);
  }
  if (code !== 0) die(`克隆源码失败：${opts.repoUrl}`, code || 1);
}

function ensureSource(dir, opts) {
  if (isSourceDir(dir)) {
    log(`[source] 复用已有源码：${dir}`);
    updateExisting(dir, opts);
    return;
  }
  if (fs.existsSync(dir)) {
    if (!fs.statSync(dir).isDirectory()) die(`目标路径已存在且不是目录：${dir}`);
    if (fs.readdirSync(dir).length > 0) {
      die(`目标目录非空且不是本仓库源码：${dir}\n  请换空目录，或删除后重装，或指向已有 clone。`);
    }
  }
  cloneFresh(dir, opts);
  if (!isSourceDir(dir)) die(`克隆后未找到 memory_agent/pyproject.toml：${dir}`);
}

function pythonBin() {
  return process.env.MEMORY_INSTALL_PYTHON || (IS_WINDOWS ? 'python' : 'python3');
}

function runInstall(dir, pass) {
  const py = pythonBin();
  const env = { ...process.env };
  env.PYTHONPATH = env.PYTHONPATH ? `${dir}${path.delimiter}${env.PYTHONPATH}` : dir;
  log(`[install] (cwd=${dir}) ${py} -m memory_agent.deploy install ${pass.join(' ')}`.trim());
  const r = spawnSync(py, ['-m', 'memory_agent.deploy', 'install', ...pass], {
    cwd: dir,
    env,
    stdio: 'inherit',
  });
  if (r.error) {
    if (r.error.code === 'ENOENT') {
      die(`找不到 Python（${py}）。装 Python >= 3.10，或用 MEMORY_INSTALL_PYTHON 指定解释器。`, 2);
    }
    die(`无法运行 ${py}：${r.error.message}`);
  }
  return r.status == null ? 1 : r.status;
}

function main() {
  const opts = parseArgs(process.argv.slice(2));
  if (opts.help) {
    process.stdout.write(HELP);
    return 0;
  }
  if (opts.version) {
    log(pkg.version);
    return 0;
  }
  if (!commandOk('git')) die('找不到 git（安装与记忆写入都需要）。请先安装 git。', 2);
  const py = pythonBin();
  if (!commandOk(py, ['--version'])) {
    die(`找不到 Python（${py}）。装 Python >= 3.10，或用 MEMORY_INSTALL_PYTHON 指定解释器。`, 2);
  }

  const dir = path.resolve(opts.dir);
  const dryRun = opts.pass.includes('--dry-run');

  log('== agent-kb / Agent-Knowledge-Base npx 安装器（#56）==');
  log(`   目标目录 : ${dir}`);
  log(`   源码     : ${opts.repoUrl}${opts.ref ? ` @ ${opts.ref}` : ''}`);
  log(`   模式     : ${dryRun ? 'dry-run（不落盘）' : 'install'}`);
  log(`   安装参数 : ${opts.pass.length ? opts.pass.join(' ') : '(默认)'}`);

  if (dryRun) {
    if (isSourceDir(dir)) return runInstall(dir, opts.pass);
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-kb-'));
    try {
      log(`[source] dry-run：克隆到临时目录 ${tmp}（结束后删除）`);
      cloneFresh(tmp, opts);
      return runInstall(tmp, opts.pass);
    } finally {
      fs.rmSync(tmp, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
    }
  }

  ensureSource(dir, opts);
  return runInstall(dir, opts.pass);
}

process.exit(main());
