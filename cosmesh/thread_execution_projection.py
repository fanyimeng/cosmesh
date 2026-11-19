"""Layered whitelist projections for the pure Thread execution model."""
from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json

from .thread_execution_model import FORMAT, build_execution_model


def _packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def compute_shared_revision(value):
    """Recompute the public digest from a shared projection, excluding its digest field."""
    if not isinstance(value, Mapping):
        raise ValueError('A shared execution projection must be a mapping.')
    public = {key: item for key, item in value.items() if key != 'shared_revision'}
    return hashlib.sha256(_packed(public).encode('utf-8')).hexdigest()


def _device_key(device):
    return ('device', device['id']) if device['id'] else ('host', device['host'].casefold())


def _summary(model):
    contexts = model['contexts']
    return {
        'device_count': len({_device_key(context['device']) for context in contexts}),
        'agent_count': len({context['agent'] for context in contexts}),
        'project_count': sum(context['project'] is not None for context in contexts),
        'context_count': len(contexts), 'removed_count': model['removed_count'],
    }


def _public_project(project):
    if project is None:
        return None
    return {key: project.get(key) for key in ('kind', 'id', 'name')}


def _shared_context(context):
    # Construct every layer from an allowlist; never redact an owner/raw object.
    return {
        'device': {key: context['device'].get(key) for key in ('id', 'host')},
        'agent': context['agent'], 'project': _public_project(context['project']),
        'status': context['status'], 'observed_at': context['observed_at'],
        'source': context['source'], 'verification': context['verification'],
    }


def _shared(model):
    result = {
        'format': FORMAT, 'audience': 'shared', 'execution_revision': model['execution_revision'],
        'summary': _summary(model), 'contexts': [_shared_context(context) for context in model['contexts']],
        'removed_count': model['removed_count'], 'truncated': False,
    }
    result['shared_revision'] = compute_shared_revision(result)
    return result


def _owner(model, current_device):
    shared = _shared(model)
    contexts = []
    for context in model['contexts']:
        item = _shared_context(context)
        item.update({
            'context_id': context['context_id'], 'workdir': context['workdir'],
            'sessions': deepcopy(context['sessions']),
            'current_device': bool(current_device and context['device']['id'] == current_device),
            'conflicts': deepcopy(context['conflicts']), 'recovery': deepcopy(context['recovery']),
        })
        contexts.append(item)
    summary = _summary(model)
    summary['session_count'] = sum(len(context['sessions']) for context in model['contexts'])
    return {
        'format': FORMAT, 'audience': 'owner', 'execution_revision': model['execution_revision'],
        'shared_revision': shared['shared_revision'], 'summary': summary, 'contexts': contexts,
        'removed_count': model['removed_count'], 'truncated': False, 'current_device': current_device,
    }


def project_execution(model, audience='shared', current_device=None):
    """Project an already folded model, preserving its execution revision."""
    if audience == 'shared':
        return _shared(model)
    if audience == 'owner':
        if current_device is not None and (not isinstance(current_device, str) or not current_device.strip()):
            raise ValueError('current_device must be a non-empty string or null.')
        return _owner(model, current_device.strip() if current_device else None)
    raise ValueError('Execution projection audience must be shared or owner.')


def shared_projection(raw_locations):
    """Build the Thread-reader projection directly from existing location rows."""
    return _shared(build_execution_model(raw_locations))


def validate_shared_projection(value):
    """Reject unknown or owner-only fields before caching a server projection."""
    if not isinstance(value, Mapping) or set(value) != {'format', 'audience', 'execution_revision',
            'summary', 'contexts', 'removed_count', 'truncated', 'shared_revision'}:
        raise ValueError('Invalid shared execution projection fields.')
    if value['format'] != FORMAT or value['audience'] != 'shared' or not isinstance(value['contexts'], list):
        raise ValueError('Invalid shared execution projection identity.')
    if set(value['summary']) != {'device_count', 'agent_count', 'project_count', 'context_count', 'removed_count'}:
        raise ValueError('Invalid shared execution summary fields.')
    for context in value['contexts']:
        if not isinstance(context, Mapping) or set(context) != {'device', 'agent', 'project', 'status', 'observed_at', 'source', 'verification'}:
            raise ValueError('Invalid shared execution context fields.')
        if not isinstance(context['device'], Mapping) or set(context['device']) != {'id', 'host'}:
            raise ValueError('Invalid shared device fields.')
        project = context['project']
        if project is not None and (not isinstance(project, Mapping) or set(project) != {'kind', 'id', 'name'}):
            raise ValueError('Invalid shared project fields.')
    if compute_shared_revision(value) != value['shared_revision']:
        raise ValueError('Shared execution revision differs from its contents.')
    return value


def owner_projection(raw_locations, current_device=None):
    """Build the owner-only projection, including private recovery metadata."""
    return project_execution(build_execution_model(raw_locations), 'owner', current_device)
