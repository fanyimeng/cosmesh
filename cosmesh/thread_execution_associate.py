"""Evidence-based association previews and idempotent confirmed writes."""
import hashlib
import json
import os
from pathlib import Path
import socket

from .agent_projects import active, agent_name, bind as bind_project
from . import _platform
from .errors import Error
from .personal_protocol import digest, packed
from .store import safe_path
from .thread_execution_model import build_execution_model, normalize_workdir
from .thread_execution_projection import shared_projection
from .thread_execution_reader import owner_execution, personal_state, raw_locations
from .thread_overview import host_snapshot
from .thread_updates import signature
from .threads import now


def _candidate_id(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def read_journal(value):
    """Read one explicitly selected private Connector journal, never its handoff."""
    path = safe_path(value)
    if path.is_symlink() or not path.is_file():
        raise Error('INPUT_INVALID', 'Select a regular Connector journal file.')
    info = path.stat()
    if not _platform.private_path(path, info) or info.st_size > 100000:
        raise Error('AUTH_DENIED', 'Connector journals must be private, owned and at most 100000 bytes.')
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        raise Error('INPUT_INVALID', 'The Connector journal is not valid bounded JSON.')
    if not isinstance(value, dict) or value.get('format') != 1 or not isinstance(value.get('input'), dict):
        raise Error('INPUT_INVALID', 'Unsupported Connector journal format.')
    return {'document': value, 'sha256': hashlib.sha256(raw).hexdigest()}


def _owned_active(wb, tid):
    item = wb.find(tid, 'thread')
    if item['repo'] != 'local' or item['doc']['meta']['author'] != wb.author_for(item):
        raise Error('AUTH_DENIED', 'Associate execution metadata only from an owner workbench.')
    shown = wb.present(item)
    if shown['lifecycle'] != 'active':
        raise Error('STATE_CONFLICT', 'Restore the Thread before changing its execution associations.')
    return shown['id']


def _synthetic_shared(raw, device, project):
    row = {'device': device, 'replica': None, 'kind': 'project_bindings', 'binding': project}
    locations = dict(raw)
    locations[digest(packed(row).encode())] = row
    return shared_projection(locations)


def _journal_row(wb, tid, journal, bindings):
    document = journal['document']
    if document['input'].get('thread_id') != tid:
        raise Error('INPUT_INVALID', 'The Connector journal belongs to another Thread.')
    owner = document.get('owner', {})
    machine = owner.get('host')
    if machine != socket.gethostname():
        raise Error('STATE_CONFLICT', 'The Connector journal belongs to another host.')
    steps = document.get('steps', {})
    if not isinstance(steps, dict) or not steps.get('workspace') or not steps.get('session'):
        raise Error('INCONSISTENT_EVIDENCE', 'The journal has no completed native Workspace and Session.')
    session_id, project_id = document.get('sessionId'), document.get('workspaceId')
    workdir = document.get('workdir') or document['input'].get('workdir')
    if not session_id or not project_id or not workdir:
        raise Error('INCONSISTENT_EVIDENCE', 'The journal lacks actual Workspace, Session or directory metadata.')
    try:
        workdir = normalize_workdir(str(Path(workdir).resolve(strict=True)))
    except OSError:
        raise Error('PATH_INVALID', 'The Connector journal directory is unavailable.')
    if steps.get('project_binding') and not any(row.get('project_id') == project_id for row in active(wb, tid)):
        raise Error('INCONSISTENT_EVIDENCE', 'The journal records a completed project binding that is currently missing.')
    if steps.get('session_binding') and not any(row['binding'].get('session_id') == session_id and agent_name(row['binding']['agent']) == 'dsh' for row in bindings):
        raise Error('INCONSISTENT_EVIDENCE', 'The journal records a completed Session binding that is currently missing.')
    return {'device': personal_state(wb).get('device'), 'replica': None, 'kind': 'bindings',
            'binding': {'thread': tid, 'agent': 'dsh', 'host': machine,
                        'workdir': workdir, 'session_id': session_id},
            '_journal_hash': journal['sha256'], '_journal_project': project_id}


def preview(wb, tid, host=None, journal=None):
    """Generate side-effect-free candidates from bounded host observations."""
    tid = _owned_active(wb, tid)
    raw = raw_locations(wb, tid)
    revision = build_execution_model(raw)['execution_revision']
    basis = signature(wb, tid)
    empty = {'thread': tid, 'private': True, 'status': 'no_candidates', 'candidates': [],
             'execution_revision': revision, 'thread_basis': basis,
             'shared_summary_preview': shared_projection(raw), 'automatic_association': False}
    if host is None:
        return empty
    observed = host_snapshot(host)
    sessions = {(agent_name(row['agent']), row['host'], row['id']): row for row in observed.get('sessions', [])}
    projects = {(agent_name(row['agent']), row['host'], row['id']): row for row in observed.get('projects', [])}
    # Only the local client table is authoritative for locally writable bindings.
    device = personal_state(wb).get('device')
    machine_name = socket.gethostname()
    bindings = [{'binding': binding, 'device': device}
                for binding in wb.client.get('bindings', [])
                if binding['thread'] == tid and binding['host'] == machine_name]
    if journal is not None:
        entry = _journal_row(wb, tid, journal, bindings)
        bindings = [row for row in bindings if row['binding'] != entry['binding']] + [entry]
    candidates = []
    for row in bindings:
        binding = row['binding']
        if not binding.get('session_id'):
            continue
        key = (agent_name(binding['agent']), binding['host'], binding['session_id'])
        session = sessions.get(key)
        if session is None or session['status'] in {'archived', 'unavailable'}:
            continue
        if row.get('_journal_project') and session.get('project_id') != row['_journal_project']:
            raise Error('INCONSISTENT_EVIDENCE', 'The native Session belongs to a different Project than the journal.')
        agent, machine = key[:2]
        matching = []
        if session.get('project_id'):
            project = projects.get((agent, machine, session['project_id']))
            if project is not None:
                evidence = 'connector_journal_host_verified' if row.get('_journal_hash') else 'session_project_membership'
                matching = [(project, evidence, True)]
        else:
            path = normalize_workdir(session.get('cwd') or binding.get('workdir'))
            matching = [(project, 'unique_workdir_clue', False) for project in projects.values()
                        if agent_name(project['agent']) == agent and project['host'] == machine
                        and path is not None and normalize_workdir(project.get('path')) == path]
        for project, evidence, confirmable in matching:
            workdir = normalize_workdir(session.get('cwd') or binding.get('workdir') or project.get('path'))
            paths = [normalize_workdir(value) for value in (binding.get('workdir'), session.get('cwd')) if value]
            if workdir is None or any(value != workdir for value in paths):
                candidates.append({'status': 'conflict', 'reason': 'Registered and observed directories differ.',
                                   'device': {'id': row.get('device'), 'host': machine}, 'agent': agent})
                continue
            kind = 'workspace' if agent == 'dsh' else 'project'
            try:
                registration = bind_project(wb, tid, agent, project['name'], project['id'], kind=kind,
                                            host=machine, workdir=project.get('path') or workdir, observation=observed, dry_run=True)
            except Error as exc:
                candidates.append({'status': 'conflict', 'reason': exc.message,
                                   'device': {'id': row.get('device'), 'host': machine}, 'agent': agent})
                continue
            identity = {'thread': tid, 'device': row.get('device'), 'host': machine, 'agent': agent,
                        'project_id': project['id'], 'project_name': project['name'], 'project_path': project.get('path'),
                        'workdir': workdir, 'session_id': session['id'],
                        'observed_at': observed['observed_at'], 'source': observed['source'],
                        'evidence': evidence, 'journal_sha256': row.get('_journal_hash')}
            candidate = {
                'candidate_id': _candidate_id(identity), 'base_revision': revision,
                'device': {'id': row.get('device'), 'host': machine}, 'agent': agent,
                'project': {'kind': kind, 'id': project['id'], 'name': project['name']},
                'workdir': workdir, 'session_id': session['id'],
                'evidence': [{'code': evidence, 'source': observed['source'],
                              'observed_at': observed['observed_at']}],
                'confirmable': confirmable, 'already_linked': registration['unchanged'],
                'project_workdir': registration['project']['workdir'],
            }
            candidate['shared_summary_preview'] = _synthetic_shared(raw, row.get('device'), registration['project'])
            candidates.append(candidate)
    linked = [row for row in candidates if row.get('already_linked')]
    confirmable = [row for row in candidates if row.get('confirmable') and not row.get('already_linked')]
    if linked and not confirmable:
        status = 'already_linked'
    elif len(confirmable) == 1:
        status = 'confirmation_required'
    elif len(confirmable) > 1 or len(candidates) > 1:
        status = 'ambiguous'
    else:
        status = 'no_candidates'
    for candidate in candidates:
        if not candidate.get('confirmable') or (status == 'ambiguous' and not candidate['already_linked']):
            candidate.pop('candidate_id', None)
    result = dict(empty, status=status, candidates=candidates)
    if status == 'confirmation_required':
        result['shared_summary_preview'] = confirmable[0]['shared_summary_preview']
    return result


def missing(wb):
    """List unassociated local Threads without probing projects or writing data."""
    rows = []
    for item in wb.catalog.values():
        if item['repo'] != 'local' or item['doc']['meta']['kind'] != 'thread':
            continue
        shown = wb.present(item)
        if shown['lifecycle'] != 'active':
            continue
        model = build_execution_model(raw_locations(wb, shown['id']))
        if any(context['project'] is not None for context in model['contexts']):
            continue
        rows.append({'thread': shown['id'], 'title': shown['title'],
                     'execution_revision': model['execution_revision'], 'status': 'unassociated'})
    return {'private': True, 'threads': rows, 'count': len(rows), 'automatic_association': False,
            'next': 'Use thread associate THREAD --host-file OBSERVED.json for evidence-based candidates.'}


def confirm(wb, tid, candidate_id, expect, host, key=None, journal=None):
    """Confirm one candidate using the existing operation-receipt idempotency boundary."""
    if not candidate_id or not expect:
        raise Error('INPUT_INVALID', 'Confirmation needs a candidate and its execution revision.')
    tid = _owned_active(wb, tid)
    payload = {'thread': tid, 'candidate': candidate_id, 'base_revision': expect}

    def build():
        result = preview(wb, tid, host, journal)
        matches = [row for row in result['candidates'] if row.get('candidate_id') == candidate_id]
        if len(matches) != 1:
            raise Error('STATE_CONFLICT', 'The association candidate changed; preview it again.')
        candidate = matches[0]
        if not candidate['confirmable']:
            raise Error('STATE_CONFLICT', 'The host has not confirmed Session membership.')
        current = result['execution_revision']
        if current != expect and not candidate.get('already_linked'):
            raise Error('STATE_CONFLICT', 'Execution metadata advanced; preview the candidate again.')
        registration = bind_project(wb, tid, candidate['agent'], candidate['project']['name'],
                                    candidate['project']['id'], kind=candidate['project']['kind'],
                                    host=candidate['device']['host'], workdir=candidate['project_workdir'],
                                    observation=host, dry_run=True)
        session = wb.bind(tid, candidate['agent'], candidate['workdir'], candidate['session_id'],
                          candidate['device']['host'], dry_run=True)['binding']
        changed = False
        if not registration['unchanged']:
            wb.client.setdefault('project_bindings', []).append({**registration['project'], 'at': now()})
            changed = True
        if session not in wb.client['bindings']:
            wb.client['bindings'].append(session)
            changed = True
        if changed:
            wb.save_client()
        metadata = owner_execution(wb, tid)
        status = 'unchanged' if not changed else 'applied'
        return {}, {'id': tid, 'thread': tid, 'status': status, 'candidate_id': candidate_id,
                    'execution_revision': metadata['execution_revision'],
                    'shared_revision': metadata['shared_revision'],
                    'shared_summary_preview': shared_projection(raw_locations(wb, tid)),
                    'association': {'device': candidate['device'], 'agent': candidate['agent'],
                                    'project_id': candidate['project']['id'], 'session_id': candidate['session_id'],
                                    'evidence': candidate['evidence'][0]['code']},
                    'records_created': 0}

    return wb._commit(wb.base, 'associate', payload, build, key or 'associate-' + candidate_id[:32])
