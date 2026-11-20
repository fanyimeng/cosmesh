"""Pure normalization and event folding for Thread execution metadata."""
from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
import ntpath
import posixpath
import re


FORMAT = 'thread.execution.v2'
_AGENT_ALIASES = {'deepseek harness': 'dsh', 'deepseek-harness': 'dsh', 'open code': 'opencode'}
_WINDOWS_PATH = re.compile(r'^(?:[A-Za-z]:[\\/]|\\\\)')


def _packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_packed(value).encode('utf-8')).hexdigest()


def _text(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError('Execution metadata labels must be strings or null.')
    return value.strip() or None


def normalize_agent(value):
    """Normalize an Agent label without consulting the host or Workbench."""
    value = _text(value)
    if not value:
        raise ValueError('An execution location needs an Agent.')
    value = value.casefold()
    return _AGENT_ALIASES.get(value, value)


def normalize_workdir(value):
    """Lexically normalize a path; never resolve links or inspect the filesystem."""
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError('A working directory must be a non-empty string or null.')
    if '\x00' in value:
        raise ValueError('A working directory cannot contain NUL.')
    return ntpath.normpath(value) if _WINDOWS_PATH.match(value) else posixpath.normpath(value)


def _timestamp(value):
    value = _text(value)
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        raise ValueError('Execution timestamps must be timezone-aware ISO-8601 strings.')


def _location_rows(locations):
    if locations is None:
        return []
    if isinstance(locations, Mapping):
        if 'binding' in locations:
            return [(None, locations)]
        return list(locations.items())
    return [(None, row) for row in locations]


def _device_key(device, host):
    # An old host-only row never acquires a synthetic stable device identifier.
    return 'device:' + device + '@' + host.casefold() if device else 'host:' + host.casefold()


def _normalize_event(row, location_id):
    if not isinstance(row, Mapping):
        raise ValueError('Each execution location must be a mapping.')
    kind = row.get('kind')
    binding = row.get('binding')
    if kind not in {'bindings', 'project_bindings'} or not isinstance(binding, Mapping):
        raise ValueError('Unsupported execution location record.')
    raw = {name: row.get(name) for name in ('device', 'replica', 'kind', 'binding')}
    if location_id is not None and row.get('id') not in (None, location_id):
        raise ValueError('Location identity differs between its key and row.')
    location_id = _text(location_id if location_id is not None else row.get('id'))
    location_id = location_id or hashlib.sha256(_packed(raw).encode('utf-8')).hexdigest()
    device = _text(row.get('device'))
    host = _text(binding.get('host')) or ''
    agent = normalize_agent(binding.get('agent'))
    workdir = normalize_workdir(binding.get('workdir'))
    if kind == 'bindings':
        normalized = {'agent': agent, 'workdir': workdir, 'session_id': _text(binding.get('session_id'))}
    else:
        removed = binding.get('removed', False)
        if type(removed) is not bool:
            raise ValueError('Project removed markers must be boolean.')
        project_id = _text(binding.get('project_id'))
        inner_kind = (_text(binding.get('kind')) or ('directory' if project_id is None else 'project')).casefold()
        normalized = {
            'agent': agent, 'kind': inner_kind, 'name': _text(binding.get('name')),
            'project_id': project_id, 'workdir': workdir,
            'status': _text(binding.get('status')) or 'registered', 'removed': removed,
            'at': _timestamp(binding.get('at')), 'observed_at': _timestamp(binding.get('observed_at')),
            'source': _text(binding.get('source')), 'verification': _text(binding.get('verification')),
        }
    return {
        'id': location_id, 'device': device, 'host': host,
        'device_key': _device_key(device, host), 'replica': _text(row.get('replica')),
        'kind': kind, 'binding': normalized,
    }


def _event_key(event):
    return event['binding'].get('at') or '', event['id']


def _observation_key(event):
    return event['binding'].get('observed_at') or '', _event_key(event)


def _field(event, name):
    return event['host'] if name == 'host' else event['binding'].get(name)


def _conflicts(events, fields, latest_at=False):
    if not events:
        return []
    if latest_at:
        latest = max(events, key=_event_key)
        events = [event for event in events if event['binding'].get('at') == latest['binding'].get('at')]
    result = []
    for field in fields:
        values = {_packed(_field(event, field)): _field(event, field) for event in events}
        if len(values) > 1:
            result.append({'field': field, 'location_ids': sorted(event['id'] for event in events),
                           'values': [values[key] for key in sorted(values)]})
    return result


def _project_group(event):
    binding = event['binding']
    base = (event['device_key'], binding['agent'])
    if binding['project_id'] is not None:
        return ('native', *base, binding['project_id']), 'native'
    if binding['kind'] == 'directory' and binding['workdir'] is not None:
        return ('directory', *base, binding['workdir']), 'directory_workdir'
    # Without a native ID, retain both name and path when available rather than
    # guessing that different unnamed/non-directory projects are equivalent.
    fallback = (binding['workdir'], binding['name'])
    quality = 'name_workdir_fallback' if all(fallback) else 'name_or_workdir_fallback'
    return ('fallback', *base, *fallback), quality


def _fold_project(events, quality):
    latest = max(events, key=_event_key)
    binding = latest['binding']
    removed = max((_event_key(event) for event in events if event['binding']['removed']), default=None)
    active = [event for event in events if removed is None or _event_key(event) > removed]
    workdirs = sorted({event['binding']['workdir'] for event in active if event['binding']['workdir']})
    observations = [event for event in active if event['binding']['observed_at'] is not None
                    and all(event['binding'][key] == binding[key] for key in ('workdir', 'name'))]
    observed = max(observations, key=_observation_key) if observations else None
    evidence = observed['binding'] if observed else binding
    verified = bool(observed and evidence['status'] == 'verified' and evidence['source'] and evidence['verification'])
    conflicts = _conflicts(events, ('kind', 'name', 'workdir', 'status', 'removed', 'host'), latest_at=True)
    if observed:
        same_observation = [event for event in observations if event['binding']['observed_at'] == evidence['observed_at']]
        conflicts.extend(_conflicts(same_observation, ('source', 'verification')))
    return {
        'device_key': latest['device_key'], 'device': latest['device'], 'host': latest['host'],
        'agent': binding['agent'], 'native': binding['project_id'] is not None,
        'identity_quality': quality, 'workdirs': workdirs, 'workdir': binding['workdir'], 'removed': binding['removed'],
        'project': {'kind': binding['kind'], 'id': binding['project_id'], 'name': binding['name']},
        'status': 'verified' if verified else 'registered', 'observed_at': evidence['observed_at'],
        'source': evidence['source'], 'verification': evidence['verification'],
        'conflicts': conflicts,
    }


def _context_id(kind, device_key, agent, identity):
    return _digest({'format': FORMAT, 'kind': kind, 'device': device_key, 'agent': agent, 'identity': identity})


def _project_context(fold):
    native = fold['native']
    identity = fold['project']['id'] if native else (fold['identity_quality'], fold['workdir'], fold['project']['name'])
    workdir = fold['workdir']
    return {
        'context_id': _context_id('project', fold['device_key'], fold['agent'], identity),
        'device': {'id': fold['device'], 'host': fold['host']}, 'agent': fold['agent'],
        'project': fold['project'], 'status': fold['status'], 'observed_at': fold['observed_at'],
        'source': fold['source'], 'verification': fold['verification'], 'workdir': workdir,
        'workdirs': fold['workdirs'], 'sessions': [], 'conflicts': fold['conflicts'],
        'recovery': {'method': 'workdir' if workdir else 'metadata_only',
                     'status': 'registered' if workdir else 'metadata_only',
                     'project_match': 'unique' if native else 'directory_fallback',
                     'identity_quality': fold['identity_quality']},
        '_device_key': fold['device_key'], '_native': native,
    }


def _add_conflict(context, conflict):
    if all(_packed(existing) != _packed(conflict) for existing in context['conflicts']):
        context['conflicts'].append(conflict)


def _attach_directories(contexts, events):
    """Keep registered working directories, including those with no native Project."""
    projects = {}
    for context in contexts:
        for path in context['workdirs']:
            projects.setdefault((context['_device_key'], context['agent'], path), []).append(context)
    directories = {}
    for event in events:
        if event['kind'] != 'bindings':
            continue
        binding = event['binding']
        key = (event['device_key'], binding['agent'], binding['workdir'])
        directories.setdefault(key, []).append(event)
    for key, rows in directories.items():
        device, agent, path = key
        related = projects.get(key, [])
        sessions = sorted({row['binding']['session_id'] for row in rows if row['binding']['session_id']})
        if related and not sessions:
            continue
        natives = [c for c in related if c['_native']]
        match = 'ambiguous' if len(natives) > 1 else 'directory_clue' if related else 'unmatched'
        # A path is a recovery location. It cannot establish native Session membership.
        directory = next((c for c in related if c['project']['kind'] == 'directory'), None)
        if directory is None:
            directory = {
                'context_id': _context_id('directory', device, agent, path),
                'device': {'id': rows[0]['device'], 'host': rows[0]['host']}, 'agent': agent,
                'project': None, 'workdir': path, 'workdirs': [path] if path else [],
                'status': 'registered', 'observed_at': None, 'source': None, 'verification': None,
                'sessions': [], 'conflicts': [], 'recovery': {'identity_quality': 'directory_workdir'},
            }
            contexts.append(directory)
        directory['sessions'] = [{'id': sid, 'workdir': path, 'status': 'registered', 'project_match': match}
                                 for sid in sessions]
        directory['recovery'].update(method='host_session_id' if sessions else 'workdir',
                                     status='registered', project_match=match)
        if len(natives) > 1:
            _add_conflict(directory, {'field': 'project_id', 'reason': 'ambiguous_session_workdir',
                                      'values': sorted(c['project']['id'] for c in related if c['project']['id'])})


def _context_sort_key(context):
    device = context['device']
    identity = 'device:' + device['id'] if device['id'] else 'host:' + device['host'].casefold()
    project = context['project'] or {}
    return (identity, context['agent'], context['workdir'] or '', project.get('id') or '', context['context_id'])


def build_execution_model(locations):
    """Fold raw location rows into normalized active contexts and a stable revision.

    ``locations`` may be a list/iterable of rows, a manifest-like ``id -> row``
    mapping, or one raw row. Replica IDs participate only in the event revision.
    """
    events = []
    for key, row in _location_rows(locations):
        events.append(_normalize_event(row, key))
    by_id = {}
    for event in events:
        packed = _packed(event)
        if event['id'] in by_id and by_id[event['id']][0] != packed:
            raise ValueError('A location ID identifies two different events.')
        by_id[event['id']] = (packed, event)
    normalized_events = [by_id[key][1] for key in sorted(by_id)]
    revision = _digest({'format': FORMAT, 'events': normalized_events})

    groups = {}
    for event in normalized_events:
        if event['kind'] != 'project_bindings':
            continue
        key, quality = _project_group(event)
        groups.setdefault(key, (quality, []))[1].append(event)
    active, removed_count = [], 0
    for key in sorted(groups, key=_packed):
        quality, rows = groups[key]
        fold = _fold_project(rows, quality)
        if fold['removed']:
            removed_count += 1
        else:
            active.append(fold)

    contexts = [_project_context(fold) for fold in active]
    directories = {}
    for context in contexts:
        if context['_native']:
            for path in context['workdirs']:
                directories.setdefault((context['_device_key'], context['agent'], path), []).append(context)
    for group in directories.values():
        if len(group) > 1:
            candidates = sorted(context['project']['id'] for context in group)
            for context in group:
                _add_conflict(context, {'field': 'project_id', 'reason': 'multiple_native_projects_same_workdir',
                                        'values': candidates})
                context['recovery']['directory_ambiguous'] = True

    _attach_directories(contexts, normalized_events)
    for context in contexts:
        context['sessions'].sort(key=lambda session: session['id'])
        context['conflicts'] = [json.loads(packed) for packed in sorted({_packed(item) for item in context['conflicts']})]
        for key in list(context):
            if key.startswith('_'):
                del context[key]
    contexts.sort(key=_context_sort_key)
    return {'format': FORMAT, 'execution_revision': revision,
            'contexts': contexts, 'removed_count': removed_count}
