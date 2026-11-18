import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { hostname } from 'node:os';
import { setTimeout as delay } from 'node:timers/promises';
const exec = promisify(execFile);

export async function runCosmesh(cli, args, signal) {
  for (let attempt = 0; attempt < 4; attempt++) {
    signal.throwIfAborted();
    let stdout;
    try { ({ stdout } = await exec(cli, [...args, '--json'], { signal, timeout: 30000, maxBuffer: 4 * 1024 * 1024, encoding: 'utf8' })); }
    catch (error) { if (signal.aborted) throw signal.reason; if (!error.stdout) throw new Error(`CosMesh process failed: ${error.code ?? error.message}`); stdout = error.stdout; }
    let result;
    try { result = JSON.parse(stdout); } catch { throw new Error('CosMesh returned invalid JSON; no output copied to the model.'); }
    if (result.ok) return result.data;
    if (result.error?.code === 'STORE_BUSY' && attempt < 3) { await delay(250 * 2 ** attempt, undefined, { signal }); continue; }
    throw new Error(`CosMesh ${result.error?.code ?? 'ERROR'}: ${result.error?.message ?? 'Operation failed'}`);
  }
}

export function cosmeshAdapter(cli) {
  const call = (args, signal) => runCosmesh(cli, args, signal);
  return {
    async checkThread(id, signal) {
      const data = await call(['thread', 'show', id], signal);
      if (data.thread?.id !== id || data.thread?.source?.repo !== 'local' || data.thread?.visibility !== 'private') {
        throw new Error('v1 requires an existing local private Thread; no access/visibility changes are performed.');
      }
      if (data.thread.lifecycle !== 'active') throw new Error('Thread is not active; restore it explicitly first.');
    },
    recordInitialization: (id, state, key, signal) => call(['thread', 'update', id, '--local-only', '--body',
      'DSH 新会话已完成用户授权的初始化确认并等待，未开展研究。具体会话与初始化回执保存在私人关联和本机接续日志中。',
      '--interaction-summary', '新Session已完成初始化确认并等待，未开展研究。', '--idempotency-key', `dsh-init-v2-${key}`], signal),
    bindProject: (id, wid, title, root, signal) => call(['thread', 'bind-project', id, '--agent', 'dsh', '--host', hostname(),
      '--project-name', title, '--project-id', wid, '--kind', 'workspace', '--workdir', root], signal),
    bindSession: (id, sid, root, signal) => call(['thread', 'bind', id, '--agent', 'dsh', '--host', hostname(), '--workdir', root, '--session-id', sid], signal),
    record: (id, state, handoffPath, key, signal) => call(['thread', 'update', id, '--local-only', '--body',
      'DSH 项目与新会话已关联，私人接续材料已保存。具体项目、会话和目录保存在私人关联与本机接续日志中。此步骤尚未启动模型工作。',
      '--interaction-summary', '已关联DSH原生项目与新Session，保存私人接续材料，未启动模型工作。',
      // New receipt payloads need distinct keys if an old write succeeded before its journal step.
      '--idempotency-key', `dsh-bridge-v2-${key}`], signal),
  };
}
