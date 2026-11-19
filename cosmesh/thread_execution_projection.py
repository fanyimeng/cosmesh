"""Owner recovery state and the small, separately approved Project summary."""
from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json

from .thread_execution_model import FORMAT, build_execution_model

SHARED_FORMAT = 'thread.projects.v1'


def compute_shared_revision(value):
    encoded = json.dumps(value['contexts'], ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _label(value):
    return (isinstance(value, str) and 0 < len(value) <= 500 and value.isprintable()
            and not any(char in value for char in ('/', chr(92))))


def _shared(model):
    labels = {(c['agent'], c['project']['kind'], c['project']['name']) for c in model['contexts']
              if c['project'] and c['project']['kind'] in ('project', 'workspace')
              and _label(c['agent']) and _label(c['project']['name'])}
    contexts = [{'agent': agent, 'project': {'kind': kind, 'name': name}}
                for agent, kind, name in sorted(labels)]
    result = {'format': SHARED_FORMAT, 'audience': 'shared', 'contexts': contexts,
              'summary': {'project_count': len(contexts)}}
    result['shared_revision'] = compute_shared_revision(result)
    return result


def project_execution(model, audience='shared', current_device=None):
    if audience == 'shared':
        return _shared(model)
    if audience != 'owner':
        raise ValueError('Execution projection audience must be shared or owner.')
    contexts = deepcopy(model['contexts'])
    for row in contexts:
        row['current_device'] = bool(current_device and row['device']['id'] == current_device)
    return {'format': FORMAT, 'audience': 'owner', 'execution_revision': model['execution_revision'],
            'shared_revision': _shared(model)['shared_revision'], 'contexts': contexts,
            'summary': {'device_count': len({(c['device']['id'] or c['device']['host']) for c in contexts}),
                        'agent_count': len({c['agent'] for c in contexts}),
                        'project_count': sum(c['project'] is not None for c in contexts),
                        'context_count': len(contexts), 'removed_count': model['removed_count'],
                        'session_count': len({(c['device']['id'] or c['device']['host'], c['agent'], s['id'])
                                              for c in contexts for s in c['sessions']})},
            'current_device': current_device, 'removed_count': model['removed_count'], 'truncated': False}


def shared_projection(raw_locations):
    return _shared(build_execution_model(raw_locations))


def validate_shared_projection(value):
    if not isinstance(value, Mapping) or set(value) != {'format', 'audience', 'contexts', 'summary', 'shared_revision'}:
        raise ValueError('Invalid shared execution projection fields.')
    rows = value['contexts']
    if (value['format'] != SHARED_FORMAT or value['audience'] != 'shared' or not isinstance(rows, list)
            or value['summary'] != {'project_count': len(rows)}):
        raise ValueError('Invalid shared Project summary.')
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {'agent', 'project'} or not _label(row['agent']):
            raise ValueError('Invalid shared Agent label.')
        project = row['project']
        if (not isinstance(project, Mapping) or set(project) != {'kind', 'name'}
                or project['kind'] not in ('workspace', 'project') or not _label(project['name'])):
            raise ValueError('Invalid shared Project label.')
    if compute_shared_revision(value) != value['shared_revision']:
        raise ValueError('Invalid shared Project revision.')
    return value


def owner_projection(raw_locations, current_device=None):
    return project_execution(build_execution_model(raw_locations), 'owner', current_device)
