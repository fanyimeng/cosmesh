"""Maintain the project-local Agent instructions for an already bound local directory."""

import codecs
import ctypes
import os
import re
import secrets
import stat
import sys
import tempfile
from collections import deque
from pathlib import Path

from .errors import Error
from .store import safe_path

BEGIN = '<!-- cosmesh:project-guidance -->'
END = '<!-- /cosmesh:project-guidance -->'
RULES = {
    'startup': '开始本项目的工作时，使用 CosMesh Skill，以项目根目录运行 `cosmesh thread locate --workdir <项目根目录> --bound-projects --json`。有多个候选时按当前任务选择，仍有歧义时向用户核实。对选定的 Thread 用 `cosmesh thread continue <ID> --ready --json` 读取交接。刚换设备、已开启个人同步且需要最新进展时，加 `--require-fresh`。以接续回执中的 Thread ID 和活动状态为准。无匹配时照常工作，确需登记时再绑定。',
    'result': '取得有意义的阶段成果或作出重要决定时，使用 CosMesh Skill 按项目已有的记录与授权范围，将必要摘要、项目内的相对路径和下一步记入本次工作的同一 Thread，可用 `cosmesh thread result <ID> --body <摘要>`。共享与附件上传沿用项目已有授权。',
}
MAX_BYTES = 2 * 1024 * 1024
# Follow only explicitly referenced, project-local operating and handoff documents.
REFERENCE = re.compile(
    r'\]\(([^)\n]+)\)|`([^`\n]+\.(?:md|txt))`|'
    r'(?<![a-z0-9/.-])([a-z0-9_./-]+\.(?:md|txt)|README)(?![a-z0-9/.-])', re.I)
SYSTEM = re.compile(r'cos(?:mesh|base)|工作串|\bthread\b|W-[\da-f]{8}-', re.I)
HISTORICAL = re.compile(r'\d{4}[-/]\d{2}[-/]\d{2}|发布验收|回执|候选验收|历史状态|release notes|changelog', re.I)
OPT_OUT = re.compile(r'(?:禁止|不得|不要|切勿|不允许|不)\s*(?:自动|主动)?'
                     r'(?:使用|启用|调用|写入|接入)\s*(?:任何\s*)?cos(?:mesh|base)|'
                     r'(?:do not|never|must not) (?:use|enable|invoke|write to) cos(?:mesh|base)', re.I)
# Match prospective practices. A receipt, an ID, or a command in an example is insufficient.
START = re.compile(r'(?:先|动手前|开工时|开始时|启动时|新会话|后续在此目录).{0,60}'
                   r'(?:读|定位|续接|接续|continue|locate).{0,36}(?:thread|工作串)|'
                   r'(?:通过|沿用|续接|继续|恢复).{0,26}(?:thread|工作串).{0,10}(?:接续|继续|恢复)|'
                   r'(?:在任一设备|开工|先|开始|接手|接续时).{0,35}thread (?:continue|locate)\b|'
                   r'(?:start|begin|resume|continue|locate|read).{0,55}(?:the |this |existing |bound )thread', re.I)
RESULT = re.compile(r'(?:进展|增量|成果|结果|决定|结论|摘要).{0,32}'
                    r'(?:写|记|接到|追加|补入|存入|汇入|归入|回填).{0,24}(?:thread|工作串)|'
                    r'(?:thread|工作串).{0,20}(?:登记|记录|写入|增加|追加|记入|保存|归档).{0,18}'
                    r'(?:增量|成果|结果|进展|决定|结论|摘要)|'
                    r'(?:做完|结束|收尾).{0,12}(?:追加|登记|写入|记录).{0,10}(?:成果|结果|摘要)|'
                    r'(?:后续|阶段).{0,28}(?:note/result|thread result|collect summary).{0,14}(?:记录|登记|写入)|'
                    r'(?:milestone updates|incremental.{0,25}(?:update|record).{0,18}thread|'
                    r'(?:record|append|save|write|log).{0,35}(?:results?|progress|decisions?).{0,28}thread)', re.I)


def _read(path):
    """Open only a bounded ordinary file. Keep its exact byte representation for the final check."""
    safe_path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise Error('PATH_INVALID', 'Instruction input is not an ordinary single-link file.')
    if info.st_size > MAX_BYTES:
        raise Error('INPUT_TOO_LARGE', 'Project instructions exceed the inspection limit.')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(fd, 'rb') as handle:
        opened = os.fstat(handle.fileno())
        if not os.path.samestat(info, opened):
            raise Error('STATE_CONFLICT', 'Project instructions changed while opening.')
        data = handle.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise Error('INPUT_TOO_LARGE', 'Project instructions exceed the inspection limit.')
    return data, info


def _decode(data):
    for bom, encoding in ((codecs.BOM_UTF8, 'utf-8'), (codecs.BOM_UTF16_LE, 'utf-16-le'),
                          (codecs.BOM_UTF16_BE, 'utf-16-be')):
        if data.startswith(bom):
            break
    else:
        bom, encoding = b'', 'utf-8'
    text = data[len(bom):].decode(encoding)
    if '\x00' in text:
        raise Error('UNSUPPORTED_ENCODING', 'Project instructions contain binary data.')
    return text, bom, encoding


def _references(text, source):
    historical = False
    for line in text.splitlines():
        if line.startswith('#'):
            historical = bool(HISTORICAL.search(line))
        for reference in _line_references(line):
            if historical and not (source.name == 'AGENTS.md' and _thread_guide(reference)):
                continue
            yield reference


def _thread_guide(path):
    return bool(re.search(r'(?:cos(?:mesh|base)[_-].*thread|thread[_-](?:link|guide))', path.stem, re.I))


def _line_references(text):
    for match in REFERENCE.finditer(text):
        name = next(s for s in match.groups() if s).strip('<>').split('#')[0]
        if name == 'README':
            name = 'README.md'
        path = Path(name)
        if (path.is_absolute() or any(p.startswith('.') for p in path.parts)
                or ':' in name or '\\' in name or path.suffix.lower() not in {'.md', '.txt'}):
            continue
        if any(p.lower() in {'runs', 'build', 'dist', 'data', 'assets', 'results', 'examples', 'manual', 'tests'} for p in path.parts):
            continue
        if (path.parent == Path('.') and re.fullmatch(r'readme|(?:chat_)?context|handoff|agents', path.stem, re.I)
                or _thread_guide(path) or re.search(r'workspace[_-]layout|discussion[_-]record|instructions|operating[_-]guide|协作约定', path.stem, re.I)
                or re.search(r'项目指引|协作指引|工作目录约定|project (?:guidance|instructions)|operating guide', text, re.I)):
            yield path


def _instructions(text):
    """Classify prospective instructions separately from historical and product documentation."""
    covered = set()
    historical = False
    for paragraph in re.split(r'\n\s*\n', text):
        paragraph = ' '.join(paragraph.split())
        if paragraph.startswith('#'):
            historical = bool(HISTORICAL.search(paragraph))
        # A dated handoff can explicitly prescribe a continuing practice. Receipts and
        # capability descriptions in the same document do not establish project policy.
        for sentence in re.split(r'(?<=[。！？])|(?<=\. )', paragraph):
            sentence = sentence.strip()
            if not sentence:
                continue
            if re.search(r'后续.{0,45}note/result.{0,12}记录.{0,8}(?:进展|结果)', sentence, re.I) and SYSTEM.search(text):
                covered.add('result')
            if re.search(r'(?:禁止|不可|不得|不要|切勿)[^。]{0,12}(?:改写|修改|写入|更新)\s*`?AGENTS\.md|'
                         r'(?:do not|never|must not) (?:automatically )?(?:modify|edit|write|update) (?:this file|`?AGENTS\.md)', sentence, re.I):
                raise Error('PROJECT_POLICY', 'The project restricts automated changes to its instructions.')
            if not SYSTEM.search(sentence):
                continue
            if (historical or HISTORICAL.search(sentence) or re.search(r'已(?:经|完成|写入|绑定|保存|登记|回读|记录)', sentence)) and not re.search(
                    r'后续|已知 ID 的 Agent 接续|今后|每次|动手前|future incremental', sentence, re.I):
                continue
            restricted_write = re.search(
                r'(?:禁止|不要|不得|切勿|不允许|不可)(?:自动|主动)?(?:写入|记录|追加|更新|回写).{0,14}(?:thread|工作串)|'
                r'(?:仅|只|必须|需要).{0,18}(?:用户|本人).{0,8}(?:同意|授权|要求|允许).{0,12}'
                r'(?:使用|写入|记录|追加|更新|回写).{0,12}(?:cosmesh|cosbase|thread|工作串)|'
                r'(?:do not|never|must not).{0,10}(?:write|update|append).{0,16}(?:thread|cosmesh|cosbase)|'
                r'only (?:use|write to|update) (?:the )?(?:cosmesh|cosbase|thread) (?:when|with|if)|'
                r'(?:use|write to|update) (?:the )?(?:cosmesh|cosbase|thread) only (?:when|with|if)', sentence, re.I)
            if OPT_OUT.search(sentence) or restricted_write:
                raise Error('PROJECT_POLICY', 'The project has an explicit CosMesh restriction.')
            if START.search(sentence) or re.search(r'已知 ID 的 Agent 接续用 thread continue|Read status:\s.*thread (?:page|continue)\b', sentence, re.I):
                covered.add('startup')
            if RESULT.search(sentence) and not re.search(r'\bthread (?:personal|sync)\b', sentence, re.I):
                covered.add('result')
    return covered


def _coverage(root, text):
    found = set()
    pending = deque([(root / 'AGENTS.md', text, 0)])
    visited = {root / 'AGENTS.md'}
    while pending:
        source, content, depth = pending.popleft()
        found.update(_instructions(content))
        if depth >= 3 or len(visited) >= 32:
            continue
        for reference in _references(content, source):
            if len(visited) >= 32:
                break
            # Markdown links may be relative to the source document. Some project
            # handoffs also quote paths relative to the project root.
            for base in (source.parent, root):
                target = base / reference
                if target in visited:
                    break
                try:
                    data, _ = _read(target)
                    child, _, _ = _decode(data)
                except (OSError, UnicodeError, Error):
                    continue
                visited.add(target)
                pending.append((target, child, depth + 1))
                break
    return found


def _block(keys, newline):
    return newline.join([BEGIN, '## CosMesh 工作记录', '', *['- ' + RULES[k] for k in keys], END])


def _compose(root, text):
    newline = '\r\n' if '\r\n' in text else '\n'
    start, stop = text.find(BEGIN), text.find(END)
    if start >= 0 or stop >= 0:
        if text.count(BEGIN) != 1 or text.count(END) != 1 or stop < start:
            raise Error('PROJECT_POLICY', 'A project guidance marker is incomplete or duplicated.')
        stop += len(END)
        managed = text[start:stop]
        known = [k for k, rule in RULES.items() if rule in managed]
        if not known or managed != _block(known, newline):
            raise Error('PROJECT_POLICY', 'The project has a user-edited CosMesh guidance section.')
        outside = text[:start] + text[stop:]
    else:
        known, outside = [], text
    needed = [k for k in RULES if k not in _coverage(root, outside)]
    missing = [k for k in needed if k not in known]
    if not missing:
        return text, []
    block = _block([k for k in RULES if k in needed or k in known], newline)
    if known:
        return text[:start] + block + text[stop:], missing
    # Keep YAML front matter and the original document title before the new section.
    lead = re.match(r'\A---\r?\n.*?\r?\n(?:---|\.\.\.)(?:\r?\n|$)', text, re.S)
    at = lead.end() if lead else 0
    heading = re.match(r'(?:[ \t]*\r?\n)*#[ \t]+[^\r\n]*(?:\r?\n|$)', text[at:])
    if heading:
        at += heading.end()
    before, after = text[:at], text[at:]
    if before and not before.endswith('\n'):
        before += newline
    return before + (newline if before else '') + block + newline + (newline if after else '') + after, missing


def _unchanged(path, original, old):
    current, observed = _read(path)
    if (not os.path.samestat(old, observed) or observed.st_ctime_ns != old.st_ctime_ns
            or current != original):
        raise Error('STATE_CONFLICT', 'The project instructions changed during the update.')


def _write_windows(path, original, old, payload):
    fd, temporary = tempfile.mkstemp(prefix='.cosmesh-guidance-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        safe_path(path)
        if old is None:
            # On Windows rename fails if another writer created the target.
            os.rename(temporary, path)
        else:
            _unchanged(path, original, old)
            # ReplaceFileW retains the target DACL and named streams.
            replace = ctypes.WinDLL('kernel32', use_last_error=True).ReplaceFileW
            replace.argtypes = [ctypes.c_wchar_p] * 3 + [ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p]
            if not replace(str(path), temporary, None, 0, None, None):
                raise ctypes.WinError(ctypes.get_last_error())
    finally:
        Path(temporary).unlink(missing_ok=True)


def _copy_permissions(source, dest):
    """Preserve permissions, ACLs and extended attributes on an existing project file."""
    original, staged = os.fstat(source), os.fstat(dest)
    if sys.platform == 'darwin':
        # COPYFILE_ACL | COPYFILE_STAT | COPYFILE_XATTR, without file contents.
        if ctypes.CDLL(None, use_errno=True).fcopyfile(source, dest, None, 7):
            raise OSError(ctypes.get_errno(), 'Cannot preserve project file metadata.')
    else:
        if (original.st_uid, original.st_gid) != (staged.st_uid, staged.st_gid):
            os.fchown(dest, original.st_uid, original.st_gid)
        os.fchmod(dest, stat.S_IMODE(original.st_mode))
        if hasattr(os, 'listxattr'):
            for name in os.listxattr(source):
                os.setxattr(dest, name, os.getxattr(source, name))


def _write(path, original, old, payload):
    if os.name == 'nt':
        return _write_windows(path, original, old, payload)
    from . import _fcntl
    directory = os.open(safe_path(path.parent), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    stage = '.cosmesh-guidance-' + secrets.token_hex(12)
    staged = False
    try:
        # The directory inode gives all CosMesh processes a common lock across atomic renames.
        _fcntl.flock(directory, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        fd = os.open(stage, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600, dir_fd=directory)
        staged = True
        with os.fdopen(fd, 'wb') as file:
            file.write(payload)
            file.flush()
            if old is not None:
                src = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                try:
                    if not os.path.samestat(old, os.fstat(src)):
                        raise Error('STATE_CONFLICT', 'The project instructions were replaced.')
                    _copy_permissions(src, file.fileno())
                    if sys.platform == 'darwin':
                        os.utime(file.fileno(), None)  # copyfile also copied the old mtime.
                finally:
                    os.close(src)
            os.fsync(file.fileno())
        if not os.path.samestat(os.fstat(directory), safe_path(path.parent).stat()):
            raise Error('STATE_CONFLICT', 'The project directory was replaced.')
        if old is None:
            # Creation must never overwrite a file or symlink installed in the meantime.
            os.link(stage, path.name, src_dir_fd=directory, dst_dir_fd=directory)
        else:
            _unchanged(path, original, old)
            os.replace(stage, path.name, src_dir_fd=directory, dst_dir_fd=directory)
            staged = False
    finally:
        if staged:
            os.unlink(stage, dir_fd=directory)
        os.close(directory)


def ensure(directory, *, dry_run=False):
    """Plan or apply missing guidance for one local bound project; never create its directory."""
    receipt = {'directory': str(directory), 'dry_run': dry_run, 'missing': []}
    try:
        if not Path(directory).expanduser().is_absolute():
            raise Error('PATH_INVALID', 'Select an absolute local project root.')
        root = safe_path(directory)
        if not root.is_dir():
            raise Error('PATH_UNAVAILABLE', 'The project directory is unavailable.')
        excluded = {'.git', '.cosmesh', '.codex', '.agents', '.ssh', '.aws'}
        system = [Path(p) for p in ('/System', '/Library', '/Applications', '/usr', '/etc', '/bin',
                                     '/sbin', '/dev', '/proc', '/sys', '/boot', '/private/etc')]
        if os.name == 'nt':
            system = [Path(os.environ[k]) for k in ('SYSTEMROOT', 'PROGRAMFILES', 'PROGRAMFILES(X86)', 'PROGRAMDATA') if os.environ.get(k)]
        if (root == Path.home() or root in Path.home().parents or root == Path(root.anchor)
                or any(p.casefold() in excluded for p in root.parts)
                or any(root == p or p in root.parents for p in system)):
            raise Error('PATH_INVALID', 'Select a specific project directory outside system and private data roots.')
        override = root / 'AGENTS.override.md'
        if override.exists() or override.is_symlink():
            if _read(override)[0].strip():
                raise Error('PROJECT_POLICY', 'AGENTS.override.md takes precedence in this project.')
        path = root / 'AGENTS.md'
        try:
            data, old = _read(path)
        except FileNotFoundError:
            data, old = b'', None
        text, bom, encoding = _decode(data)
        updated, missing = _compose(root, text)
        receipt['missing'] = missing
        if not missing:
            return dict(receipt, status='unchanged', reason='guidance_present')
        if old and (not old.st_mode & 0o222 or (not dry_run and not os.access(path, os.W_OK))):
            raise PermissionError('The project instructions are read-only.')
        if not root.stat().st_mode & 0o222 or (not dry_run and not os.access(root, os.W_OK)):
            raise PermissionError('The project directory is read-only.')
        if not dry_run:
            _write(path, data, old, bom + updated.encode(encoding))
        return dict(receipt, status='updated' if old else 'created')
    except (OSError, UnicodeError, Error) as exc:
        reason = getattr(exc, 'code', type(exc).__name__)
        withheld = {'PATH_INVALID', 'PROJECT_POLICY', 'INPUT_TOO_LARGE', 'UNSUPPORTED_ENCODING', 'UnicodeDecodeError'}
        result = dict(receipt, status='skipped' if reason in withheld else 'pending', reason=reason)
        if isinstance(exc, Error):
            result['detail'] = exc.message
        return result
