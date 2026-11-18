import test from 'node:test';
import assert from 'node:assert/strict';
import { hostAdapter } from '../lib/dsh.js';
import { tmpdir } from 'node:os';
import { realpath } from 'node:fs/promises';
const signal = new AbortController().signal;
const root = await realpath(tmpdir());

function fixture() {
  const calls = []; const workspace = { id: 'w1', path: root, title: 'Existing', sessionIds: ['s1'] };
  const live = { header: { cwd: root, origin: 'ordinary' } };
  const ctx = {
    workspaceRegistry: { list: () => [workspace], get: id => id === 'w1' ? workspace : undefined,
      archivedSessionIds: [], create: async (...args) => { calls.push(['createWorkspace', ...args]); return workspace; } },
    sessions: { get: id => id === 's1' ? live : undefined, flush: async s => { calls.push(['flush', s]); return true; } },
    sessionPersistence: { stat: async id => id === 's1' ? { header: live.header } : undefined },
    sessionController: { create: async request => { calls.push(['createSession', request]); return { sessionId: 's1' }; },
      rename: async request => { calls.push(['rename', request]); } },
  };
  return { ctx, calls, api: hostAdapter(ctx, 'tests') };
}
test('adapter uses registry entities and preserves existing title', async () => {
  const { api } = fixture();
  assert.deepEqual(await api.ensureWorkspace(root, 'Requested', signal), { id: 'w1', title: 'Existing', created: false });
  await api.verifyWorkspace('w1', root, signal);
  await assert.rejects(api.verifyWorkspace('missing', root, signal));
});
test('create and rename use Host controllers, checkpoint persistence, never prompt', async () => {
  const { api, calls } = fixture();
  assert.equal(await api.createSession('w1', 's1', signal), 's1');
  await api.renameSession('s1', 'Title', signal);
  assert.deepEqual(calls.map(x => x[0]), ['createSession', 'flush', 'rename', 'flush']);
});
test('Session verification rejects archived, wrong membership and subagents', async () => {
  const { ctx, api } = fixture();
  await api.verifySession('s1', 'w1', root, signal);
  ctx.workspaceRegistry.archivedSessionIds = ['s1'];
  await assert.rejects(api.verifySession('s1', 'w1', root, signal), /archived/);
  ctx.workspaceRegistry.archivedSessionIds = [];
  ctx.sessions.get('s1').header.origin = 'subagent';
  await assert.rejects(api.verifySession('s1', 'w1', root, signal), /identity/);
});
test('missing durable checkpoint fails rather than claiming success', async () => {
  const { ctx, api } = fixture();
  ctx.sessionPersistence.stat = async () => undefined;
  await assert.rejects(api.createSession('w1', 's1', signal), /durability/);
});
