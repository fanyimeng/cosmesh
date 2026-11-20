// A receiver belongs to one explicitly configured existing Session in this DSH profile.
import { spawn } from 'node:child_process';
import { closeSync, constants, fstatSync, openSync, readFileSync } from 'node:fs';
import { realpath } from 'node:fs/promises';
import path from 'node:path';
import { createInterface } from 'node:readline';

export function routes(config) {
  if (!Array.isArray(config) || config.length > 64) throw new Error('Invalid taskReceivers.');
  return config.map(row => {
    if (!row || Object.keys(row).some(k => !['thread','session','root','interval'].includes(k)) ||
        !/^W-[0-9a-f-]{36}$/.test(row.thread) || typeof row.session !== 'string' ||
        !/^[a-zA-Z0-9._-]{1,200}$/.test(row.session) ||
        (row.root !== undefined && (typeof row.root !== 'string' || !path.isAbsolute(row.root))) ||
        (row.interval !== undefined && (!Number.isInteger(row.interval) || row.interval < 1 || row.interval > 3600))) {
      throw new Error('A task receiver needs a Thread ID and native Session.');
    }
    return row;
  });
}

export function profileReceivers(dir) {
  let fd;
  try {
    fd = openSync(path.join(dir,'cosmesh-receivers.json'), constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    const st = fstatSync(fd);
    if (!st.isFile() || st.uid !== process.getuid() || (st.mode & 0o077) || st.size > 65536) {
      throw new Error('Expected an owner-private receiver list.');
    }
    return routes(JSON.parse(readFileSync(fd,'utf8')));
  } catch (error) {
    if (error.code === 'ENOENT') return [];
    throw error;
  } finally {
    if (fd !== undefined) closeSync(fd);
  }
}

function evidence(events, id) {
  let seen = false, result = '', mixed = false;
  for (const e of events) {
    if (e.type === 'user/message') {
      if (e.data.source?.rpcId === id) seen = true;
      else if (seen && ['user','retry'].includes(e.data.source?.kind)) mixed = true;
    }
    if (!seen) continue;
    if (e.type === 'assistant/message') {
      const text = (e.data.message?.content ?? []).filter(b => b.type === 'text').map(b => b.text).join('');
      if (text.trim()) result = text;
    }
    if (e.type === 'turn/end') {
      if (mixed) return { state:'unconfirmed' };
      const reason = e.data.reason?.kind;
      if (reason !== 'completed') return { state:'failed', result:'DSH 回合结束：' + (reason ?? 'unknown') };
      return result ? { state:'completed', result } : { state:'failed', result:'DSH 已结束，未返回最终文本。' };
    }
  }
  return { state:seen ? 'running' : 'unconfirmed' };
}

export async function nativeRequest(ctx, route, request, signal) {
  const { native, session, task, workdir } = request;
  if (session !== route.session || task.thread !== route.thread || task.agent !== 'dsh' || task.session !== session) {
    throw new Error('Task receiver route mismatch.');
  }
  const header = ctx.sessions.get(session)?.header ?? (await ctx.sessionPersistence.stat(session))?.header;
  if (!header?.cwd || header.origin === 'subagent' || ctx.workspaceRegistry.archivedSessionIds.includes(session) ||
      await realpath(header.cwd) !== await realpath(workdir)) throw new Error('DSH session binding changed.');
  const controller = ctx.sessionController;
  if (native === 'inspect') {
    const state = await controller.state({ sessionId:session }, signal);
    return { ready:state.running === false, cursor:state.cursor };
  }
  if (native === 'start') {
    const text = `[CosMesh task ${task.id}]\nThread ${task.thread}\n\n${task.body}`;
    const reply = await controller.prompt({ sessionId:session, mode:'queue', requestId:task.id, content:[{ type:'text', text }] }, signal);
    if (!reply.accepted) throw new Error('DSH did not confirm admission.');
    return {};
  }
  if (native !== 'observe') throw new Error('Unknown receiver operation.');
  let beforeSeq, throughSeq;
  const groups = [];
  while (true) {
    const page = await controller.events({ address:{kind:'session',sessionId:session}, maxMessages:32,
      ...(beforeSeq === undefined ? {} : { beforeSeq, throughSeq }) }, signal);
    throughSeq ??= page.cursor;
    const events = page.records.filter(r => r.type === 'event').map(r => r.event);
    groups.push(events.filter(e => e.seq > task.cursor));
    if (!page.hasMore || !events.length || events[0].seq <= task.cursor) break;
    if (beforeSeq !== undefined && events[0].seq >= beforeSeq) throw new Error('DSH history cursor did not advance.');
    beforeSeq = events[0].seq;
  }
  const outcome = evidence(groups.reverse().flat(), task.id);
  if (outcome.result && Buffer.byteLength(outcome.result) > 64000) {
    outcome.result = Buffer.from(outcome.result).subarray(0,63800).toString() + '\n[回复过长，完整内容保存在原生会话中。]';
  }
  return outcome;
}

export function startReceiver(ctx, cli, route) {
  let child, restart, stopped = false;
  const abort = new AbortController();
  function launch() {
    const args = ['thread','task','receive',route.thread,'--agent','dsh','--session',route.session,'--dsh-stdio','--json'];
    if (route.root) args.push('--root',route.root);
    if (route.interval) args.push('--interval',String(route.interval));
    const proc = spawn(cli,args,{stdio:['pipe','pipe','pipe']});
    child = proc;
    proc.stdin.on('error', () => {});
    proc.stderr.on('data', data => ctx.logger?.warn?.(String(data).trim().slice(0,150)));
    proc.on('error', () => ctx.logger?.warn?.('CosMesh task receiver unavailable.'));
    proc.once('close', () => { if (!stopped) restart = setTimeout(launch,15000); });
    const lines = createInterface({input:proc.stdout});
    (async () => {
      for await (const line of lines) {
        if (stopped) break;
        const req = JSON.parse(line);
        if (!req.native) continue;
        let reply;
        try { reply = { result:await nativeRequest(ctx,route,req,abort.signal) }; }
        catch { reply = {error:'DSH session unavailable or delivery unconfirmed.'}; }
        if (!proc.stdin.destroyed) proc.stdin.write(JSON.stringify(reply) + '\n');
      }
    })().catch(() => proc.kill());
  }
  launch();
  return () => { stopped = true; clearTimeout(restart); abort.abort(); child?.kill(); };
}
