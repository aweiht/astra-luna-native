"""Reviewed managed-key installation, legacy upgrades and conflict-safe rollback."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat
import sys
import time
import tomllib

from . import __version__, toml_edit
from .platform import atomic, command_text, encode, file_lock, loads, read, safe

ROOT = Path(__file__).resolve().parents[1]
RESOURCE = 'astra-luna-native'
ENTRY = RESOURCE + '/runtime/astra-luna.py'
MARKER = 'ASTRA_LUNA_NATIVE'
EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')
RUNTIME_FILES = ('astra-luna.py', 'astra_luna/__init__.py', 'astra_luna/cli.py',
 'astra_luna/install.py', 'astra_luna/toml_edit.py', 'astra_luna/platform.py',
 'astra_luna/policy.py', 'astra_luna/verify.py', 'astra_luna/transport.py', 'astra_luna/process_tree.py',
 'astra_luna/process_registry.py',
 'astra_luna/assets/parent.md',
 'astra_luna/assets/leaf.md', 'astra_luna/assets/contract.md',
 'astra_luna/assets/normalize_tags.py', 'astra_luna/assets/unique_numbers.py')
# The public 0.4.0 schema-4 receipt predates the process supervisor and registry.
# This complete baseline is required for upgrades; extra files must still belong
# to the known owned_paths set. New receipts include all current RUNTIME_FILES.
PREVIOUS_SCHEMA4_RUNTIME = ('astra-luna.py', 'astra_luna/__init__.py', 'astra_luna/cli.py',
 'astra_luna/install.py', 'astra_luna/toml_edit.py', 'astra_luna/platform.py',
 'astra_luna/policy.py', 'astra_luna/verify.py', 'astra_luna/transport.py',
 'astra_luna/assets/parent.md', 'astra_luna/assets/leaf.md', 'astra_luna/assets/contract.md',
 'astra_luna/assets/normalize_tags.py', 'astra_luna/assets/unique_numbers.py')
LEGACY_RUNTIME = ('astra-luna', 'VERSION', 'scripts/native.py', 'scripts/install_native.py',
 'scripts/native_smoke.py', 'scripts/verify_native_events.py', 'internal/configmerge/toml_edit.py',
 'templates/native/parent.md', 'templates/native/leaf.md', 'templates/smoke/contract.md',
 'templates/smoke/normalize.py', 'templates/smoke/unique.py', 'templates/smoke/acceptance.py')
DESIRED = {'model':'"gpt-6-astra"', 'model_reasoning_effort':'"max"',
 'agents.enabled':'true', 'agents.default_subagent_model':'"gpt-5.6-luna"',
 'agents.default_subagent_reasoning_effort':'"max"',
 'agents.max_threads':'5', 'agents.max_concurrent_threads_per_session':'5'}


def sha(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def legacy(schema):
    result = {f'agents/adaptive_luna_{e}.toml' for e in EFFORTS}
    if schema < 3:
        result.add(RESOURCE + '/luna-selector')
    if schema == 2:
        result.update(RESOURCE + '/runtime/' + p for p in LEGACY_RUNTIME)
    if schema == 3:
        result.add(RESOURCE + '/runtime/astra-luna')
    if schema == 4:
        result.update(RESOURCE + '/runtime/' + p for p in RUNTIME_FILES)
    return result


def owned_paths():
    return legacy(2) | legacy(3) | legacy(4)


def previous_schema4():
    result = {f'agents/adaptive_luna_{e}.toml' for e in EFFORTS}
    result.update(RESOURCE + '/runtime/' + p for p in PREVIOUS_SCHEMA4_RUNTIME)
    return result


def block_span(raw):
    begin = f'<!-- BEGIN {MARKER} -->'.encode()
    end = f'<!-- END {MARKER} -->'.encode()
    if not raw.count(begin) and not raw.count(end):
        return None
    if raw.count(begin) != 1 or raw.count(end) != 1:
        raise ValueError('ambiguous native instruction block')
    a, b = raw.index(begin), raw.index(end) + len(end)
    if b <= a:
        raise ValueError('invalid instruction block order')
    return a, b


def transform(raw, mode, desired=None, changes=None):
    result = toml_edit.run({'input': (raw or b'').decode('utf-8'), 'mode': mode,
                           'desired': desired or {}, 'changes': changes or []})
    if result['conflicts']:
        raise ValueError('managed configuration changed')
    return result['output'].encode('utf-8'), result['changes']


def desired(raw):
    obj = tomllib.loads((raw or b'').decode('utf-8'))
    if obj.get('model_provider', 'openai') != 'openai':
        raise ValueError('official openai provider required; existing provider preserved')
    result = dict(DESIRED)
    agents = obj.get('agents', {})
    if 'max_threads' not in agents:
        result.pop('agents.max_threads')
    if 'max_threads' in agents and 'max_concurrent_threads_per_session' not in agents:
        result.pop('agents.max_concurrent_threads_per_session')
    return result


def _same_real_directory(left, right):
    """Accept only an existing, non-link directory identity with a case alias."""
    left, right = Path(left), Path(right)
    if not left.is_absolute() or not right.is_absolute():
        return False
    def has_link_component(path):
        current = Path(path.anchor)
        for part in path.parts[1:]:
            current /= part
            try:
                info = current.lstat()
            except (OSError, ValueError):
                return False
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                return True
        return False
    if has_link_component(left) or has_link_component(right):
        return False
    try:
        left_info, right_info = left.lstat(), right.lstat()
    except (OSError, ValueError):
        return False
    if any(stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400
           for info in (left_info, right_info)):
        return False
    if not stat.S_ISDIR(left_info.st_mode) or not stat.S_ISDIR(right_info.st_mode):
        return False
    try:
        same_identity = os.path.samefile(left, right)
    except (OSError, ValueError):
        return False
    if not same_identity:
        return False
    # The identity check above is the platform-sensitive gate.  This second
    # check keeps ``samefile`` from broadening the receipt contract to an
    # arbitrary spelling such as a different path reached through ``..``.
    return os.path.normpath(os.fspath(left)).casefold() == os.path.normpath(os.fspath(right)).casefold()


def load(home):
    raw = read(home / RESOURCE / 'install-manifest.json')
    if raw is None:
        return None
    receipt = loads(raw)
    schema = receipt.get('schema')
    recorded_home = receipt.get('codex_home')
    if (type(schema) is not int or schema not in (1, 2, 3, 4) or
            type(recorded_home) is not str or
            (recorded_home != str(home) and not _same_real_directory(Path(recorded_home), home))):
        raise ValueError('invalid installation receipt')
    if receipt.get('status') == 'uninstalled':
        return None
    if receipt.get('status') != 'installed':
        raise ValueError('invalid receipt status')
    owned = receipt.get('owned_files', {})
    required = legacy(schema)
    if not isinstance(owned, dict):
        raise ValueError('invalid receipt paths')
    owned_keys = set(owned)
    if not required <= owned_keys:
        if schema != 4 or not previous_schema4() <= owned_keys:
            raise ValueError('invalid receipt paths')
    if not owned_keys <= owned_paths():
        raise ValueError('invalid receipt paths')
    if schema < 3 and set(owned) != required:
        raise ValueError('invalid legacy receipt paths')
    if any(not isinstance(h, str) or not re.fullmatch('[0-9a-f]{64}', h) for h in owned.values()):
        raise ValueError('invalid receipt hash')
    seen = set()
    for row in receipt.get('config_changes', []):
        key = row.get('key')
        if key in seen or key not in DESIRED or row.get('after') != DESIRED[key]:
            raise ValueError('invalid managed configuration receipt')
        seen.add(key)
        before = row.get('before')
        if before is not None:
            value = tomllib.loads('v=' + before)['v']
            if type(value) is not toml_edit.TYPES[key]:
                raise ValueError('invalid original configuration type')
    block = receipt.get('block', '')
    if not block.startswith(f'<!-- BEGIN {MARKER} -->\n') or not block.endswith(f'\n<!-- END {MARKER} -->'):
        raise ValueError('invalid instruction receipt')
    return receipt


def validate(home):
    receipt = load(home)
    if receipt is None:
        return None
    for relative, expected in receipt['owned_files'].items():
        if sha(read(home / relative)) != expected:
            raise ValueError('managed file changed or missing: ' + relative)
    raw = read(home / 'AGENTS.md') or b''
    span = block_span(raw)
    if span is None or raw[slice(*span)].decode('utf-8') != receipt['block']:
        raise ValueError('managed instruction block changed')
    transform(read(home / 'config.toml'), 'revert', changes=receipt['config_changes'])
    return receipt


def native_block(home, codex_home=None):
    template = read(ROOT / 'astra_luna/assets/parent.md').decode('utf-8')
    block_home = Path(codex_home) if codex_home is not None else Path(home)
    invocation = command_text([sys.executable, '-I', '-B', str(block_home / ENTRY), '--codex-home', str(block_home)])
    project_arg = '.'
    content = template.replace('{{COMMAND}}', invocation).replace('{{PROJECT}}', project_arg)
    return (f'<!-- BEGIN {MARKER} -->\n' + content.strip() + f'\n<!-- END {MARKER} -->').encode('utf-8')


def role(effort):
    import json
    instruction = read(ROOT / 'astra_luna/assets/leaf.md').decode('utf-8')
    return (f'name = "adaptive_luna_{effort}"\n'
            f'description = "Luna {effort} leaf worker selected by the daily policy; bounded execution only."\n'
            'model = "gpt-5.6-luna"\n'
            f'model_reasoning_effort = "{effort}"\n'
            f'developer_instructions = {json.dumps(instruction, ensure_ascii=False)}\n\n'
            '[agents]\nenabled = false\n\n[shell_environment_policy.set]\nORCHESTRATOR_WORKER = "1"\n').encode('utf-8')


def build(home, uninstall=False):
    home = Path(os.path.abspath(home))
    inputs = {}
    def get(relative):
        value = read(home / relative)
        inputs[relative] = sha(value)
        return value
    journal = get(RESOURCE + '/transaction.json')
    if journal is not None and loads(journal).get('status') == 'applying':
        raise ValueError('unfinished transaction; run recover')
    if get('AGENTS.override.md') is not None:
        raise ValueError('AGENTS.override.md takes precedence')
    config, instructions = get('config.toml'), get('AGENTS.md')
    manifest = get(RESOURCE + '/install-manifest.json')
    receipt = validate(home)
    for relative in sorted(owned_paths()):
        get(relative)
    raw = instructions or b''
    span = block_span(raw)
    if receipt is None and span:
        raise ValueError('unowned instruction marker')
    records = []
    def add(relative, before, after, mode=0o600):
        if before == after:
            return
        before_mode = stat.S_IMODE((home / relative).stat().st_mode) if before is not None else None
        if relative in ('config.toml', 'AGENTS.md') and before_mode is not None:
            mode = before_mode
        records.append(dict(path=relative, before=before, after=after, before_mode=before_mode, mode=mode))
    sources = {}
    if uninstall:
        if receipt is None:
            raise ValueError('not installed')
        new_config, _ = transform(config, 'revert', changes=receipt['config_changes'])
        plan_changes = [dict(key=row['key'], before=row['after'], after=row['before'])
                        for row in receipt['config_changes']]
        a, b = span
        if raw[b:b+1] == b'\n':
            b += 1
        add('config.toml', config, new_config)
        add('AGENTS.md', instructions, raw[:a] + raw[b:])
        for relative in sorted(receipt['owned_files']):
            add(relative, get(relative), None)
        next_receipt = dict(receipt, status='uninstalled')
    else:
        new_config, changes = transform(config, 'merge', desired=desired(config))
        plan_changes = list(changes)
        if receipt:
            if changes:
                raise ValueError('existing managed defaults changed')
            changes = receipt['config_changes']
        block_home = receipt.get('codex_home') if receipt else str(home)
        block = native_block(home, block_home)
        if span:
            a, b = span
            new_instructions = raw[:a] + block + raw[b:]
        else:
            new_instructions = raw + (b'\n' if raw and not raw.endswith(b'\n') else b'') + block + b'\n'
        add('config.toml', config, new_config)
        add('AGENTS.md', instructions, new_instructions)
        payloads = {f'agents/adaptive_luna_{e}.toml': role(e) for e in EFFORTS}
        for relative in RUNTIME_FILES:
            data = read(ROOT / relative)
            if data is None:
                raise ValueError('runtime source missing: ' + relative)
            sources[relative] = sha(data)
            payloads[RESOURCE + '/runtime/' + relative] = data
        owned = dict(receipt['owned_files']) if receipt else {}
        for relative, data in sorted(payloads.items()):
            before = get(relative)
            if before is not None and (not receipt or relative not in owned):
                raise ValueError('unowned installation destination: ' + relative)
            add(relative, before, data)
            owned[relative] = sha(data)
        next_receipt = dict(schema=4, version=__version__, status='installed', codex_home=block_home,
                            config_changes=changes, block=block.decode('utf-8'), owned_files=owned)
    add(RESOURCE + '/install-manifest.json', manifest, encode(next_receipt))
    summary = dict(mode='uninstall' if uninstall else 'install', codex_home=str(home),
       files=[dict(path=r['path'], before_sha256=sha(r['before']), after_sha256=sha(r['after']),
                   before_mode=r['before_mode'], mode=r['mode']) for r in records],
       input_sha256=inputs, source_sha256=sources, python_executable=sys.executable,
       config_changes=plan_changes, native_block=next_receipt['block'],
       per_session_limit=5, new_hooks=0, new_mcp_servers=0)
    summary['plan_id'] = sha(encode(summary))
    return home, records, summary


def restore(home, journal):
    if journal.get('schema') != 1 or journal.get('status') not in ('applying', 'committed'):
        raise ValueError('invalid or already recovered transaction')
    backup = Path(journal['backup'])
    if not _same_real_directory(backup.parent, home / RESOURCE / 'backups'):
        raise ValueError('backup outside managed installation')
    safe(backup / 'transaction.json')
    allowed = owned_paths() | {'config.toml', 'AGENTS.md', RESOURCE + '/install-manifest.json'}
    originals, seen = {}, set()
    for row in journal['files']:
        relative = row['path']
        if relative not in allowed or relative in seen:
            raise ValueError('invalid transaction target')
        seen.add(relative)
        if sha(read(home / relative)) not in (row['before_sha256'], row['after_sha256']):
            raise ValueError('post-transaction change preserved')
        if row['before_sha256'] is not None:
            name = row['backup_file']
            mode = row['before_mode']
            if not re.fullmatch(r'\d+\.bin', name) or type(mode) is not int or not 0 <= mode <= 0o777:
                raise ValueError('invalid backup entry')
            data = read(backup / name)
            if sha(data) != row['before_sha256']:
                raise ValueError('backup hash mismatch')
            originals[relative] = data
    for row in reversed(journal['files']):
        target = home / row['path']
        current = sha(read(target))
        if current == row['before_sha256']:
            continue
        if current != row['after_sha256']:
            raise ValueError('concurrent rollback change preserved')
        if row['before_sha256'] is None:
            target.unlink()
        else:
            atomic(target, originals[row['path']], row['before_mode'])
        if sha(read(target)) != row['before_sha256']:
            raise ValueError('rollback verification failed')
    journal['status'] = 'rolled_back'
    atomic(backup / 'transaction.json', encode(journal))
    atomic(home / RESOURCE / 'transaction.json', encode(journal))


def recover(home, backup=None):
    home = Path(os.path.abspath(home))
    with file_lock(home / RESOURCE / 'install.lock'):
        path = Path(backup) / 'transaction.json' if backup else home / RESOURCE / 'transaction.json'
        raw = read(path)
        if raw is None:
            raise ValueError('no recovery transaction')
        journal = loads(raw)
        restore(home, journal)
        return dict(status='ROLLBACK_EXACT_PASS', backup=journal['backup'])


def apply(home, expected, uninstall=False, *, _fail_after=-1):
    home = Path(os.path.abspath(home))
    with file_lock(home / RESOURCE / 'install.lock'):
        home, records, summary = build(home, uninstall)
        if not expected or expected != summary['plan_id']:
            raise ValueError('stale plan; generate a fresh plan')
        if not records:
            return dict(status='IDEMPOTENT_PASS', plan_id=expected)
        backup = home / RESOURCE / 'backups' / (str(time.time_ns()) + '-' + expected[:10])
        backup.mkdir(parents=True, mode=0o700)
        journal = dict(schema=1, status='applying', backup=str(backup), files=[])
        for index, row in enumerate(records):
            name = str(index) + '.bin'
            if row['before'] is not None:
                atomic(backup / name, row['before'])
                if sha(read(backup / name)) != sha(row['before']):
                    raise ValueError('backup verification failed')
            journal['files'].append(dict(path=row['path'], backup_file=name,
               before_sha256=sha(row['before']), after_sha256=sha(row['after']), before_mode=row['before_mode']))
        atomic(backup / 'transaction.json', encode(journal))
        atomic(home / RESOURCE / 'transaction.json', encode(journal))
        try:
            for relative, expected_hash in summary['input_sha256'].items():
                if relative != RESOURCE + '/transaction.json' and sha(read(home / relative)) != expected_hash:
                    raise ValueError('concurrent input change preserved')
            for index, row in enumerate(records):
                if index == _fail_after:
                    raise RuntimeError('injected write failure')
                target = home / row['path']
                if sha(read(target)) != sha(row['before']):
                    raise ValueError('concurrent write change preserved')
                if row['after'] is None:
                    target.unlink()
                else:
                    atomic(target, row['after'], row['mode'])
                if sha(read(target)) != sha(row['after']):
                    raise ValueError('post-write verification failed')
            journal['status'] = 'committed'
            atomic(home / RESOURCE / 'transaction.json', encode(journal))
            atomic(backup / 'transaction.json', encode(journal))
        except BaseException:
            journal['status'] = 'applying'
            restore(home, journal)
            raise
        return dict(status='UNINSTALLED' if uninstall else 'INSTALLED', plan_id=expected,
                    backup=str(backup), changed_files=[r['path'] for r in records])
