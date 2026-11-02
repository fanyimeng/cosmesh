"""Shared system feedback: private outbox plus ordinary append-only Updates."""
import fcntl
import hashlib
import json
import os
import stat
import shutil
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from . import __version__
from .access import DEFAULT_DISCOVERY, discovery
from .errors import Error
from .protocol import identifier, public
from .service import Service
from .store import Store, atomic, safe_path
from .sync import clone, remote_status, state, synchronize, transport

CATEGORIES = ('bug', 'suggestion', 'question', 'experience', 'reply')
PRODUCTS = ('cosmesh', 'cosbase', 'system')
MAX_BODY = 64 * 1024
DEFAULT_REPO = 'cosmesh-feedback'


def validate_record(meta):
    info = meta.get('feedback')
    if (not isinstance(info, dict) or set(info) != {'schema_version', 'category', 'product', 'component', 'client_version'} or
            type(info['schema_version']) is not int or info['schema_version'] != 1 or
            info['category'] not in CATEGORIES or info['product'] not in PRODUCTS or
            not isinstance(info['component'], str) or len(info['component']) > 100 or
            not isinstance(info['client_version'], str) or len(info['client_version']) > 100):
        raise Error('SCHEMA_INVALID', 'Invalid system feedback metadata.')
    if bool(meta.get('reply_to')) != (info['category'] == 'reply') or meta.get('type') != 'feedback' or meta.get('task'):
        raise Error('SCHEMA_INVALID', 'System feedback replies require a parent Update.')


def validate_snapshot(model):
    for doc in model.docs.values():
        meta = doc['meta']
        if meta['kind'] == 'project':
            if meta.get('builtin') != 'system_feedback' or meta.get('members'):
                raise Error('POLICY_DENIED', 'Feedback contexts belong to their own authors.')
        elif meta['kind'] == 'update':
            validate_record(meta)
            if len(doc['body'].encode()) > MAX_BODY:
                raise Error('INPUT_TOO_LARGE', 'System feedback exceeds 64 KiB.')
            if meta.get('reply_to'):
                validate_record(model.docs[meta['reply_to']]['meta'])
        else:
            raise Error('POLICY_DENIED', 'The system feedback repository stores contexts and feedback Updates.')


class FeedbackService(Service):
    def mutate(self, action, payload):
        if action != 'feedback.add':
            raise Error('COMMAND_DENIED', 'Unsupported feedback mutation.')
        doc_id = identifier(payload['id'])
        if not doc_id.startswith('U-') or doc_id in self.docs:
            raise Error('IDEMPOTENCY_CONFLICT', 'This feedback ID already exists with a different request.')
        contexts = sorted((d for d in self.docs.values() if d['meta']['kind'] == 'project' and
                           d['meta']['author'] == self.user), key=lambda d: d['meta']['id'])
        context = contexts[0] if contexts else self.fresh('project', '系统反馈：' + self.user,
                    '个人提交反馈的上下文；反馈在本 Hub 内共享。', lead=self.user, members=[], builtin='system_feedback')
        request = payload['request']
        if request.get('reply_to'):
            validate_record(self.get(request['reply_to'], 'update')['meta'])
        extra = {'reply_to': request['reply_to']} if request.get('reply_to') else {}
        doc = self.fresh('update', request['title'], request['body'], id=doc_id,
                         project=context['meta']['id'], type='feedback',
                         feedback={'schema_version': 1, 'category': request['category'], 'product': request['product'],
                                   'component': request['component'], 'client_version': payload['client_version']}, **extra)
        validate_snapshot(self)
        return public(doc)


class Feedback:
    def __init__(self):
        parent = Path(os.environ.get('COSMESH_DATA_DIR', str(Path.home() / '.local/share/cosmesh')))
        self.root = safe_path(os.environ.get('COSMESH_FEEDBACK_DIR', str(parent / 'feedback')))
        self.store = Store(self.root / 'repo')
        prefix = os.environ.get('COSMESH_INSTALL_PREFIX')
        if prefix and self.root.is_relative_to(Path(prefix).resolve()):
            raise Error('PATH_INVALID', 'Feedback data must stay outside the application installation.')
        if any((p / '.git').exists() for p in (self.root, *self.root.parents)):
            raise Error('PATH_INVALID', 'Keep the feedback outbox outside other Git working copies.')

    def directory(self, create=False):
        if create:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.exists():
            safe_path(self.root)
            info = self.root.stat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise Error('AUTH_DENIED', 'The feedback directory must be private and owned by the current account.')

    def read_json(self, path):
        safe_path(path)
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_nlink != 1:
            raise Error('AUTH_DENIED', 'Unsafe feedback metadata file.')
        if info.st_size > 8 * MAX_BODY:
            raise Error('INPUT_TOO_LARGE', 'Feedback metadata exceeds its limit.')
        return json.loads(path.read_text())

    @contextmanager
    def locked(self, name):
        self.directory(create=True)
        path = safe_path(self.root / (name + '.lock'))
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        with os.fdopen(descriptor, 'a+') as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_nlink != 1:
                raise Error('AUTH_DENIED', 'Unsafe feedback lock.')
            deadline = time.monotonic() + 3
            while True:
                try:
                    fcntl.lockf(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise Error('FEEDBACK_BUSY', 'Another operation is using this feedback resource; retry later.')
                    time.sleep(.05)
            try:
                yield
            finally:
                fcntl.lockf(handle, fcntl.LOCK_UN)

    def drafts(self):
        self.directory()
        folder = safe_path(self.root / 'outbox')
        return [(p, self.read_json(p)) for p in sorted(folder.glob('*.json'))] if folder.exists() else []

    def queue(self, request, key=None, dry_run=False):
        request = dict(request)
        body = request.get('body', '')
        if not isinstance(body, str) or not body.strip() or '\x00' in body or len(body.encode()) > MAX_BODY:
            raise Error('INPUT_INVALID', 'Provide nonempty feedback of at most 64 KiB.')
        request['title'] = request.get('title') or body.strip().splitlines()[0].lstrip('# ')[:100]
        if not isinstance(request['title'], str) or not request['title'].strip() or len(request['title']) > 200:
            raise Error('INPUT_INVALID', 'Provide a feedback title of at most 200 characters.')
        if request.get('reply_to'):
            identifier(request['reply_to'])
            if not request['reply_to'].startswith('U-'):
                raise Error('INPUT_INVALID', 'Reply to a feedback Update ID.')
        elif request['category'] == 'reply':
            raise Error('INPUT_INVALID', 'A feedback reply needs a target ID.')
        validate_record({'feedback': {'schema_version': 1, **{k: request[k] for k in ('category', 'product', 'component')},
                                     'client_version': __version__}, 'type': 'feedback', 'reply_to': request.get('reply_to')})
        key = key or str(uuid4())
        if not isinstance(key, str) or not key or len(key) > 200:
            raise Error('INPUT_INVALID', 'Use an idempotency key from 1 to 200 characters.')
        fingerprint = hashlib.sha256(json.dumps(request, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        path = self.root / 'outbox' / (hashlib.sha256(key.encode()).hexdigest() + '.json')
        def prepare():
            if path.exists():
                existing = self.read_json(path)
                if existing['fingerprint'] != fingerprint:
                    raise Error('IDEMPOTENCY_CONFLICT', 'This feedback key belongs to different content.')
                return {**existing, 'replayed': True, 'dry_run': dry_run}
            try:
                target = self.read_json(self.root / 'connection.json') if (self.root / 'connection.json').exists() else self.infer()
            except Error:
                target = None
            return {'id': 'U-' + str(uuid4()), 'key': key, 'fingerprint': fingerprint, 'request': request, 'destination': target,
                    'client_version': __version__, 'queued_at': datetime.now(timezone.utc).isoformat(),
                    'state': 'pending', 'replayed': False, 'dry_run': dry_run}
        if dry_run:
            self.directory()
            return prepare()
        with self.locked('outbox'):
            folder = safe_path(path.parent)
            folder.mkdir(mode=0o700, exist_ok=True)
            result = prepare()
            if not result['replayed']:
                atomic(path, json.dumps(result, ensure_ascii=False, indent=2) + '\n')
            return result

    def infer(self, ssh=None, service=None, repo=None):
        if ssh:
            return transport({'kind': 'ssh-system', 'host': ssh, 'discovery': service or DEFAULT_DISCOVERY,
                              'repo': repo or DEFAULT_REPO})
        path = service or os.environ.get('COSMESH_SERVICE', DEFAULT_DISCOVERY)
        if service or Path(path).is_file():
            settings = discovery(path)
            return transport({'kind': 'system', 'discovery': settings['path'],
                              'repo': repo or settings.get('feedback_repo', DEFAULT_REPO)})
        parent = Path(os.environ.get('COSMESH_DATA_DIR', str(Path.home() / '.local/share/cosmesh')))
        candidates = {}
        for product in ('cosmesh', 'cosbase'):
            client = parent / product / '.cosmesh/client.json'
            if client.is_file():
                remote = json.loads(client.read_text()).get('remote', {})
                if remote.get('kind') in ('system', 'ssh-system'):
                    remote = {**remote, 'repo': repo or DEFAULT_REPO}
                    candidates[json.dumps(remote, sort_keys=True)] = remote
        if len(candidates) != 1:
            raise Error('FEEDBACK_HUB_REQUIRED', 'Choose an existing SSH alias with feedback connect --ssh ALIAS.')
        return transport(next(iter(candidates.values())))

    def ensure_cache(self, config=None):
        self.directory(create=True)
        settings = self.root / 'connection.json'
        previous = self.read_json(settings) if settings.exists() else None
        config = transport(config or previous or self.infer())
        if previous and previous != config:
            raise Error('FEEDBACK_DESTINATION_CONFLICT', 'Use a separate COSMESH_FEEDBACK_DIR for another Hub.')
        if any(d['state'] == 'pending' and d.get('destination') and d['destination'] != config for _, d in self.drafts()):
            raise Error('FEEDBACK_DESTINATION_CONFLICT', 'Pending feedback belongs to a different Hub connection.')
        status = remote_status(config)
        if not status.get('system_feedback') or status['product'] != 'cosmesh':
            raise Error('POLICY_DENIED', 'This repository is not a shared system feedback channel.')
        if not self.store.root.exists():
            folder = Path(tempfile.mkdtemp(prefix='.clone-', dir=str(self.root)))
            try:
                candidate = folder / 'repo'
                clone(candidate, 'cosmesh', config, publish=False)
                candidate.rename(self.store.root)
            finally:
                shutil.rmtree(folder)
        else:
            if self.store.client().get('remote') != config:
                raise Error('FEEDBACK_DESTINATION_CONFLICT', 'The feedback cache belongs to another connection.')
            if self.store.client().get('user') != status['actor']:
                raise Error('AUTH_DENIED', 'The feedback connection now identifies a different user.')
        atomic(settings, json.dumps(config, indent=2) + '\n')
        return config

    def connect(self, **options):
        with self.locked('network'):
            config = self.infer(**options)
            self.ensure_cache(config)
            return {'configured': True, 'connection': config, 'visibility': 'hub_authenticated_users'}

    def pull(self):
        with self.locked('network'):
            self.ensure_cache()
            return synchronize(self.store, 'cosmesh', publish=False)[0]

    def sync(self):
        with self.locked('network'):
            self.ensure_cache()
            synchronize(self.store, 'cosmesh', publish=False)
            drafts = {d['id']: (p, d) for p, d in self.drafts() if d['state'] == 'pending'}
            ordered, visiting, visited = [], set(), set()
            def visit(doc_id):
                if doc_id in visited:
                    return
                if doc_id in visiting:
                    raise Error('STATE_CONFLICT', 'Pending feedback contains a reply cycle.')
                visiting.add(doc_id)
                parent = drafts[doc_id][1]['request'].get('reply_to')
                if parent in drafts:
                    visit(parent)
                visiting.remove(doc_id); visited.add(doc_id); ordered.append(drafts[doc_id])
            for doc_id in drafts:
                visit(doc_id)
            ordered = ordered[:100]
            staged = []
            for path, draft in ordered:
                with self.locked('outbox'):
                    if self.read_json(path)['state'] != 'pending':
                        continue
                    FeedbackService(self.store, 'cosmesh').run('feedback.add',
                        {k: draft[k] for k in ('id', 'request', 'client_version')}, key=draft['key'])
                    staged.append((path, draft))
            result, revision = synchronize(self.store, 'cosmesh')
            with self.locked('outbox'):
                for path, draft in staged:
                    current = self.read_json(path)
                    current.update(state='published', remote_revision=revision)
                    atomic(path, json.dumps(current, ensure_ascii=False, indent=2) + '\n')
            return {**result, 'published_count': len(staged), 'source_revision': revision}

    def read(self, query=None, product=None, category=None, mine=False, doc_id=None, limit=50):
        if not 1 <= limit <= 200:
            raise Error('INPUT_INVALID', 'Use a feedback limit from 1 to 200.')
        self.directory()
        records, source, actor = {}, None, None
        if self.store.path('.cosmesh/client.json').exists():
            client = self.store.client(); source = client.get('remote_revision'); actor = client.get('user')
            if source:
                _, files = self.store.snapshot(source)
                for doc in self.store.documents(files).values():
                    if doc['meta']['kind'] == 'update' and doc['meta'].get('feedback'):
                        entry = public(doc); entry['publication'] = 'published'; records[entry['id']] = entry
        for path, draft in self.drafts():
            if draft['state'] != 'discarded' and draft['id'] not in records:
                request = draft['request']
                records[draft['id']] = {'id': draft['id'], 'title': request['title'], 'body': request['body'],
                    'author': actor, 'created_at': draft['queued_at'], 'reply_to': request.get('reply_to'),
                    'feedback': {k: request[k] for k in ('category', 'product', 'component')},
                    'publication': draft['state']}
        base = {'source_revision': source, 'visibility': 'hub_authenticated_users_after_publish',
                'pending_count': sum(d['state'] == 'pending' for _, d in self.drafts())}
        if doc_id:
            identifier(doc_id)
            if doc_id not in records:
                raise Error('NOT_FOUND', 'No feedback with that ID in the current snapshot.')
            root, seen = doc_id, set()
            while records[root].get('reply_to') in records:
                if root in seen:
                    raise Error('STATE_CONFLICT', 'Feedback reply cycle.')
                seen.add(root); root = records[root]['reply_to']
            selected = {root}
            while True:
                more = {k for k, v in records.items() if v.get('reply_to') in selected}
                if more <= selected:
                    break
                selected |= more
            return {**base, 'feedback': records[root], 'replies': sorted((records[k] for k in selected if k != root), key=lambda d: (d['created_at'], d['id']))}
        matches = [r for r in records.values() if r['feedback']['category'] != 'reply' and
                   (not product or r['feedback']['product'] == product) and
                   (not category or r['feedback']['category'] == category) and
                   (not mine or r['author'] == actor or r['publication'] == 'pending') and
                   (not query or query.casefold() in (r['title'] + r['body']).casefold())]
        matches.sort(key=lambda d: (d['created_at'], d['id']), reverse=True)
        return {**base, 'records': matches[:limit], 'total': len(matches)}

    def discard(self, doc_id):
        identifier(doc_id)
        with self.locked('outbox'):
            for path, draft in self.drafts():
                if draft['id'] == doc_id:
                    if draft['state'] != 'pending' or self.store.path('documents/' + doc_id + '.md').exists():
                        raise Error('HISTORY_PROTECTED', 'Submitted feedback is preserved; append a reply or correction.')
                    draft['state'] = 'discarded'
                    atomic(path, json.dumps(draft, ensure_ascii=False, indent=2) + '\n')
                    return {'id': doc_id, 'discarded': True, 'retained_locally': True}
        raise Error('NOT_FOUND', 'No pending local feedback with that ID.')

    def status(self):
        result = self.read()
        result.pop('records'); result.pop('total')
        result['configured'] = (self.root / 'connection.json').is_file()
        result['root'] = str(self.root)
        result['sync'] = state(self.store) if self.store.path('.cosmesh/config.json').is_file() else None
        return result
