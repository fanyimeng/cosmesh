import { createHash, randomUUID } from 'node:crypto';
import * as fs from 'node:fs/promises';
import path from 'node:path';

const digest = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
export function normalize(args) {
  if (!args || typeof args !== 'object' || Array.isArray(args)) throw new Error('Arguments must be an object.');
  const allowed = ['thread_id', 'workdir', 'project_title', 'session_title', 'operation_key', 'handoff', 'create_directory', 'initialize'];
  if (Object.keys(args).some(k => !allowed.includes(k))) throw new Error('Unknown argument.');
  if (args.initialize !== undefined && typeof args.initialize !== 'boolean') throw new Error('initialize must be boolean.');
  if (args.create_directory !== undefined && typeof args.create_directory !== 'boolean') throw new Error('create_directory must be boolean.');
  if (typeof args.thread_id !== 'string' || typeof args.workdir !== 'string') throw new Error('thread_id and workdir must be strings.');
  if (!/^W-[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(args.thread_id ?? '')) throw new Error('A stable W-UUID Thread ID is required.');
  if (!path.isAbsolute(args.workdir ?? '')) throw new Error('workdir must be an absolute directory.');
  for (const k of ['project_title', 'session_title', 'operation_key']) {
    if (typeof args[k] !== 'string' || !args[k].trim() || args[k].length > 160) throw new Error(`${k} must contain 1–160 characters.`);
  }
  if (args.handoff !== undefined && (typeof args.handoff !== 'string' || args.handoff.length > 12000)) throw new Error('handoff must be at most 12000 characters.');
  return { thread_id: args.thread_id, workdir: path.resolve(args.workdir), project_title: args.project_title,
    session_title: args.session_title, operation_key: args.operation_key, handoff: args.handoff ?? '', create_directory: args.create_directory === true,
    initialize: args.initialize !== false };
}

// Persist and return a small receipt, never a model stream/replay (including legacy outcomes).
export function boundedReceipt(value) {
  const result = {};
  if (!value || typeof value !== 'object') return result;
  for (const k of ['completed', 'accepted', 'consumed', 'started', 'ended', 'hasReply', 'visibleAsOrdinary', 'running']) {
    if (typeof value[k] === 'boolean') result[k] = value[k];
  }
  for (const k of ['status', 'endReason', 'acknowledgement']) {
    if (typeof value[k] === 'string') result[k] = value[k].slice(0, k === 'acknowledgement' ? 512 : 80);
    else if (value[k] === null) result[k] = null;
  }
  for (const k of ['inputSeq', 'replySeq', 'endSeq']) {
    if (value[k] === null || (Number.isSafeInteger(value[k]) && value[k] >= 0)) result[k] = value[k];
  }
  return result;
}

export function createBridge(adapter) {
  // One Host mutation queue; filesystem lock also refuses another process using this operation.
  let queue = Promise.resolve();
  return (args, signal) => {
    const task = queue.then(() => run(normalize(args), adapter, signal));
    queue = task.catch(() => {});
    return task;
  };
}

async function privateDirectory(dir) {
  await fs.mkdir(dir, { recursive: true, mode: 0o700 });
  const stat = await fs.lstat(dir);
  if (!stat.isDirectory() || stat.isSymbolicLink()) throw new Error(`Refusing unsafe journal directory: ${dir}`);
}

async function run(args, api, signal) {
  // Initialization is an additive action, not part of creation identity: v1 journals keep their exact fingerprint.
  const { initialize, ...input } = args;
  signal.throwIfAborted();
  // Validate Thread before creating any directories or Host objects. No public mutations.
  await api.checkThread(input.thread_id, signal);
  signal.throwIfAborted();
  if (input.create_directory) await fs.mkdir(input.workdir, { recursive: true });
  const root = await fs.realpath(input.workdir);
  if (!(await fs.stat(root)).isDirectory()) throw new Error('workdir is not a directory.');
  const dataDir = path.join(root, '.cosmesh-dsh');
  await privateDirectory(dataDir);
  const ownerDir = path.join(dataDir, digest(api.owner).slice(0, 20));
  await privateDirectory(ownerDir);
  const key = digest([input.thread_id, input.operation_key]);
  const journal = path.join(ownerDir, `${key}.json`);
  const lock = `${journal}.lock`;
  let handle;
  try { handle = await fs.open(lock, 'wx', 0o600); }
  catch (e) { if (e.code === 'EEXIST') throw new Error(`Operation locked: ${lock}. Do not retry creation with a new key. Check the owning process before manually removing a stale lock.`); throw e; }
  await handle.writeFile(JSON.stringify({ pid: process.pid, owner: api.owner }));
  let state;
  const save = async () => {
    const temp = `${journal}.${randomUUID()}.tmp`;
    await fs.writeFile(temp, JSON.stringify(state, null, 2) + '\n', { flag: 'wx', mode: 0o600, flush: true });
    await fs.rename(temp, journal);
  };
  try {
    try {
      const st = await fs.lstat(journal);
      if (!st.isFile() || st.isSymbolicLink() || st.size > 100000) throw new Error('Unsafe or oversized operation journal.');
      state = JSON.parse(await fs.readFile(journal, 'utf8'));
    } catch (e) { if (e.code !== 'ENOENT') throw e; }
    const fingerprint = digest({ ...input, workdir: root });
    if (state && (state.format !== 1 || state.fingerprint !== fingerprint)) throw new Error('Operation key already used with different input. Reuse the original arguments.');
    if (state?.initialization?.outcome) {
      const receipt = boundedReceipt(state.initialization.outcome);
      if (JSON.stringify(receipt) !== JSON.stringify(state.initialization.outcome)) {
        state.initialization.outcome = receipt;
        await save();
      }
    }
    if (!state) {
      state = { format: 1, fingerprint, input: { ...input, workdir: root }, owner: api.owner,
        sessionId: `session-${randomUUID()}`, workspaceId: null, steps: {}, createdAt: new Date().toISOString() };
      await save();
    }
    const step = async (name, operation) => {
      signal.throwIfAborted();
      if (state.steps[name]) return;
      await operation();
      state.steps[name] = true;
      await save();
    };
    await step('workspace', async () => {
      const w = await api.ensureWorkspace(root, input.project_title, signal);
      state.workspaceId = w.id; state.projectTitle = w.title; state.workspaceCreated = w.created;
    });
    // Every resume checks real Host metadata. Never replace a missing/deleted workspace silently.
    await api.verifyWorkspace(state.workspaceId, root, signal);
    await step('session', async () => {
      const existing = await api.findSession(state.sessionId, signal);
      if (existing) await api.verifySession(state.sessionId, state.workspaceId, root, signal);
      else {
        const result = await api.createSession(state.workspaceId, state.sessionId, signal);
        if (result !== state.sessionId) throw new Error(`Host returned unexpected Session ${result}; stop and inspect before retry.`);
      }
    });
    await api.verifySession(state.sessionId, state.workspaceId, root, signal);
    await step('session_title', () => api.renameSession(state.sessionId, input.session_title, signal));
    await step('project_binding', () => api.bindProject(input.thread_id, state.workspaceId, state.projectTitle, root, signal));
    await step('session_binding', () => api.bindSession(input.thread_id, state.sessionId, root, signal));
    // Handoff is a local file only: no prompt, inbox injection or model call.
    const handoffPath = path.join(ownerDir, `${key}.handoff.md`);
    await step('handoff', async () => {
      const text = api.handoffText(state);
      try { await fs.writeFile(handoffPath, text, { flag: 'wx', mode: 0o600 }); }
      catch (e) {
        if (e.code !== 'EEXIST') throw e;
        const st = await fs.lstat(handoffPath);
        if (!st.isFile() || st.isSymbolicLink() || st.size > 20000) throw new Error('Unsafe existing handoff; not reading or overwriting.');
        if ((await fs.readFile(handoffPath, 'utf8')) !== text) throw new Error('Existing handoff differs; not overwriting.');
      }
    });
    await step('record', () => api.record(input.thread_id, state, handoffPath, key, signal));
    if (initialize) {
      if (!state.initialization) {
        state.initialization = { requestId: `cosmesh-init-${digest([key, state.sessionId])}`, text: api.initializationText(state) };
        await save();
      }
      if (!state.steps.initialization) {
        const outcome = boundedReceipt(await api.initializeSession(state.sessionId, state.initialization.requestId, state.initialization.text, signal));
        state.initialization.outcome = outcome;
        state.steps.initialization = outcome.completed === true;
        await save();
        if (!outcome.completed) throw new Error(`Initialization ${outcome.status}; accepted is not completed. Retry original key to inspect, never replace the request ID.`);
      }
      await step('initialization_record', () => api.recordInitialization(input.thread_id, state, key, signal));
    }
    return { ok: true, status: initialize ? 'initialized_waiting' : 'created_only', operationKey: input.operation_key, threadId: input.thread_id, workspaceId: state.workspaceId,
      projectTitle: state.projectTitle, sessionId: state.sessionId, workdir: root, journal, handoffPath,
      initialization: state.initialization?.outcome ? boundedReceipt(state.initialization.outcome) : null, steps: state.steps, researchStarted: false, published: false };
  } catch (e) {
    if (!state) throw e;
    return { ok: false, status: 'partial', operationKey: input.operation_key, error: String(e.message ?? e).slice(0, 1500), threadId: input.thread_id,
      workspaceId: state.workspaceId, sessionId: state.steps?.session ? state.sessionId : null,
      reservedSessionId: state.sessionId, steps: state.steps, journal, initialization: state.initialization?.outcome ? boundedReceipt(state.initialization.outcome) : null, researchStarted: false, published: false,
      next: 'Retry the same operation_key and original arguments; do not create a replacement.' };
  } finally {
    await handle.close();
    // Exact lock path created by this invocation; never touch another operation's journal.
    await fs.unlink(lock);
  }
}
