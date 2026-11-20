"""Local native-session adapters. Host commands and permissions come from this device."""
import json
from pathlib import Path
import socket
import subprocess
import sys
import time

from .errors import Error


def task_prompt(task):
    return f"[CosMesh task {task['id']}]\nThread {task['thread']}\n\n{task['body']}"


def bounded_result(text):
    from .task_queue import MAX_RESULT
    tail = '\n[回复过长，完整内容保存在原生会话中。]'
    raw = text.encode()
    if len(raw) <= MAX_RESULT:
        return text
    return raw[:MAX_RESULT-len(tail.encode())].decode(errors='ignore') + tail


class Dsh:
    """Private stdio channel to the connector inside its selected DSH profile."""
    def call(self, method, **params):
        print(json.dumps({'native': method, **params}, ensure_ascii=False), flush=True)
        line = sys.stdin.buffer.readline(512000)
        if not line:
            raise EOFError('DSH connector disconnected')
        value = json.loads(line)
        if 'error' in value:
            raise Error('HOST_UNAVAILABLE', value['error'])
        return value['result']


class Codex:
    def __init__(self, sock=None):
        self.sock = sock
        self.proc = None
        self.channel = None
        self.seq = 0

    def close(self):
        if self.channel is not None:
            self.channel.abort()
            self.channel.shutdown()
            self.channel = None
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
            self.proc = None

    def connect(self):
        import websocket
        if self.sock:
            stream = socket.socket(socket.AF_UNIX)
        else:
            # The installed Codex resolves its own default control socket. Its
            # proxy carries raw WebSocket bytes, including the HTTP upgrade.
            stream, peer = socket.socketpair()
            try:
                self.proc = subprocess.Popen(['codex','app-server','proxy'], stdin=peer,
                                             stdout=peer, stderr=subprocess.DEVNULL)
            finally:
                peer.close()
        try:
            stream.settimeout(10)
            if self.sock:
                stream.connect(self.sock)
            self.channel = websocket.create_connection('ws://localhost/', socket=stream,
                                                       suppress_origin=True, timeout=10)
            self.rpc('initialize', {'clientInfo': {'name':'cosmesh-receiver','version':'0.6.2'},
                                    'capabilities': {'experimentalApi': True}})
            self.channel.send(json.dumps({'method':'initialized'}))
        except BaseException:
            stream.close()
            self.close()
            raise

    def rpc(self, method, params):
        import websocket
        try:
            if self.channel is None:
                self.connect()
            self.seq += 1
            request = self.seq
            self.channel.send(json.dumps({'id':request, 'method':method, 'params':params}))
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                self.channel.settimeout(max(.1, deadline-time.monotonic()))
                raw = self.channel.recv()
                if not raw:
                    raise Error('HOST_UNAVAILABLE', 'Codex disconnected.')
                reply = json.loads(raw)
                if reply.get('id') != request or 'method' in reply:
                    continue
                if 'error' in reply:
                    raise Error('HOST_UNAVAILABLE', 'Codex ' + method + ': ' + str(reply['error'].get('message', 'RPC error'))[:300])
                return reply['result']
            raise Error('HOST_UNAVAILABLE','Codex app-server did not answer in time.')
        except websocket.WebSocketException as exc:
            self.close()
            raise Error('HOST_UNAVAILABLE','Codex local WebSocket unavailable.') from exc

    def call(self, method, session, workdir, task, **kwargs):
        try:
            if method == 'start':
                # queue/add delegates the final busy/idle decision to the host.
                # A direct turn/start can splice into a human turn racing our poll.
                reply = self.rpc('thread/queue/add', {'threadId':session, 'clientUserMessageId':task['id'],
                                                      'input':[{'type':'text','text':task_prompt(task)}]})
                if reply['queuedSubmission']['clientUserMessageId'] != task['id']:
                    raise Error('HOST_UNAVAILABLE','Codex did not confirm the assigned input.')
                return {}
            thread = self.rpc('thread/read', {'threadId':session, 'includeTurns':False})['thread']
            if thread['id'] != session or Path(thread['cwd']).resolve(strict=True) != Path(workdir).resolve(strict=True):
                raise Error('TARGET_CHANGED', 'Codex native session directory differs from its binding.')
            if method == 'observe':
                cursor = None
                while True:
                    queue = self.rpc('thread/queue/list', {'threadId':session,'limit':50,'cursor':cursor})
                    if any(item['clientUserMessageId'] == task['id'] for item in queue['data']):
                        return {'state':'queued_on_host'}
                    cursor = queue['nextCursor']
                    if not cursor:
                        break
                cursor = None
                while True:
                    page = self.rpc('thread/turns/list', {'threadId':session, 'cursor':cursor,
                                    'limit':4, 'itemsView':'full', 'sortDirection':'desc'})
                    evidence = codex_evidence(page['data'], task)
                    if evidence is not None:
                        return evidence
                    cursor = page['nextCursor']
                    if not cursor or any((t.get('startedAt') or 2**63) < task['cursor'] for t in page['data']):
                        return {'state':'unconfirmed'}
            if thread['status']['type'] not in ('idle', 'notLoaded'):
                return {'ready':False}
            if thread['status']['type'] == 'notLoaded':
                thread = self.rpc('thread/resume', {'threadId':session})['thread']
            queue = self.rpc('thread/queue/list', {'threadId':session,'limit':1})
            return {'ready':not queue['data'] and thread['id'] == session and thread['status']['type'] == 'idle'
                    and Path(thread['cwd']).resolve(strict=True) == Path(workdir).resolve(strict=True), 'cursor':int(time.time()) - 5}
        except (OSError, ValueError, KeyError, Error):
            self.close()
            raise


def codex_evidence(turns, task):
    for turn in turns:
        items = turn['items']
        users = [i for i in items if i['type'] == 'userMessage']
        if not any(i.get('clientId') == task['id'] or any(
                part.get('type') == 'text' and part.get('text') == task_prompt(task)
                for part in i.get('content', [])) for i in users):
            continue
        if len(users) != 1:
            return {'state':'unconfirmed'}
        status = turn['status']
        if status == 'inProgress':
            return {'state':'running'}
        if status in ('interrupted','failed'):
            return {'state':'failed','result': 'Codex 回合' + ('已中断。' if status == 'interrupted' else '执行失败。')}
        if status == 'completed':
            texts = [i['text'] for i in items if i['type'] == 'agentMessage'
                     and i.get('phase') in (None, 'final_answer') and i.get('text','').strip()]
            return {'state':'completed','result':bounded_result(texts[-1])} if texts else {
                    'state':'failed','result':'Codex 已结束，未返回最终文本。'}
    return None
