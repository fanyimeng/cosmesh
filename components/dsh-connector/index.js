// Native ToolDefinition contract; no package import that depends on profile resolution.
import { createBridge } from './lib/work-link.js';
import { hostAdapter, defaultCliPath } from './lib/dsh.js';
import path from 'node:path';
import { profileReceivers, startReceiver } from './lib/tasks.js';

export const name = 'cosmesh-dsh-connector';
export const inject = ['tools', 'sandboxPolicy', 'workspaceRegistry', 'sessionController', 'sessions', 'sessionPersistence'];

export function validateConfig(config = {}) {
  if (!config || typeof config !== 'object' || Array.isArray(config)) throw new Error('Connector config must be an object.');
  if (Object.keys(config).some(k => !['namespace', 'cliPath'].includes(k))) throw new Error('Unknown connector config field.');
  const namespace = config.namespace === undefined ? 'desktop' : config.namespace;
  if (typeof namespace !== 'string' || !/^[a-zA-Z0-9_-]{1,80}$/.test(namespace)) throw new Error('Invalid bridge namespace.');
  const cliPath = config.cliPath === undefined ? defaultCliPath() : config.cliPath;
  if (typeof cliPath !== 'string' || !path.isAbsolute(cliPath) || /[\x00-\x1f\x7f]/.test(cliPath)) throw new Error('cliPath must be an absolute executable path without control characters.');
  return { namespace, cliPath };
}

export function apply(ctx, config = {}) {
  const { namespace, cliPath } = validateConfig(config);
  const profile = ctx.get?.('profileContext');
  if (profile?.dir) {
    try {
      for (const route of profileReceivers(profile.dir)) ctx.effect(() => startReceiver(ctx, cliPath, route));
    } catch {
      ctx.logger?.warn?.('Check cosmesh-receivers.json in this DSH profile.');
    }
  }
  const bridge = createBridge(hostAdapter(ctx, namespace, cliPath));
  ctx.effect(() => ctx.tools.register({
    name: 'cosmesh_open_work',
    description: 'Connect an existing private CosMesh Thread to a native DSH project and Session. By default, send ONE initialization-only prompt: the model acknowledges context then waits, with all tool execution denied during that turn. This incurs a model request, but does not start research, publish or upload. Requires Full access and explicit user authorization. initialize:false creates an idle blank Session that DSH hides when not selected. Same operation_key reuses objects and deduplicates initialization. Retry original creation arguments; initialize may change from false to true to initialize an earlier blank. A different key deliberately creates another Session. Existing project titles are preserved.',
    parameters: {
      type: 'object', additionalProperties: false,
      required: ['thread_id', 'workdir', 'project_title', 'session_title', 'operation_key'],
      properties: {
      thread_id: { type: 'string', description: 'Existing local private CosMesh W-UUID.' },
      workdir: { type: 'string', description: 'Absolute project directory on this Host.' },
      project_title: { type: 'string', description: 'Title for a newly registered project; existing project titles are preserved.' },
      session_title: { type: 'string', description: 'Title for the new Session.' },
      operation_key: { type: 'string', description: 'Stable key for this logical new-session request, e.g. example-work-1. Reuse on retries.' },
      handoff: { type: 'string', description: 'At most 12000 characters of private context. Saved locally and included as future-work context in the initialization prompt, not instructions for this turn.' },
      initialize: { type: 'boolean', description: 'Defaults to true: one guarded model acknowledgement then wait. False creates only a blank Session; it may be hidden in DSH.' },
      create_directory: { type: 'boolean', description: 'Create missing project directories only if explicitly authorized. Defaults to false.' },
      },
    },
    output: { schema: { type: 'string' }, render: (_args, value) => [{ type: 'text', text: value }] },
    async execute(args, exec) {
      if (!exec.agent) throw new Error('An Agent Session is required.');
      const policy = ctx.sandboxPolicy.resolve({ session: exec.agent.session });
      if (policy.mode !== 'danger-full-access') throw new Error('Full access is required. This tool cannot approve or change permission mode.');
      exec.signal.throwIfAborted();
      return JSON.stringify(await bridge(args, exec.signal));
    },
  }));
}
