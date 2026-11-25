"""An owner-authored work increment with a deterministic input receipt."""
import hashlib
import json
import os

from . import _platform
from .errors import Error
from .store import atomic
from .threads import now


def private_root(wb):
    directory = '.cosmesh/project-updates/'
    exclude = wb.base.path('.git/info/exclude')
    if directory not in exclude.read_text(encoding="utf-8").splitlines():
        atomic(exclude, exclude.read_text(encoding="utf-8") + '\n' + directory + '\n')
    path = wb.base.path(directory)
    path.mkdir(mode=0o700, exist_ok=True)
    if path.is_symlink() or not _platform.private_path(path):
        raise Error('AUTH_DENIED', 'Project update checkpoints must be private.')
    return path


def signature(wb, tid):
    refs = [wb.ref(wb.find(tid)), *[wb.ref(r) for r in wb.records(tid)]]
    return hashlib.sha256(json.dumps(sorted(refs, key=lambda r: r['id']), sort_keys=True).encode()).hexdigest()


def update(wb, args, body, key=None, dry=False):
    item = wb.find(args.id, 'thread'); tid = item['doc']['meta']['id']; repo = item['repo']
    if not body.strip():
        raise Error('INPUT_INVALID', 'Provide the Agent-authored increment; an empty update creates no record.')
    if getattr(args, 'basis', None) and args.basis != signature(wb, tid):
        raise Error('STATE_CONFLICT', 'This Thread changed; compare the new records before retrying your increment.')
    if repo != 'local':
        raise Error('POLICY_DENIED','Write progress to your own work. Historical shared records are read-only.')
    kind = args.kind
    refs = [wb.citation(r) for r in args.ref]
    author = wb.author_for(item)
    digest = hashlib.sha256(json.dumps([tid, author, kind, body.strip(), refs], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    # Author/content comparison also catches a fresh invocation with a different operation key.
    existing = next((r for r in reversed(wb.records(tid)) if r['doc']['meta']['type'] == kind and
                     r['doc']['meta']['author'] == author and r['doc']['body'].strip() == body.strip() and
                     r['doc']['meta'].get('refs', []) == refs), None)
    if existing:
        record = wb.present(existing); disposition = 'unchanged'
    else:
        record = wb.append(tid, body, kind, refs=refs,
                           key=key or 'project-update-' + digest, dry_run=dry)
        disposition = 'replayed' if record.get('replayed') else 'created'
        if not dry:
            wb.load()
    publication = {'state':'private','accepted':False}
    if not dry:
        atomic(private_root(wb) / (tid + '.json'), json.dumps({'thread': tid, 'record': record['id'],
               'source': wb.ref(wb.find(record['id'])), 'input_digest': digest, 'operation_key': key or 'project-update-' + digest,
               'publication': publication, 'at': now()}, ensure_ascii=False))
    from .thread_chat import summary
    return {'format': 'thread.update.v1', 'thread': tid, 'disposition': disposition,
            'record': {k: v for k, v in record.items() if k not in {'body', 'generation'}},
            'summary': summary(body), 'saved_locally': not dry, 'dry_run': dry, 'publication': publication,
            'basis': signature(wb, tid), 'notifications': 'not_requested',
            'read_hint': '报告本次新增或复用的工作记录。本人同步可由收集器接续。'}
