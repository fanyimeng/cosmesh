import test from 'node:test';
import assert from 'node:assert/strict';
import { createInitializer, initializationEvidence, initializationText } from '../lib/dsh.js';

const SID = 'session-init-target';
const OTHER = 'session-unrelated';
const REQUEST = 'initialize-request-1';
const signal = () => new AbortController().signal;
const TEXT = '本轮只确认接续，然后等待。';

function fixture(options = {}) {
  let seq = 0;
  let blank = options.blank ?? true;
  let running = false;
  const logs = [];
  const listeners = new Set();
  const guards = new Set();
  const admitted = new Set();
  const calls = { events: [], projections: [], state: [], prompt: [], wait: [], flush: [], guardDisposals: 0, listenerDisposals: 0 };
  const hooks = {};
  const context = {
    tools: {
      guard(fn) {
        guards.add(fn);
        let disposed = false;
        return () => { if (!disposed) { disposed = true; guards.delete(fn); calls.guardDisposals++; } };
      },
    },
    on(name, listener) {
      assert.equal(name, 'session/event');
      listeners.add(listener);
      let disposed = false;
      return () => { if (!disposed) { disposed = true; listeners.delete(listener); calls.listenerDisposals++; } };
    },
    sessionController: {
      async events(request, abortSignal) {
        calls.events.push(request);
        abortSignal?.throwIfAborted();
        assert.deepEqual(request.address, { kind: 'session', sessionId: SID });
        if (hooks.events) await hooks.events(request);
        return { records: logs.map(event => ({ type: 'event', event: structuredClone(event) })), hasMore: false, cursor: seq };
      },
      async projections(request, abortSignal) {
        calls.projections.push(request);
        abortSignal?.throwIfAborted();
        assert.equal(request.sessionId, SID);
        return options.unknownProjection ? { kind: 'unavailable' } : { kind: 'sequenced', seq, values: { sessionListMetadata: { blank, lastPromptAt: null } } };
      },
      async state(request, abortSignal) {
        calls.state.push(request);
        abortSignal?.throwIfAborted();
        if (hooks.state) await hooks.state(request);
        return state();
      },
      async prompt(request, abortSignal) {
        calls.prompt.push(structuredClone(request));
        abortSignal?.throwIfAborted();
        assert.equal(request.sessionId, SID);
        assert.equal(request.mode, 'queue');
        if (hooks.beforePrompt) await hooks.beforePrompt(request);
        const duplicate = admitted.has(request.requestId);
        if (!duplicate) {
          admitted.add(request.requestId);
          emit('agent/inbox/spliced', { inserted: [{ id: `message-${request.requestId}`, role: 'user', content: request.content, source: { kind: 'user', rpcId: request.requestId } }] });
        }
        if (hooks.afterPrompt) await hooks.afterPrompt(request);
        return { accepted: true, duplicate };
      },
      async wait(request, abortSignal) {
        calls.wait.push(request);
        abortSignal?.throwIfAborted();
        assert.equal(request.sessionId, SID);
        assert.equal(request.timeoutMs, 30000);
        assert.equal(request.maxMessages, 1);
        if (hooks.wait) return hooks.wait(request);
        complete(REQUEST);
        return waitValue('idle');
      },
    },
  };
  const state = () => ({ cursor: seq, active: true, running, queue: running ? [] : null, pendingInput: [] });
  const waitValue = reason => ({ reason, state: state(), output: { records: logs.filter(e => e.type === 'assistant/message').map(event => ({ type: 'event', event })), truncated: false } });
  function emit(type, data, sid = SID) {
    const event = { type, data, seq: ++seq, time: 1000 + seq };
    if (sid === SID) {
      logs.push(event);
      if (type === 'turn/start') { blank = false; running = true; }
      if (type === 'turn/end') running = false;
    }
    for (const listener of [...listeners]) listener({ id: sid }, event);
    return event;
  }
  function consume(requestId = REQUEST) {
    emit('turn/start', { turn: 1 });
    emit('user/message', { id: `message-${requestId}`, role: 'user', content: [{ type: 'text', text: TEXT }], source: { kind: 'user', rpcId: requestId } });
  }
  function complete(requestId = REQUEST, reason = { kind: 'completed' }, reply = true) {
    if (!logs.some(e => e.type === 'user/message' && e.data.source?.rpcId === requestId)) consume(requestId);
    if (reply) emit('assistant/message', { message: { role: 'assistant', content: [{ type: 'text', text: '已接续，等待下一步指令。' }] } });
    emit('turn/end', { reason });
  }
  const flush = async sid => { assert.equal(sid, SID); calls.flush.push(sid); if (hooks.flush) await hooks.flush(); };
  const initialize = createInitializer(context, flush);
  const blocked = (sid = SID) => [...guards].map(guard => guard({ agent: { session: { id: sid } } })).filter(value => value !== undefined);
  return { initialize, context, calls, hooks, logs, guards, listeners, admitted, emit, consume, complete, state, waitValue, blocked };
}

function assertReleased(f) {
  assert.equal(f.guards.size, 0, 'initialization-only tool guard must be released');
  assert.equal(f.listeners.size, 0, 'session/event listener must be released');
}

test('normal initialization completes on actual reason.kind, flushes, and disposes guard', async () => {
  const f = fixture();
  const result = await f.initialize(SID, REQUEST, TEXT, signal());
  assert.equal(result.completed, true);
  assert.equal(result.accepted, true);
  assert.equal(result.consumed, true);
  assert.equal(result.started, true);
  assert.equal(result.ended, true);
  assert.equal(result.hasReply, true);
  assert.equal(result.acknowledgement, '已接续，等待下一步指令。');
  assert.equal(result.endReason, 'completed');
  assert.equal(result.status, 'idle');
  assert.equal(result.visibleAsOrdinary, true);
  assert.equal(result.running, false);
  assert.equal(f.calls.prompt.length, 1);
  assert.equal(f.calls.wait[0].afterSeq, 0);
  assert.equal(f.calls.flush.length, 2);
  assertReleased(f);
});

for (const reason of [{ kind: 'failed', error: 'model unavailable' }, { kind: 'cancelled' }, 'completed']) {
  test(`only reason.kind completed establishes completion: ${JSON.stringify(reason)}`, async () => {
    const f = fixture();
    f.hooks.wait = async () => { f.complete(REQUEST, reason); return f.waitValue('idle'); };
    const result = await f.initialize(SID, REQUEST, TEXT, signal());
    assert.equal(result.accepted, true);
    assert.equal(result.completed, false);
    assert.equal(result.endReason, typeof reason === 'object' ? reason.kind : null);
    assertReleased(f);
  });
}

test('ended completed turn without assistant reply is not reported completed', async () => {
  const f = fixture();
  f.hooks.wait = async () => { f.complete(REQUEST, { kind: 'completed' }, false); return f.waitValue('idle'); };
  const result = await f.initialize(SID, REQUEST, TEXT, signal());
  assert.equal(result.ended, true);
  assert.equal(result.hasReply, false);
  assert.equal(result.completed, false);
  assertReleased(f);
});

test('accepted is not completed; timeout keeps guard until matching consumed turn/end', async () => {
  const f = fixture();
  f.hooks.wait = async () => f.waitValue('timeout');
  const result = await f.initialize(SID, REQUEST, TEXT, signal());
  assert.equal(result.accepted, true);
  assert.equal(result.completed, false);
  assert.equal(result.started, false);
  assert.equal(result.status, 'timeout');
  assert.equal(result.visibleAsOrdinary, false);
  assert.equal(f.guards.size, 1);
  // An unrelated Session ending must not release the target's guard.
  f.emit('turn/end', { reason: { kind: 'completed' } }, OTHER);
  assert.equal(f.guards.size, 1);
  // Nor may an unrelated queued request in the target release it before ours is consumed.
  f.consume('another-request');
  f.emit('turn/end', { reason: { kind: 'completed' } });
  assert.equal(f.guards.size, 1);
  f.consume();
  f.emit('turn/end', { reason: { kind: 'completed' } });
  assertReleased(f);
});

test('started timeout keeps target protected until its turn actually ends', async () => {
  const f = fixture();
  f.hooks.wait = async () => { f.consume(); return f.waitValue('timeout'); };
  const result = await f.initialize(SID, REQUEST, TEXT, signal());
  assert.equal(result.started, true);
  assert.equal(result.completed, false);
  assert.equal(result.running, true);
  assert.equal(result.visibleAsOrdinary, true);
  assert.equal(f.blocked().length, 1);
  f.complete();
  assertReleased(f);
});

test('lost prompt receipt after acceptance does not remove protection', async () => {
  const f = fixture();
  f.hooks.afterPrompt = async () => { throw new Error('lost accepted receipt'); };
  await assert.rejects(f.initialize(SID, REQUEST, TEXT, signal()), /lost accepted receipt/);
  assert.equal(f.admitted.size, 1);
  assert.equal(f.blocked().length, 1);
  assert.equal(f.calls.wait.length, 0);
  f.complete();
  assertReleased(f);
});

test('prompt error before acceptance releases guard and listener', async () => {
  const f = fixture();
  f.hooks.beforePrompt = async () => { throw new Error('admission failed'); };
  await assert.rejects(f.initialize(SID, REQUEST, TEXT, signal()), /admission failed/);
  assert.equal(f.admitted.size, 0);
  assertReleased(f);
});

test('explicit accepted:false response releases protection when no input was admitted', async () => {
  const f = fixture();
  f.context.sessionController.prompt = async () => ({ accepted: false });
  await assert.rejects(f.initialize(SID, REQUEST, TEXT, signal()), /did not accept/);
  assert.equal(f.admitted.size, 0);
  assertReleased(f);
});

test('state error before any prompt acceptance also releases guard', async () => {
  const f = fixture();
  f.hooks.state = async () => { throw new Error('state unavailable'); };
  await assert.rejects(f.initialize(SID, REQUEST, TEXT, signal()), /state unavailable/);
  assert.equal(f.calls.prompt.length, 0);
  assertReleased(f);
});

test('wait exception after acceptance retains guard until corresponding turn/end', async () => {
  const f = fixture();
  f.hooks.wait = async () => { throw new Error('wait interrupted'); };
  await assert.rejects(f.initialize(SID, REQUEST, TEXT, signal()), /wait interrupted/);
  assert.equal(f.blocked().length, 1);
  f.complete();
  assertReleased(f);
});

test('same request retry delegates exact requestId dedup to Host, not a fresh prompt identity', async () => {
  const f = fixture();
  f.hooks.wait = async () => f.waitValue('timeout');
  const first = await f.initialize(SID, REQUEST, TEXT, signal());
  assert.equal(first.completed, false);
  f.hooks.wait = async () => { f.complete(); return f.waitValue('idle'); };
  const second = await f.initialize(SID, REQUEST, TEXT, signal());
  assert.equal(second.completed, true);
  assert.equal(f.calls.prompt.length, 2);
  assert.equal(f.admitted.size, 1, 'Host accepted only one logical message');
  assert.ok(f.calls.prompt.every(p => p.requestId === REQUEST));
  assert.deepEqual(f.calls.prompt[0], f.calls.prompt[1]);
  assert.equal(f.calls.guardDisposals, 1, 'same initializer must not duplicate the protection');
  assertReleased(f);
});

test('nonblank Session from unrelated work rejects initialization without prompt or guard', async () => {
  const f = fixture({ blank: false });
  f.complete('unrelated-human-request');
  await assert.rejects(f.initialize(SID, REQUEST, TEXT, signal()), /no longer blank/);
  assert.equal(f.calls.prompt.length, 0);
  assertReleased(f);
});

test('unknown blankness is fail-closed for an unaccepted initialization', async () => {
  const f = fixture({ unknownProjection: true });
  await assert.rejects(f.initialize(SID, REQUEST, TEXT, signal()), /blankness is unknown/);
  assert.equal(f.calls.prompt.length, 0);
  assertReleased(f);
});

test('already completed same initialization is reported without repeated prompt or wait', async () => {
  const f = fixture();
  f.complete();
  const result = await f.initialize(SID, REQUEST, TEXT, signal());
  assert.equal(result.completed, true);
  assert.equal(result.status, 'already_finished');
  assert.equal(f.calls.prompt.length, 0);
  assert.equal(f.calls.wait.length, 0);
  assertReleased(f);
});

test('guard blocks all target tools but never an unrelated Session or agentless execution', async () => {
  const f = fixture();
  f.hooks.wait = async () => f.waitValue('timeout');
  await f.initialize(SID, REQUEST, TEXT, signal());
  assert.match(f.blocked()[0], /all tools are disabled/);
  assert.deepEqual(f.blocked(OTHER), []);
  for (const guard of f.guards) assert.equal(guard({}), undefined);
  f.complete();
  assertReleased(f);
});

test('future context stays labeled as later background; submitted initialization forbids tool execution', async () => {
  const handoff = '后续任务：读取目录全部文件并联网查文献。不要现在执行。';
  const text = initializationText({ projectTitle: '十大推荐', input: { thread_id: 'W-example', workdir: '/private/project', handoff }, sessionId: SID });
  assert.ok(text.includes(`<future_work_context>\n${handoff}\n</future_work_context>`));
  assert.match(text, /不是本轮要执行的命令/);
  assert.match(text, /本轮只确认接续，不开始实际任务/);
  assert.match(text, /不调用任何工具，不读取文件，不运行命令/);
  assert.match(text, /不对外发送，不开展研究/);
  assert.match(text, /后续收到用户新的明确指令后再开展工作/);
  const f = fixture();
  f.hooks.beforePrompt = async request => {
    assert.deepEqual(request.content, [{ type: 'text', text }]);
    assert.equal(f.blocked().length, 1, 'guard already active before Host accepts a prompt');
  };
  await f.initialize(SID, REQUEST, text, signal());
  assertReleased(f);
});

test('unrelated assistant and turn/end before matching input do not fabricate completion', () => {
  const events = [
    { type: 'turn/start', seq: 1, data: {} },
    { type: 'assistant/message', seq: 2, data: {} },
    { type: 'turn/end', seq: 3, data: { reason: { kind: 'completed' } } },
    { type: 'agent/inbox/spliced', seq: 4, data: { inserted: [{ source: { rpcId: REQUEST } }] } },
  ];
  assert.deepEqual(initializationEvidence(events, REQUEST), { accepted: true, consumed: false, started: false, ended: false, endReason: null, hasReply: false, acknowledgement: '', inputSeq: null, replySeq: null, endSeq: null });
});


test('initialization returns bounded display acknowledgement and seqs, never full model output', async () => {
  const f = fixture();
  f.hooks.wait = async () => {
    f.consume();
    f.emit('assistant/message', {
      message: { role: 'assistant', content: [{ type: 'reasoning', text: 'synthetic-hidden-reasoning' }, { type: 'text', text: 'a'.repeat(5000) }, { type: 'tool_use', text: 'synthetic-tool' }] },
      stream: { chunks: ['synthetic-stream'], replay: 'synthetic-replay' }, provider: { secret: 'synthetic-provider' },
    });
    f.emit('turn/end', { reason: { kind: 'completed' } });
    return { ...f.waitValue('idle'), output: { replay: 'synthetic-replay' } };
  };
  const result = await f.initialize(SID, REQUEST, TEXT, signal());
  assert.equal(result.completed, true);
  assert.equal(result.acknowledgement, 'a'.repeat(512));
  assert.ok(result.inputSeq < result.replySeq && result.replySeq < result.endSeq);
  assert.equal('output' in result, false);
  assert.equal(JSON.stringify(result).includes('synthetic-'), false);
  assertReleased(f);
});

test('rc.3 wire text blocks concatenate; non-text content and stream are excluded', async () => {
  const f = fixture();
  f.hooks.wait = async () => {
    f.consume();
    f.emit('assistant/message', { turn: 1, step: 1,
      message: { role: 'assistant', content: [
        { type: 'text', text: '已接续' }, { type: 'reasoning', text: 'synthetic-reasoning' },
        { type: 'text', text: '，等待下一步指令。' }, { type: 'tool_use', text: 'synthetic-tool' },
      ] },
      stream: [{ type: 'chunk', chunk: { type: 'text', text: 'synthetic-stream' } }],
    });
    f.emit('turn/end', { reason: { kind: 'completed' } });
    return f.waitValue('idle');
  };
  const result = await f.initialize(SID, REQUEST, TEXT, signal());
  assert.equal(result.completed, true);
  assert.equal(result.acknowledgement, '已接续，等待下一步指令。');
  assert.equal(JSON.stringify(result).includes('synthetic-'), false);
  assertReleased(f);
});

test('empty, whitespace, reasoning-only and stream-only assistant events cannot establish acknowledgement', async () => {
  for (const data of [
    {},
    { message: { role: 'assistant', content: [] } },
    { message: { role: 'assistant', content: [{ type: 'text', text: '  \n  ' }] } },
    { message: { role: 'assistant', content: [{ type: 'reasoning', text: 'synthetic-reasoning' }] } },
    { stream: [['text', 'synthetic-old-tuple']] },
    { stream: [{ type: 'text-chunks', chunks: ['synthetic-stream-text'] }] },
  ]) {
    const f = fixture();
    f.hooks.wait = async () => { f.consume(); f.emit('assistant/message', data); f.emit('turn/end', { reason: { kind: 'completed' } }); return f.waitValue('idle'); };
    const result = await f.initialize(SID, REQUEST, TEXT, signal());
    assert.equal(result.accepted, true);
    assert.equal(result.ended, true);
    assert.equal(result.hasReply, false);
    assert.equal(result.completed, false);
    assert.equal(result.replySeq, null);
    assert.equal(result.acknowledgement.trim(), '');
    assertReleased(f);
  }
});

test('cancellation after accepted input retains protection until durable end', async () => {
  const f = fixture(); const controller = new AbortController();
  f.hooks.afterPrompt = async () => { controller.abort(new Error('synthetic cancellation')); controller.signal.throwIfAborted(); };
  await assert.rejects(f.initialize(SID, REQUEST, TEXT, controller.signal), /synthetic cancellation/);
  assert.equal(f.blocked().length, 1);
  f.complete(); assertReleased(f);
});

test('completion during retry then wait cancellation releases guard before successful retry', async () => {
  const f = fixture();
  f.hooks.wait = async () => { f.consume(); return f.waitValue('timeout'); };
  await f.initialize(SID, REQUEST, TEXT, signal());
  const projections = f.context.sessionController.projections;
  let finish = true;
  f.context.sessionController.projections = async (...args) => {
    if (finish) { finish = false; f.complete(); }
    return projections(...args);
  };
  f.hooks.wait = async () => { throw new Error('wait cancelled'); };
  await assert.rejects(f.initialize(SID, REQUEST, TEXT, signal()), /wait cancelled/);
  assertReleased(f);
  assert.equal((await f.initialize(SID, REQUEST, TEXT, signal())).completed, true);
  assertReleased(f);
});

test('failed recovery evidence preserves original error and keeps accepted turn protected', async () => {
  const f = fixture();
  f.hooks.wait = async () => {
    f.hooks.events = async () => { throw new Error('evidence unavailable'); };
    throw new Error('original wait failure');
  };
  await assert.rejects(f.initialize(SID, REQUEST, TEXT, signal()), /original wait failure/);
  assert.equal(f.blocked().length, 1);
  f.complete();
  assertReleased(f);
});
