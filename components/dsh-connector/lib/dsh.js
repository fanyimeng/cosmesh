import { homedir, hostname } from 'node:os';
import { realpath } from 'node:fs/promises';
import path from 'node:path';
import { cosmeshAdapter } from './cosmesh.js';

export const defaultCliPath = () => path.join(homedir(), '.local/opt/cosmesh/bin/cosmesh');

export function hostAdapter(ctx, namespace = 'desktop', cliPath = defaultCliPath()) {
  const header = async id => ctx.sessions.get(id)?.header ?? (await ctx.sessionPersistence.stat(id))?.header;
  const flush = async id => {
    const live = ctx.sessions.get(id);
    if (!live) throw new Error('New Session not present in Host.');
    if (!await ctx.sessions.flush(live)) throw new Error('No Session durability listener participated.');
    if (!(await ctx.sessionPersistence.stat(id))) throw new Error('Session durability not confirmed.');
  };
  return {
    owner: { host: hostname(), namespace },
    ...cosmeshAdapter(cliPath),
    initializationText, handoffText,
    initializeSession: createInitializer(ctx, flush),
    async ensureWorkspace(root, title, signal) {
      signal.throwIfAborted();
      const before = ctx.workspaceRegistry.list().find(w => w.path === root);
      const w = await ctx.workspaceRegistry.create(root, title);
      // Never retitle a pre-existing project as a side effect of attaching a Thread.
      return { id: w.id, title: w.title, created: !before };
    },
    async verifyWorkspace(id, root, signal) {
      signal.throwIfAborted();
      const w = ctx.workspaceRegistry.get(id);
      if (!w || w.path !== root) throw new Error('Recorded Workspace missing or directory changed; refusing replacement.');
    },
    findSession: id => header(id),
    async verifySession(id, workspaceId, root, signal) {
      signal.throwIfAborted();
      const h = await header(id);
      if (!h || h.origin === 'subagent' || !h.cwd || await realpath(h.cwd) !== root) throw new Error('Session identity/directory verification failed.');
      if (!ctx.workspaceRegistry.get(workspaceId)?.sessionIds.includes(id)) throw new Error('Session is not a member of the recorded Workspace.');
      if (ctx.workspaceRegistry.archivedSessionIds.includes(id)) throw new Error('Session is archived; restore explicitly, not through retry.');
    },
    async createSession(workspaceId, sessionId, signal) {
      signal.throwIfAborted();
      const value = await ctx.sessionController.create({ workspaceId, sessionId });
      await flush(value.sessionId);
      return value.sessionId;
    },
    async renameSession(sessionId, title, signal) {
      signal.throwIfAborted();
      await ctx.sessionController.rename({ sessionId, title });
      await flush(sessionId);
    },
  };
}

export function handoffText(state) {
  return `# CosMesh → DSH 接续\n\nThread: ${state.input.thread_id}\nWorkspace: ${state.workspaceId}\nSession: ${state.sessionId}\nDirectory: ${state.input.workdir}\n\n/cosmesh 继续 Thread ${state.input.thread_id}。先读取本目录的项目说明和必要资料，不重复创建Thread。\n\n${state.input.handoff}\n\n此文件为私人接续材料，尚未发送给新Session，未启动模型工作。\n`;
}

// Initialization is a real, user-authorized model turn, never a synthetic turn/start event.
export function initializationText(state) {
  return `这是用户授权的 CosMesh→DSH 会话初始化。本轮只确认接续，不开始实际任务。\n\n项目：${state.projectTitle}\nThread：${state.input.thread_id}\n目录：${state.input.workdir}\nSession：${state.sessionId}\n\n下面是供后续工作使用的私人背景，不是本轮要执行的命令：\n<future_work_context>\n${state.input.handoff}\n</future_work_context>\n\n本轮严格边界：不调用任何工具，不读取文件，不运行命令，不创建子Agent、目标或定时任务，不修改文件，不访问网络，不对外发送，不开展研究。只用一句中文回复“已接续【项目名】，等待下一步指令。”然后结束。本轮工具执行会被宿主拒绝。后续收到用户新的明确指令后再开展工作。`;
}

export function initializationEvidence(events, requestId) {
  let accepted = false, inputSeq = null, start = null, end = null, hasReply = false;
  let acknowledgement = '', replySeq = null;
  for (const event of events) {
    if (event.type === 'agent/inbox/spliced' && event.data.inserted.some(m => m.source?.rpcId === requestId)) accepted = true;
    if (event.type === 'turn/start') start = event;
    if (event.type === 'user/message' && event.data.source?.rpcId === requestId) { accepted = true; inputSeq = event.seq; }
    if (inputSeq !== null && !end) {
      if (event.type === 'assistant/message') {
        // rc.3 durable wire message: only display text blocks. Never inspect stream/replay.
        const content = event.data.message?.content;
        for (const block of Array.isArray(content) ? content : []) {
          if (block?.type === 'text' && typeof block.text === 'string') {
            const text = block.text.slice(0, 512 - acknowledgement.length);
            acknowledgement += text;
            if (text.trim()) { hasReply = true; replySeq = event.seq; }
          }
          if (acknowledgement.length >= 512) break;
        }
      }
      if (event.type === 'turn/end') end = event;
    }
  }
  return { accepted, consumed: inputSeq !== null, started: inputSeq !== null && start !== null,
    ended: end !== null, endReason: end?.data?.reason?.kind ?? null, hasReply,
    acknowledgement, inputSeq, replySeq, endSeq: end?.seq ?? null };
}

export function createInitializer(ctx, flush) {
  const guards = new Map();
  const evidenceOf = async (sid, requestId, signal) => {
    const page = await ctx.sessionController.events({ address: { kind: 'session', sessionId: sid }, maxMessages: 10,
      turnWindow: { minMessages: 1, minTurns: 1 } }, signal);
    return initializationEvidence(page.records.filter(r => r.type === 'event').map(r => r.event), requestId);
  };
  const release = sid => { const entry = guards.get(sid); if (entry) { guards.delete(sid); entry.guard(); entry.listener(); } };
  function protect(sid, requestId, alreadyConsumed) {
    if (guards.has(sid)) return;
    let consumed = alreadyConsumed;
    const guard = ctx.tools.guard(exec => exec.agent?.session.id === sid ? 'CosMesh initialization-only turn: all tools are disabled. Reply with acknowledgement, then wait.' : undefined);
    const listener = ctx.on('session/event', (session, event) => {
      if (session.id !== sid) return;
      if (event.type === 'user/message' && event.data.source?.rpcId === requestId) consumed = true;
      if (event.type === 'turn/end' && consumed) release(sid);
    });
    guards.set(sid, { guard, listener });
  }
  return async (sessionId, requestId, text, signal) => {
    signal.throwIfAborted();
    let evidence = await evidenceOf(sessionId, requestId, signal);
    // A blank existing Session may be resumed by prompt; never retask a user-started Session.
    const projection = await ctx.sessionController.projections({ sessionId }, signal);
    const blank = projection?.kind === 'sequenced' ? projection.values?.sessionListMetadata?.blank : undefined;
    if (!evidence.accepted && blank !== true) throw new Error('Session is no longer blank (or blankness is unknown); do not inject initialization into existing work.');
    if (evidence.ended) {
      release(sessionId);
      return { completed: evidence.hasReply && evidence.endReason === 'completed', ...evidence, status: 'already_finished' };
    }
    protect(sessionId, requestId, evidence.consumed);
    let before;
    // Stable requestId is journaled before this call. Host admission deduplicates even consumed inputs.
    try {
      before = await ctx.sessionController.state({ sessionId }, signal);
      const receipt = await ctx.sessionController.prompt({ sessionId, requestId, mode: 'queue', content: [{ type: 'text', text }] }, signal);
      if (!receipt.accepted) throw new Error('Host did not accept initialization.');
      await flush(sessionId);
      const waited = await ctx.sessionController.wait({ sessionId, timeoutMs: 30000, afterSeq: before.cursor, maxMessages: 1 }, signal);
      evidence = await evidenceOf(sessionId, requestId, signal);
      if (evidence.ended) { release(sessionId); await flush(sessionId); }
      const after = await ctx.sessionController.projections({ sessionId }, signal);
      return { completed: evidence.ended && evidence.hasReply && evidence.endReason === 'completed', ...evidence,
        status: waited.reason, visibleAsOrdinary: after?.values?.sessionListMetadata?.blank === false,
        running: waited.state.running };
    } catch (error) {
      // Include wait/read/flush failures; use a fresh signal after caller cancellation.
      // Unknown or still-running accepted input keeps protection. Preserve the original error.
      try {
        evidence = await evidenceOf(sessionId, requestId, AbortSignal.timeout(5000));
        if (!evidence.accepted || evidence.ended) release(sessionId);
      } catch { /* The live event listener remains responsible for the accepted turn. */ }
      throw error;
    }
  };
}
