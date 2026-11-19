import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import { apply, validateConfig } from '../index.js';
import { hostAdapter } from '../lib/dsh.js';
import { cosmeshAdapter, runCosmesh } from '../lib/cosmesh.js';

const packageRoot = fileURLToPath(new URL('../', import.meta.url));
async function temporary(t) {
  const parent = await fs.realpath(os.tmpdir());
  const prefix = path.join(parent, 'connector-synthetic-');
  const dir = await fs.mkdtemp(prefix);
  t.after(async () => {
    assert.equal(await fs.realpath(dir), dir);
    assert.equal(path.dirname(dir), parent);
    assert.ok(dir.startsWith(prefix) && dir.length > prefix.length);
    assert.equal((await fs.lstat(dir)).isSymbolicLink(), false);
    await fs.rm(dir, { recursive: true });
  });
  return dir;
}
const signal = () => new AbortController().signal;

// POSIX shebang paths cannot contain spaces; quote both runtime and payload in a trampoline.
async function syntheticExecutable(cli, source) {
  const script = `${cli}.mjs`;
  const quote = value => "'" + value.replaceAll("'", "'\\''") + "'";
  await fs.writeFile(script, source, { mode: 0o600 });
  await fs.writeFile(cli, `#!/bin/sh\nexec ${quote(process.execPath)} ${quote(script)} "$@"\n`, { mode: 0o700 });
}

test('default namespace preserves legacy owner and CLI path', () => {
  const config = validateConfig();
  assert.deepEqual(config, { namespace: 'desktop', cliPath: path.join(os.homedir(), '.local/opt/cosmesh/bin/cosmesh') });
  assert.deepEqual(hostAdapter({}).owner, { host: os.hostname(), namespace: 'desktop' });
  assert.deepEqual(hostAdapter({}, 'custom').owner, { host: os.hostname(), namespace: 'custom' });
});

test('configuration fails closed before tool registration; executable is never a tool argument', () => {
  for (const value of [null, [], 4, { unknown: true }, { namespace: null }, { namespace: '../bad' }, { namespace: '' }, { cliPath: null }, { cliPath: 3 }, { cliPath: 'cosmesh' }, { cliPath: '~/bin/cosmesh' }, { cliPath: '/bin/x\n--arg' }, { cliPath: '/bin/x\0' }]) {
    assert.throws(() => apply({ effect: () => assert.fail('registered invalid config') }, value));
  }
  assert.equal(validateConfig({ cliPath: '/opt/synthetic tools/cosmesh' }).cliPath, '/opt/synthetic tools/cosmesh');
  let tool, disposed = 0;
  const cleanup = [];
  apply({ effect: fn => cleanup.push(fn()), tools: { register: value => { tool = value; return () => disposed++; } } });
  assert.equal('cliPath' in tool.parameters.properties, false);
  assert.equal('command' in tool.parameters.properties, false);
  cleanup.forEach(fn => fn());
  assert.equal(disposed, 1);
});

test('configured synthetic executable receives literal argv, JSON mode and private-only mutations', async t => {
  const dir = await temporary(t);
  const cli = path.join(dir, "synthetic ' cosmesh");
  const id = 'W-00000000-0000-4000-8000-000000000001';
  await syntheticExecutable(cli, `const a=process.argv.slice(2);console.log(JSON.stringify({ok:true,data:a[1]==='show'?{thread:{id:a[2],source:{repo:'local'},visibility:'private',lifecycle:'active'}}:{args:a}}));\n`);
  const api = cosmeshAdapter(cli);
  await api.checkThread(id, signal());
  const literal = '$(not-a-command); synthetic title';
  const bound = await api.bindProject(id, 'workspace-synthetic', literal, dir, signal());
  assert.ok(bound.args.includes(literal));
  assert.equal(bound.args.at(-1), '--json');
  const state = { sessionId: 'session-synthetic', workspaceId: 'workspace-synthetic', projectTitle: literal, input: { workdir: dir }, initialization: { requestId: 'synthetic-request' } };
  for (const result of [await api.record(id, state, '/synthetic/handoff', 'synthetic-key', signal()), await api.recordInitialization(id, state, 'synthetic-key', signal())]) {
    assert.ok(result.args.includes('--local-only'));
    assert.equal(result.args.includes('--sync'), false);
    assert.ok(result.args.includes('--idempotency-key'));
    const body = result.args[result.args.indexOf('--body') + 1];
    for (const privateValue of [state.sessionId, state.workspaceId, state.projectTitle, dir, '/synthetic/handoff', state.initialization.requestId]) {
      assert.equal(body.includes(privateValue), false, 'execution coordinates must never enter publishable records');
    }
    const key = result.args[result.args.indexOf('--idempotency-key') + 1];
    assert.match(key, /^dsh-(bridge|init)-v2-synthetic-key$/);

  }
});

test('CLI rejects public or inactive Thread and does not echo malformed stdout', async t => {
  const dir = await temporary(t);
  for (const [label, data, expected] of [
    ['public', { thread: { id: 'synthetic', source: { repo: 'local' }, visibility: 'public', lifecycle: 'active' } }, /private/],
    ['inactive', { thread: { id: 'synthetic', source: { repo: 'local' }, visibility: 'private', lifecycle: 'archived' } }, /not active/],
  ]) {
    const cli = path.join(dir, label);
    await syntheticExecutable(cli, `console.log(${JSON.stringify(JSON.stringify({ ok: true, data }))});\n`);
    await assert.rejects(cosmeshAdapter(cli).checkThread('synthetic', signal()), expected);
  }
  const cli = path.join(dir, 'malformed');
  await syntheticExecutable(cli, `console.log('synthetic-private-stdout');\n`);
  await assert.rejects(runCosmesh(cli, [], signal()), error => /invalid JSON/.test(error.message) && !error.message.includes('synthetic-private-stdout'));
});

test('npm pack is self-contained and root, locale and icon exports resolve without DSH installation', async t => {
  const dir = await temporary(t);
  const packed = JSON.parse(execFileSync('npm', ['pack', '--offline', '--ignore-scripts', '--json', '--cache', path.join(dir, 'cache'), '--pack-destination', dir], { cwd: packageRoot, encoding: 'utf8' }))[0];
  const files = packed.files.map(f => f.path).sort();
  assert.deepEqual(files, ['README.md', 'cordis.patch.yml', 'icon.svg', 'index.js', 'lib/cosmesh.js', 'lib/dsh.js', 'lib/work-link.js', 'locale/en.json', 'locale/zh.json', 'package.json'].sort());
  const installed = path.join(dir, 'node_modules', '@cosmesh', 'dsh-connector');
  await fs.mkdir(installed, { recursive: true });
  const archives = (await fs.readdir(dir)).filter(name => name.endsWith('.tgz'));
  assert.equal(archives.length, 1);
  execFileSync('tar', ['-xzf', path.join(dir, archives[0]), '--strip-components=1', '-C', installed]);
  const manifest = JSON.parse(await fs.readFile(path.join(installed, 'package.json'), 'utf8'));
  assert.equal(manifest.name, '@cosmesh/dsh-connector');
  assert.equal(manifest.version, JSON.parse(await fs.readFile(path.join(packageRoot, 'package.json'), 'utf8')).version);
  assert.equal(manifest.private, true);
  const result = execFileSync(process.execPath, ['--input-type=module', '-e', `import {apply} from '@cosmesh/dsh-connector'; import fs from 'node:fs'; import {fileURLToPath} from 'node:url'; for (const p of ['@cosmesh/dsh-connector/locale/en.json','@cosmesh/dsh-connector/locale/zh.json']) { const j=JSON.parse(fs.readFileSync(fileURLToPath(import.meta.resolve(p)))); if(!j.meta.title||!j.meta.description) throw Error('locale'); } const svg=fs.readFileSync(fileURLToPath(import.meta.resolve('@cosmesh/dsh-connector/icon')),'utf8'); if(!svg.includes('<svg')) throw Error('icon'); if(typeof apply!=='function') throw Error('root'); console.log('self-contained exports OK');`], { cwd: dir, encoding: 'utf8' });
  assert.match(result, /self-contained exports OK/);
});
