import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtempSync, writeFileSync, chmodSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { nativeRequest, routes, profileReceivers } from '../lib/tasks.js';

test('DSH routes validate and match a user-authorized existing session', async () => {
  const route = {thread:'W-dd338800-a4eb-4d25-9e5d-0e016aa64509',session:'session-one'};
  assert.deepEqual(routes([route]),[route]);
  assert.throws(() => routes([{...route, root:'relative'}]));
  await assert.rejects(nativeRequest({},route,{session:'other',task:{}},new AbortController().signal),/route mismatch/);
});

test('native queue, paged receipt, runtime context and unrelated turns', async () => {
  const task = {id:'id',thread:'W-test',session:'test',agent:'dsh',cursor:0,body:'test'};
  const scope = {thread:task.thread,session:task.session};
  let calls = 0;
  const rows = [
    {seq:1,type:'user/message',data:{source:{rpcId:task.id,kind:'user'}}},
    {seq:2,type:'user/message',data:{source:{kind:'runtime-context'}}},
    {seq:3,type:'assistant/message',data:{message:{content:[{type:'text',text:'this result'}]}}},
    {seq:4,type:'turn/end',data:{reason:{kind:'completed'}}},
    {seq:5,type:'assistant/message',data:{message:{content:[{type:'text',text:'another turn'}]}}},
  ];
  const ctx = {workspaceRegistry:{archivedSessionIds:[]},sessions:{get:()=>({header:{cwd:process.cwd()}})},
    sessionController:{state:async()=>({running:true,cursor:0}),prompt:async request=>{
      assert.equal(request.mode,'queue'); assert.equal(request.requestId,task.id);calls++;return {accepted:true};
    },events:async ({beforeSeq})=>({cursor:5,hasMore:!beforeSeq,records:
      (beforeSeq ? rows.slice(0,2) : rows.slice(2)).map(event=>({type:'event',event}))})}};
  const request=native=>nativeRequest(ctx,scope,{native,session:scope.session,task,workdir:process.cwd()},new AbortController().signal);
  assert.equal((await request('inspect')).ready,false);assert.equal(calls,0);
  await request('start');assert.equal(calls,1);
  assert.deepEqual(await request('observe'),{state:'completed',result:'this result'});
  rows[1].data.source.kind='user';assert.equal((await request('observe')).state,'unconfirmed');
});

test('profile-only receiver list survives component downgrades and rejects a shared file', t => {
  const dir = mkdtempSync(join(tmpdir(),'dsh-route-')); t.after(()=>rmSync(dir,{recursive:true}));
  const file = join(dir,'cosmesh-receivers.json');
  const route = {thread:'W-dd338800-a4eb-4d25-9e5d-0e016aa64509',session:'session-one'};
  assert.deepEqual(profileReceivers(dir),[]);
  writeFileSync(file,JSON.stringify([route]),{mode:0o600});
  assert.deepEqual(profileReceivers(dir),[route]);
  chmodSync(file,0o644);assert.throws(()=>profileReceivers(dir),/owner-private/);
});
