import test from 'node:test';
import assert from 'node:assert/strict';
import { apply } from '../index.js';
import { normalize } from '../lib/work-link.js';
function fixture(mode) {
  let tool;
  const ctx = { effect: fn => fn(), tools: { register: value => { tool = value; } }, sandboxPolicy: { resolve: () => ({ mode }) } };
  apply(ctx, { namespace: 'tests' });
  return tool;
}
test('tool registers native JSON Schema without importing installed DSH packages', () => {
  const tool = fixture('danger-full-access');
  assert.equal(tool.name, 'cosmesh_open_work');
  assert.equal(tool.parameters.type, 'object');
  assert.equal(tool.parameters.additionalProperties, false);
  assert.ok(tool.parameters.required.includes('operation_key'));
  assert.equal(tool.output.schema.type, 'string');
});
test('low permission callers are refused before any Host or filesystem operation', async () => {
  for (const mode of ['read-only', 'workspace-write', undefined]) {
    await assert.rejects(fixture(mode).execute({}, { agent: { session: {} }, signal: new AbortController().signal }), /Full access/);
  }
});
test('programmatic caller without an Agent cannot mutate', async () => {
  await assert.rejects(fixture('danger-full-access').execute({}, { signal: new AbortController().signal }), /Agent Session/);
});
test('raw ToolDefinition arguments have explicit validation', () => {
  for (const value of [null, [], 1, { unexpected: 1 }, { create_directory: 'true' }, { thread_id: 1 }]) assert.throws(() => normalize(value));
});
