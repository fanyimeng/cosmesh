import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { initializationText, handoffText } from '../lib/dsh.js';
import { createBridge, normalize } from '../lib/work-link.js';

const THREAD = 'W-00000000-0000-4000-8000-000000000001';
const OWNED_TEMP_DIRS = new Set();
const freshSignal = () => new AbortController().signal;

async function fixture(t) {
  const tmpParent = await fs.realpath(os.tmpdir());
  const prefix = path.join(tmpParent, 'cosmesh-dsh-bridge-test-');
  const root = await fs.mkdtemp(prefix);
  OWNED_TEMP_DIRS.add(root);
  t.after(async () => {
    // Delete only this fixture's own, resolved, absolute mkdtemp directory.
    assert.ok(OWNED_TEMP_DIRS.has(root), 'cleanup requires a directory created by this fixture');
    assert.ok(path.isAbsolute(root));
    assert.equal(path.dirname(root), tmpParent);
    assert.ok(root.startsWith(prefix) && root.length > prefix.length);
    assert.equal(await fs.realpath(root), root, 'refuse substituted/symlink cleanup roots');
    assert.ok((await fs.lstat(root)).isDirectory());
    assert.equal((await fs.lstat(root)).isSymbolicLink(), false);
    await fs.rm(root, { recursive: true, force: false });
    OWNED_TEMP_DIRS.delete(root);
  });
  const workdir = path.join(root, 'project');
  await fs.mkdir(workdir);
  return {
    root,
    workdir,
    args: {
      thread_id: THREAD,
      workdir,
      project_title: 'Scientific material workspace',
      session_title: 'Prepare nomination evidence',
      operation_key: 'create-nomination-session-1',
      initialize: false,
      handoff: 'Read the paper register. Do not publish or start research automatically.',
    },
  };
}

function fakeAdapter() {
  const calls = [];
  const workspaces = new Map();
  const sessions = new Map();
  const projectBindings = new Set();
  const sessionBindings = new Set();
  const records = [];
  const counts = { workspaceObjects: 0, sessionObjects: 0, prompts: 0 };
  const before = new Map();
  const after = new Map();
  const hooks = (map, name, action) => {
    if (!map.has(name)) map.set(name, []);
    map.get(name).push(action);
  };
  const wrap = (name, impl) => async (...args) => {
    calls.push({ name, args });
    args.at(-1)?.throwIfAborted?.();
    const pre = before.get(name)?.shift();
    if (pre) await pre(...args);
    const result = await impl(...args);
    const post = after.get(name)?.shift();
    if (post) await post(...args);
    return result;
  };
  const api = {
    initializationText, handoffText,
    owner: { agent: 'dsh', host: 'unit-test-host', profile: 'test-profile' },
    checkThread: wrap('checkThread', async id => {
      assert.equal(id, THREAD);
      return { owned: true, visibility: 'private' };
    }),
    ensureWorkspace: wrap('ensureWorkspace', async (root, title) => {
      const existing = [...workspaces.values()].find(w => w.path === root);
      if (existing) return { ...existing, created: false };
      const workspace = { id: `workspace-${++counts.workspaceObjects}`, path: root, title };
      workspaces.set(workspace.id, workspace);
      return { ...workspace, created: true };
    }),
    verifyWorkspace: wrap('verifyWorkspace', async (id, root) => {
      const workspace = workspaces.get(id);
      if (!workspace) throw new Error(`Recorded workspace is missing: ${id}`);
      if (workspace.path !== root) throw new Error('Recorded workspace path mismatch');
    }),
    findSession: wrap('findSession', async id => sessions.get(id)),
    createSession: wrap('createSession', async (workspaceId, id) => {
      assert.ok(workspaces.has(workspaceId));
      assert.equal(sessions.has(id), false, 'core must find a committed session before trying create again');
      counts.sessionObjects++;
      sessions.set(id, { id, workspaceId, cwd: workspaces.get(workspaceId).path });
      return id;
    }),
    verifySession: wrap('verifySession', async (id, workspaceId, root) => {
      const session = sessions.get(id);
      if (!session) throw new Error(`Recorded session is missing: ${id}`);
      if (session.workspaceId !== workspaceId || session.cwd !== root) throw new Error('Recorded session placement mismatch');
    }),
    renameSession: wrap('renameSession', async (id, title) => {
      assert.ok(sessions.has(id));
      sessions.get(id).title = title;
    }),
    bindProject: wrap('bindProject', async (threadId, id, title, root) => {
      projectBindings.add(JSON.stringify([threadId, id, title, root]));
    }),
    bindSession: wrap('bindSession', async (threadId, id, root) => {
      sessionBindings.add(JSON.stringify([threadId, id, root]));
    }),
    record: wrap('record', async (threadId, state, handoffPath, key) => {
      records.push({ threadId, state: structuredClone(state), handoffPath, key });
    }),
    initializeSession: wrap('initializeSession', async (sid, requestId, text) => {
      const s = sessions.get(sid);
      if (!s.init) { s.init = { requestId, text }; counts.prompts++; }
      assert.deepEqual(s.init, { requestId, text });
      return { completed: true, accepted: true, status: 'idle', visibleAsOrdinary: true };
    }),
    recordInitialization: wrap('recordInitialization', async () => {}),
    // A forbidden capability deliberately exists so accidental use is detected.
    prompt: wrap('prompt', async () => {
      counts.prompts++;
      throw new Error('The bridge must never prompt a session');
    }),
  };
  return {
    api, calls, counts, workspaces, sessions, projectBindings, sessionBindings, records,
    beforeOnce: (name, action) => hooks(before, name, action),
    afterOnce: (name, action) => hooks(after, name, action),
    failBefore: (name, message = `${name} failed before effect`) => hooks(before, name, () => { throw new Error(message); }),
    failAfter: (name, message = `${name} failed after effect`) => hooks(after, name, () => { throw new Error(message); }),
    callCount: name => calls.filter(call => call.name === name).length,
  };
}

async function assertAbsent(file) {
  await assert.rejects(fs.lstat(file), { code: 'ENOENT' });
}

async function assertNoLocks(dir) {
  for (const entry of await fs.readdir(dir, { withFileTypes: true })) {
    assert.equal(entry.name.endsWith('.lock'), false, `stale lock: ${entry.name}`);
    assert.equal(entry.name.endsWith('.tmp'), false, `stale journal temporary: ${entry.name}`);
    if (entry.isDirectory()) await assertNoLocks(path.join(dir, entry.name));
  }
}

function assertNoPrompt(fake, result) {
  assert.equal(fake.counts.prompts, 0);
  assert.equal(fake.callCount('prompt'), 0);
  assert.equal(result.researchStarted, false);
  assert.equal(result.published, false);
}

function assertSingleObjects(fake) {
  assert.equal(fake.counts.workspaceObjects, 1);
  assert.equal(fake.counts.sessionObjects, 1);
  assert.equal(fake.callCount('createSession'), 1);
}

test('default initialization runs once and migrates an existing create-only journal without new objects', async t => {
  const { args } = await fixture(t); const fake = fakeAdapter(); const bridge = createBridge(fake.api);
  const first = await bridge(args, freshSignal());
  const { initialize, ...defaultArgs } = args;
  const second = await bridge(defaultArgs, freshSignal());
  const third = await bridge(defaultArgs, freshSignal());
  assert.equal(second.status, 'initialized_waiting'); assert.equal(second.sessionId, first.sessionId);
  assert.equal(third.sessionId, first.sessionId); assertSingleObjects(fake);
  assert.equal(fake.counts.prompts, 1); assert.equal(fake.callCount('recordInitialization'), 1);
});

test('initialization acceptance followed by lost receipt reuses the same saved request and payload', async t => {
  const { args } = await fixture(t); const fake = fakeAdapter(); fake.failAfter('initializeSession');
  const bridge = createBridge(fake.api); const input = { ...args, initialize: true };
  const first = await bridge(input, freshSignal()); assert.equal(first.ok, false);
  const second = await bridge(input, freshSignal()); assert.equal(second.ok, true);
  assertSingleObjects(fake); assert.equal(fake.counts.prompts, 1);
  const requests = fake.calls.filter(c => c.name === 'initializeSession').map(c => c.args.slice(0,3));
  assert.deepEqual(requests[0], requests[1]);
});

test('accepted but incomplete initialization is partial, not success', async t => {
  const { args } = await fixture(t); const fake = fakeAdapter();
  fake.api.initializeSession = async () => ({ completed: false, accepted: true, status: 'timeout' });
  const result = await createBridge(fake.api)({ ...args, initialize: true }, freshSignal());
  assert.equal(result.ok, false); assert.equal(result.initialization.status, 'timeout');
  assert.equal(fake.callCount('recordInitialization'), 0);
});

test('normal creation persists IDs, bindings, private handoff and result without any prompt', async t => {
  const { args, workdir } = await fixture(t);
  const fake = fakeAdapter();
  const result = await createBridge(fake.api)(args, freshSignal());
  assert.equal(result.ok, true);
  assert.equal(result.status, 'created_only');
  assert.equal(result.threadId, THREAD);
  assert.equal(result.workdir, await fs.realpath(workdir));
  assert.equal(result.workspaceId, 'workspace-1');
  assert.match(result.sessionId, /^session-[0-9a-f-]{36}$/);
  assert.deepEqual(result.steps, {
    workspace: true, session: true, session_title: true,
    project_binding: true, session_binding: true, handoff: true, record: true,
  });
  assert.equal(fake.projectBindings.size, 1);
  assert.equal(fake.sessionBindings.size, 1);
  assert.equal(fake.records.length, 1);
  assert.equal(fake.sessions.get(result.sessionId).title, args.session_title);
  const handoff = await fs.readFile(result.handoffPath, 'utf8');
  assert.ok(handoff.includes(args.handoff));
  assert.ok(handoff.includes(THREAD));
  assert.ok(handoff.includes(result.workspaceId));
  assert.ok(handoff.includes(result.sessionId));
  const journal = JSON.parse(await fs.readFile(result.journal, 'utf8'));
  assert.equal(journal.format, 1);
  assert.equal(journal.sessionId, result.sessionId);
  assert.deepEqual(journal.steps, result.steps);
  assert.equal((await fs.stat(result.journal)).mode & 0o777, 0o600);
  assert.equal((await fs.stat(result.handoffPath)).mode & 0o777, 0o600);
  assert.equal(fake.calls[0].name, 'checkThread');
  assertSingleObjects(fake);
  assertNoPrompt(fake, result);
  await assertNoLocks(workdir);
});

test('same-key retry, including a new bridge instance, reuses objects and does not rewrite handoff', async t => {
  const { args, workdir } = await fixture(t);
  const fake = fakeAdapter();
  const bridge = createBridge(fake.api);
  const first = await bridge(args, freshSignal());
  const before = await fs.stat(first.handoffPath);
  const second = await bridge({ ...args }, freshSignal());
  const third = await createBridge(fake.api)({ ...args }, freshSignal());
  assert.equal(second.ok, true);
  assert.equal(third.ok, true);
  for (const result of [second, third]) {
    assert.equal(result.workspaceId, first.workspaceId);
    assert.equal(result.sessionId, first.sessionId);
    assert.equal(result.journal, first.journal);
  }
  assertSingleObjects(fake);
  for (const name of ['ensureWorkspace', 'renameSession', 'bindProject', 'bindSession', 'record']) assert.equal(fake.callCount(name), 1);
  assert.equal(fake.callCount('verifyWorkspace'), 3);
  assert.equal(fake.callCount('verifySession'), 3);
  assert.equal((await fs.stat(first.handoffPath)).mtimeMs, before.mtimeMs);
  assertNoPrompt(fake, third);
  await assertNoLocks(workdir);
});

for (const changed of [
  { project_title: 'Different project' },
  { session_title: 'Different session' },
  { handoff: 'Different material' },
  { create_directory: true },
]) {
  test(`same-key changed ${Object.keys(changed)[0]} returns conflict without mutation`, async t => {
    const { args, workdir } = await fixture(t);
    const fake = fakeAdapter();
    const bridge = createBridge(fake.api);
    const first = await bridge(args, freshSignal());
    const journal = await fs.readFile(first.journal, 'utf8');
    const handoff = await fs.readFile(first.handoffPath, 'utf8');
    const result = await bridge({ ...args, ...changed }, freshSignal());
    assert.equal(result.ok, false);
    assert.match(result.error, /different input/i);
    assert.equal(await fs.readFile(first.journal, 'utf8'), journal);
    assert.equal(await fs.readFile(first.handoffPath, 'utf8'), handoff);
    assertSingleObjects(fake);
    assert.equal(fake.callCount('record'), 1);
    assertNoPrompt(fake, result);
    await assertNoLocks(workdir);
  });
}

test('project binding failure resumes its failed step rather than creating replacement objects', async t => {
  const { args, workdir } = await fixture(t);
  const fake = fakeAdapter();
  fake.failBefore('bindProject');
  const first = await createBridge(fake.api)(args, freshSignal());
  assert.equal(first.ok, false);
  assert.equal(first.status, 'partial');
  assert.equal(first.steps.session_title, true);
  assert.equal(first.steps.project_binding, undefined);
  assert.equal(fake.callCount('bindSession'), 0);
  assert.equal(fake.callCount('record'), 0);
  const second = await createBridge(fake.api)(args, freshSignal());
  assert.equal(second.ok, true);
  assert.equal(second.workspaceId, first.workspaceId);
  assert.equal(second.sessionId, first.reservedSessionId);
  assertSingleObjects(fake);
  assert.equal(fake.callCount('bindProject'), 2);
  assert.equal(fake.callCount('renameSession'), 1);
  assert.equal(fake.projectBindings.size, 1);
  assert.equal(fake.sessionBindings.size, 1);
  assertNoPrompt(fake, second);
  await assertNoLocks(workdir);
});

test('session creation commits then throws: recovery looks up reserved ID and never calls create twice', async t => {
  const { args, workdir } = await fixture(t);
  const fake = fakeAdapter();
  fake.failAfter('createSession', 'Connection lost after Host committed Session');
  const first = await createBridge(fake.api)(args, freshSignal());
  assert.equal(first.ok, false);
  assert.equal(first.sessionId, null);
  assert.ok(fake.sessions.has(first.reservedSessionId));
  assert.equal(first.steps.session, undefined);
  const recoveryStart = fake.calls.length;
  const second = await createBridge(fake.api)(args, freshSignal());
  assert.equal(second.ok, true);
  assert.equal(second.sessionId, first.reservedSessionId);
  assertSingleObjects(fake);
  assert.equal(fake.callCount('findSession'), 2);
  const recovery = fake.calls.slice(recoveryStart);
  assert.ok(recovery.some(call => call.name === 'findSession' && call.args[0] === first.reservedSessionId));
  assert.equal(recovery.some(call => call.name === 'createSession'), false);
  assertNoPrompt(fake, second);
  await assertNoLocks(workdir);
});

test('concurrent same-key requests on one bridge serialize to one workspace and session', async t => {
  const { args, workdir } = await fixture(t);
  const fake = fakeAdapter();
  const bridge = createBridge(fake.api);
  const results = await Promise.all(Array.from({ length: 8 }, () => bridge({ ...args }, freshSignal())));
  assert.ok(results.every(result => result.ok));
  assert.equal(new Set(results.map(result => result.sessionId)).size, 1);
  assert.equal(new Set(results.map(result => result.workspaceId)).size, 1);
  assertSingleObjects(fake);
  assert.equal(fake.callCount('record'), 1);
  assertNoPrompt(fake, results[0]);
  await assertNoLocks(workdir);
});

test('concurrent separate bridge instances refuse an occupied same-key file lock then resume safely', async t => {
  const { args, workdir } = await fixture(t);
  const fake = fakeAdapter();
  let entered;
  let release;
  const entering = new Promise(resolve => { entered = resolve; });
  const gate = new Promise(resolve => { release = resolve; });
  fake.beforeOnce('ensureWorkspace', async () => { entered(); await gate; });
  const first = createBridge(fake.api)(args, freshSignal());
  await entering;
  try {
    await assert.rejects(createBridge(fake.api)(args, freshSignal()), /Operation locked/);
  } finally {
    release();
  }
  const result = await first;
  const retry = await createBridge(fake.api)(args, freshSignal());
  assert.equal(result.ok, true);
  assert.equal(retry.sessionId, result.sessionId);
  assertSingleObjects(fake);
  await assertNoLocks(workdir);
});

test('missing directory without create_directory fails without creating files or Host objects', async t => {
  const { args, root } = await fixture(t);
  const fake = fakeAdapter();
  const missing = path.join(root, 'missing');
  await assert.rejects(createBridge(fake.api)({ ...args, workdir: missing }, freshSignal()), { code: 'ENOENT' });
  await assertAbsent(missing);
  assert.deepEqual(fake.calls.map(call => call.name), ['checkThread']);
  assert.equal(fake.counts.workspaceObjects, 0);
  assert.equal(fake.counts.sessionObjects, 0);
});

test('explicit create_directory creates a missing nested directory after private Thread validation', async t => {
  const { args, root } = await fixture(t);
  const fake = fakeAdapter();
  const missing = path.join(root, 'new-project', 'nested');
  fake.beforeOnce('checkThread', () => assertAbsent(path.join(root, 'new-project')));
  const result = await createBridge(fake.api)({ ...args, workdir: missing, create_directory: true }, freshSignal());
  assert.equal(result.ok, true);
  assert.equal(result.workdir, await fs.realpath(missing));
  assertSingleObjects(fake);
  assertNoPrompt(fake, result);
});

for (const badThread of ['', 'Agenda', 'W-not-a-uuid', `W-${'a'.repeat(36)}`, `W-${'-'.repeat(36)}`]) {
  test(`invalid Thread ${JSON.stringify(badThread)} is rejected by normalize before adapter or file access`, async t => {
    const { args, workdir } = await fixture(t);
    const fake = fakeAdapter();
    assert.throws(() => normalize({ ...args, thread_id: badThread }), /Thread ID/i);
    await assert.rejects(createBridge(fake.api)({ ...args, thread_id: badThread }, freshSignal()), /Thread ID/i);
    assert.equal(fake.calls.length, 0);
    assert.deepEqual(await fs.readdir(workdir), []);
  });
}

test('relative workdir fails validation before any adapter calls', async t => {
  const { args, workdir } = await fixture(t);
  const fake = fakeAdapter();
  await assert.rejects(createBridge(fake.api)({ ...args, workdir: 'relative/project' }, freshSignal()), /absolute directory/);
  assert.equal(fake.calls.length, 0);
  assert.deepEqual(await fs.readdir(workdir), []);
});

test('a file workdir is rejected without creating a journal', async t => {
  const { args, root } = await fixture(t);
  const fake = fakeAdapter();
  const file = path.join(root, 'ordinary-file');
  await fs.writeFile(file, 'unchanged', { flag: 'wx' });
  await assert.rejects(createBridge(fake.api)({ ...args, workdir: file }, freshSignal()), /not a directory/);
  assert.equal(await fs.readFile(file, 'utf8'), 'unchanged');
  assert.deepEqual(fake.calls.map(call => call.name), ['checkThread']);
});

for (const createDirectory of [false, true]) {
  test(`private Thread validation refusal makes no filesystem changes (create_directory=${createDirectory})`, async t => {
    const { args, root, workdir } = await fixture(t);
    const fake = fakeAdapter();
    fake.failBefore('checkThread', 'Thread is not owned/private; access validation refused');
    const target = createDirectory ? path.join(root, 'must-not-be-created', 'nested') : workdir;
    const before = await fs.readdir(root);
    await assert.rejects(createBridge(fake.api)({ ...args, workdir: target, create_directory: createDirectory }, freshSignal()), /access validation refused/);
    assert.deepEqual(await fs.readdir(root), before);
    assert.deepEqual(await fs.readdir(workdir), []);
    if (createDirectory) await assertAbsent(path.join(root, 'must-not-be-created'));
    assert.deepEqual(fake.calls.map(call => call.name), ['checkThread']);
    assert.equal(fake.counts.workspaceObjects, 0);
    assert.equal(fake.counts.sessionObjects, 0);
  });
}

test('conflicting pre-existing handoff is not overwritten and prevents completion record', async t => {
  const { args, workdir } = await fixture(t);
  const fake = fakeAdapter();
  fake.failBefore('bindSession');
  const first = await createBridge(fake.api)(args, freshSignal());
  assert.equal(first.ok, false);
  const handoffPath = first.journal.replace(/\.json$/, '.handoff.md');
  const userText = 'Human-authored handoff: preserve exactly.\n';
  await fs.writeFile(handoffPath, userText, { flag: 'wx' });
  const second = await createBridge(fake.api)(args, freshSignal());
  assert.equal(second.ok, false);
  assert.match(second.error, /Existing handoff differs; not overwriting/);
  assert.equal(await fs.readFile(handoffPath, 'utf8'), userText);
  assert.equal(fake.callCount('record'), 0);
  assertSingleObjects(fake);
  assertNoPrompt(fake, second);
  await assertNoLocks(workdir);
});

test('journal symlink is refused before parsing or Host mutations', async t => {
  const { args, root, workdir } = await fixture(t);
  const fake = fakeAdapter();
  const digest = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
  const ownerDir = path.join(workdir, '.cosmesh-dsh', digest(fake.api.owner).slice(0, 20));
  await fs.mkdir(ownerDir, { recursive: true });
  const journalPath = path.join(ownerDir, `${digest([args.thread_id, args.operation_key])}.json`);
  const target = path.join(root, 'journal-link-target.json');
  const original = '{"must":"remain untouched"}\n';
  await fs.writeFile(target, original, { flag: 'wx' });
  await fs.symlink(target, journalPath);
  await assert.rejects(createBridge(fake.api)(args, freshSignal()), /Unsafe.*journal/i);
  assert.equal(await fs.readFile(target, 'utf8'), original);
  assert.equal((await fs.lstat(journalPath)).isSymbolicLink(), true);
  assert.deepEqual(fake.calls.map(call => call.name), ['checkThread']);
  await assertNoLocks(workdir);
});

test('handoff symlink is refused even when its target contains the exact expected handoff', async t => {
  const { args, root, workdir } = await fixture(t);
  const fake = fakeAdapter();
  fake.failBefore('bindSession');
  const first = await createBridge(fake.api)(args, freshSignal());
  assert.equal(first.ok, false);
  const handoffPath = first.journal.replace(/\.json$/, '.handoff.md');
  const expected = `# CosMesh → DSH 接续\n\nThread: ${args.thread_id}\nWorkspace: ${first.workspaceId}\nSession: ${first.reservedSessionId}\nDirectory: ${await fs.realpath(workdir)}\n\n/cosmesh 继续 Thread ${args.thread_id}。先读取本目录的项目说明和必要资料，不重复创建Thread。\n\n${args.handoff}\n\n此文件为私人接续材料，尚未发送给新Session，未启动模型工作。\n`;
  const target = path.join(root, 'handoff-link-target.md');
  await fs.writeFile(target, expected, { flag: 'wx' });
  await fs.symlink(target, handoffPath);
  const retry = await createBridge(fake.api)(args, freshSignal());
  assert.equal(retry.ok, false, 'a matching symlink target must not be treated as a safe handoff');
  assert.match(retry.error, /unsafe|symbolic|symlink/i);
  assert.equal((await fs.lstat(handoffPath)).isSymbolicLink(), true);
  assert.equal(await fs.readFile(target, 'utf8'), expected);
  assert.equal(fake.callCount('record'), 0);
  assertNoPrompt(fake, retry);
  await assertNoLocks(workdir);
});

test('completed handoff edited by a user is preserved on same-key retry', async t => {
  const { args } = await fixture(t);
  const fake = fakeAdapter();
  const first = await createBridge(fake.api)(args, freshSignal());
  const userText = 'Manual changes after initial completion.\n';
  await fs.writeFile(first.handoffPath, userText);
  const retry = await createBridge(fake.api)(args, freshSignal());
  assert.equal(retry.ok, true);
  assert.equal(await fs.readFile(first.handoffPath, 'utf8'), userText);
  assert.equal(fake.callCount('record'), 1);
  assertNoPrompt(fake, retry);
});

for (const missingKind of ['workspace', 'session']) {
  test(`recorded ${missingKind} missing on resume is reported, never silently recreated`, async t => {
    const { args, workdir } = await fixture(t);
    const fake = fakeAdapter();
    const first = await createBridge(fake.api)(args, freshSignal());
    if (missingKind === 'workspace') fake.workspaces.delete(first.workspaceId);
    else fake.sessions.delete(first.sessionId);
    const retry = await createBridge(fake.api)(args, freshSignal());
    assert.equal(retry.ok, false);
    assert.match(retry.error, new RegExp(`Recorded ${missingKind} is missing`));
    assert.equal(retry.workspaceId, first.workspaceId);
    assert.equal(retry.reservedSessionId, first.sessionId);
    assertSingleObjects(fake);
    assert.equal(fake.callCount('ensureWorkspace'), 1);
    assert.equal(fake.callCount('record'), 1);
    assertNoPrompt(fake, retry);
    await assertNoLocks(workdir);
  });
}

test('already-aborted signal stops before Thread check or filesystem modification', async t => {
  const { args, workdir } = await fixture(t);
  const fake = fakeAdapter();
  const controller = new AbortController();
  controller.abort(new Error('User cancelled before execution'));
  await assert.rejects(createBridge(fake.api)(args, controller.signal), /cancelled before execution/);
  assert.equal(fake.calls.length, 0);
  assert.deepEqual(await fs.readdir(workdir), []);
});

test('cancellation during Thread validation is observed before any directory creation', async t => {
  const { args, root } = await fixture(t);
  const fake = fakeAdapter();
  const controller = new AbortController();
  const missing = path.join(root, 'cancelled-before-create', 'nested');
  fake.afterOnce('checkThread', () => { controller.abort(new Error('Cancelled after Thread validation')); });
  // Permit either a rejection or a structured partial result, but never a new directory.
  try {
    const result = await createBridge(fake.api)({ ...args, workdir: missing, create_directory: true }, controller.signal);
    assert.equal(result.ok, false);
    assert.match(result.error, /Cancelled after Thread validation/);
  } catch (error) {
    assert.match(error.message, /Cancelled after Thread validation/);
  }
  await assertAbsent(path.join(root, 'cancelled-before-create'));
  assert.deepEqual(fake.calls.map(call => call.name), ['checkThread']);
});

test('cancellation at a partial binding stops later steps, releases lock and permits same-key resume', async t => {
  const { args, workdir } = await fixture(t);
  const fake = fakeAdapter();
  const controller = new AbortController();
  fake.beforeOnce('bindProject', () => {
    controller.abort(new Error('User cancelled at project binding'));
    controller.signal.throwIfAborted();
  });
  const first = await createBridge(fake.api)(args, controller.signal);
  assert.equal(first.ok, false);
  assert.match(first.error, /cancelled at project binding/);
  assert.equal(fake.callCount('bindSession'), 0);
  assert.equal(fake.callCount('record'), 0);
  await assertNoLocks(workdir);
  const resumed = await createBridge(fake.api)(args, freshSignal());
  assert.equal(resumed.ok, true);
  assert.equal(resumed.sessionId, first.reservedSessionId);
  assertSingleObjects(fake);
  assertNoPrompt(fake, resumed);
  await assertNoLocks(workdir);
});

test('a cancelled queued request does not execute after a preceding operation finishes', async t => {
  const { args, workdir } = await fixture(t);
  const fake = fakeAdapter();
  let entered;
  let release;
  const entering = new Promise(resolve => { entered = resolve; });
  const gate = new Promise(resolve => { release = resolve; });
  fake.beforeOnce('ensureWorkspace', async () => { entered(); await gate; });
  const bridge = createBridge(fake.api);
  const first = bridge(args, freshSignal());
  await entering;
  const controller = new AbortController();
  const second = bridge({ ...args }, controller.signal);
  const rejected = assert.rejects(second, /Cancelled while queued/);
  controller.abort(new Error('Cancelled while queued'));
  release();
  assert.equal((await first).ok, true);
  await rejected;
  assert.equal(fake.callCount('checkThread'), 1);
  assertSingleObjects(fake);
  await assertNoLocks(workdir);
});


test('legacy desktop journal path and fingerprint resume without replacement and scrub old replay', async t => {
  const { args, workdir } = await fixture(t); const fake = fakeAdapter();
  fake.api.owner = { host: 'synthetic-desktop-host', namespace: 'desktop' };
  const digest = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
  const first = await createBridge(fake.api)({ ...args, initialize: true }, freshSignal());
  const { initialize, ...input } = normalize(args);
  const expected = path.join(await fs.realpath(workdir), '.cosmesh-dsh', digest(fake.api.owner).slice(0, 20), `${digest([args.thread_id, args.operation_key])}.json`);
  assert.equal(first.journal, expected);
  const legacy = JSON.parse(await fs.readFile(expected, 'utf8'));
  assert.equal(legacy.fingerprint, digest({ ...input, workdir: await fs.realpath(workdir) }));
  legacy.initialization.outcome.output = { records: [{ stream: [['reasoning', 'synthetic-private-reasoning']] }] };
  legacy.initialization.outcome.replay = 'synthetic-replay';
  await fs.writeFile(expected, JSON.stringify(legacy));
  const retry = await createBridge(fake.api)({ ...args, initialize: true }, freshSignal());
  assert.equal(retry.sessionId, first.sessionId); assertSingleObjects(fake);
  assert.equal(fake.counts.prompts, 1);
  assert.equal('output' in retry.initialization, false);
  const saved = await fs.readFile(expected, 'utf8');
  assert.equal(saved.includes('synthetic-private-reasoning'), false);
  assert.equal(saved.includes('synthetic-replay'), false);
  assert.equal(JSON.parse(saved).fingerprint, legacy.fingerprint);
});

test('orchestrator persists only bounded initialization receipt even from an unsafe adapter', async t => {
  const { args } = await fixture(t); const fake = fakeAdapter();
  fake.api.initializeSession = async () => ({ completed: true, accepted: true, status: 'idle', acknowledgement: 'a'.repeat(5000), replySeq: 5, output: { records: ['synthetic-secret-stream'] }, replay: ['synthetic-replay'] });
  const result = await createBridge(fake.api)({ ...args, initialize: true }, freshSignal());
  assert.equal(result.ok, true);
  assert.equal(result.initialization.acknowledgement.length, 512);
  assert.equal(result.initialization.replySeq, 5);
  assert.equal('output' in result.initialization, false);
  const journal = await fs.readFile(result.journal, 'utf8');
  assert.equal(journal.includes('synthetic-secret-stream'), false);
  assert.equal(journal.includes('synthetic-replay'), false);
});
