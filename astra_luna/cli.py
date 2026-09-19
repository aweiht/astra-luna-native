"""One portable command for setup, policy and independent verification."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

from . import __version__, install
from .platform import atomic, clean_env, encode, loads, read, run


def resolve_codex(home, explicit=None):
    value = explicit
    if not value:
        raw = read(home / install.RESOURCE / 'capabilities.json')
        if raw:
            value = loads(raw).get('codex')
    value = value or 'codex'
    found = shutil.which(value)
    if not found:
        raise ValueError('official Codex CLI required; install it and run codex login')
    return os.path.abspath(found)


def doctor(home, codex=None):
    from . import policy
    result = dict(version=__version__, codex_home=str(home), status='NOT_READY',
                  checks={}, errors=[], live_verified=False, verification_level='offline_installation_only')
    try:
        receipt = install.validate(home)
        if receipt is None:
            raise ValueError('not installed; run install')
        if receipt['schema'] != 4 or receipt.get('version') != __version__:
            raise ValueError('installation version differs; run install to upgrade')
        install.build(home, uninstall=True)
        raw = read(home / 'config.toml')
        _, changes = install.transform(raw, 'merge', desired=install.desired(raw))
        if changes:
            raise ValueError('Astra/Luna defaults differ from installation')
        result['checks']['managed_files_and_config'] = 'PASS'
        command = resolve_codex(home, codex)
        result['checks']['capabilities'] = policy.doctor_capabilities(home, command)
        if result['checks']['capabilities'].get('status') != 'READY':
            raise ValueError('public capability snapshot or CLI unavailable; run refresh')
        cached = policy.cached_select(home)
        if cached.get('role') not in ('adaptive_luna_' + e for e in install.EFFORTS):
            raise ValueError('policy cache unavailable or stale; run refresh')
        result['checks']['policy'] = cached
        env = clean_env()
        env['CODEX_HOME'] = str(home)
        version = run([sys.executable, '-B', str(home / install.ENTRY), '--version'], env=env, timeout=8)
        if version.returncode or version.stdout.strip().decode() != __version__:
            raise ValueError('installed Python entry version mismatch')
        result['status'] = 'READY_FALLBACK' if cached.get('status') == 'fallback' else 'READY'
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as error:
        result['errors'] = [str(error) if type(error) in (ValueError, RuntimeError) else 'input or file operation failed']
    return result


def parser():
    value = argparse.ArgumentParser(description='Astra/Luna Native: Python 3.11+, standard library only.')
    value.add_argument('command', nargs='?', choices=('install', 'uninstall', 'recover', 'rollback',
        'doctor', 'select', 'refresh', 'verify', 'internal-smoke-check'))
    value.add_argument('--version', action='version', version=__version__)
    value.add_argument('--codex-home')
    value.add_argument('--codex', help='official Codex CLI path')
    value.add_argument('--project')
    value.add_argument('--output')
    value.add_argument('--backup')
    value.add_argument('--plan-id')
    value.add_argument('--yes', action='store_true', help='apply the managed, backed-up change')
    value.add_argument('--dry-run', action='store_true')
    value.add_argument('--explain', action='store_true')
    value.add_argument('--live', action='store_true', help='use real Codex allowance for two native tasks')
    return value


def show(value):
    print(encode(value).decode('utf-8'), end='')


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    seen = set()
    for arg in arguments:
        if arg.startswith('--'):
            key = arg.split('=', 1)[0]
            if key in seen:
                show(dict(status='ERROR', error='duplicate command option'))
                return 1
            seen.add(key)
    cli = parser()
    args = cli.parse_args(arguments)
    if args.command is None:
        cli.print_help()
        return 0
    home = Path(os.path.abspath(os.path.expanduser(args.codex_home or os.environ.get('CODEX_HOME') or '~/.codex')))
    try:
        command = args.command
        if args.yes and args.dry_run:
            raise ValueError('--yes and --dry-run cannot be combined')
        if args.live and command != 'verify':
            raise ValueError('--live is only valid for verify')
        if command == 'internal-smoke-check':
            from . import verify
            if not args.project:
                raise ValueError('--project is required')
            result = verify.check(Path(os.path.abspath(args.project)))
            show(result)
            return 0 if result.get('ok') else 1
        if command in ('recover', 'rollback'):
            if not args.yes:
                raise ValueError('recover/rollback requires --yes')
            show(install.recover(home, args.backup))
            return 0
        if command in ('install', 'uninstall'):
            uninstall = command == 'uninstall'
            _, _, plan = install.build(home, uninstall)
            if args.dry_run or not args.yes:
                show(plan)
                return 0
            expected = args.plan_id or plan['plan_id']
            snapshot = None
            if not uninstall:
                from . import policy
                codex = resolve_codex(home, args.codex)
                snapshot = policy.preflight(home, codex)
            result = install.apply(home, expected, uninstall)
            if snapshot is not None:
                atomic(home / install.RESOURCE / 'capabilities.json', encode(snapshot))
                try:
                    selected = policy.select(home, force=True)
                    if selected.get('role') not in ('adaptive_luna_' + e for e in install.EFFORTS):
                        raise ValueError('no supported selection')
                except (RuntimeError, ValueError, OSError):
                    result.update(status='INSTALLED_NEEDS_REFRESH', next_step='run refresh', live_verified=False)
                    show(result)
                    return 1
                result.update(policy=selected, live_verified=False,
                    standalone_entrypoint=str(home / install.ENTRY),
                    next_step='open a new Codex task; doctor or verify --live')
            show(result)
            return 0
        from . import policy
        if command == 'doctor':
            result = doctor(home, args.codex)
            show(result)
            return 0 if result['status'].startswith('READY') else 1
        if command in ('select', 'refresh', 'verify'):
            receipt = install.validate(home)
            if receipt is None or receipt['schema'] != 4:
                raise ValueError('Python edition not installed; run install')
            if os.environ.get('ORCHESTRATOR_WORKER') == '1':
                raise ValueError('leaf workers must not refresh or delegate')
        if command == 'select':
            project = Path(os.path.abspath(args.project or os.getcwd()))
            selected = policy.select(home, project)
            if selected.get('role') not in ('adaptive_luna_' + e for e in install.EFFORTS):
                raise ValueError('selection unavailable; run refresh; no role dispatched')
            if args.explain:
                show(selected)
            else:
                print(selected['role'])
            return 0
        if command == 'refresh':
            policy.refresh_capabilities(home, resolve_codex(home, args.codex))
            selected = policy.select(home, force=True)
            if selected.get('role') not in ('adaptive_luna_' + e for e in install.EFFORTS):
                raise ValueError('selection unavailable after refresh')
            show(selected)
            return 0
        if command == 'verify':
            if not args.live:
                raise ValueError('verify requires --live; it uses real Codex allowance')
            codex = resolve_codex(home, args.codex)
            readiness = doctor(home, codex)
            if not readiness['status'].startswith('READY'):
                show(readiness)
                return 1
            from . import verify
            if args.project:
                project = Path(os.path.abspath(args.project))
            else:
                project = Path(tempfile.mkdtemp(prefix='astra-luna-verify-')).resolve() / 'project'
            output = Path(os.path.abspath(args.output)) if args.output else project.parent / ('verification-' + str(time.time_ns()))
            role = policy.select(home)['role']
            if role not in ('adaptive_luna_' + e for e in install.EFFORTS):
                raise ValueError('selection unavailable; no role dispatched')
            print('Starting two native Luna tasks (up to 600 seconds); this uses Codex allowance.', file=sys.stderr)
            result = verify.run(home, codex, project, output, role)
            show(result)
            return 0 if result.get('status') == 'LIVE_VERIFIED' else 1
        raise ValueError('unknown command')
    except KeyboardInterrupt:
        show(dict(status='INTERRUPTED'))
        return 130
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as error:
        # TOML/JSON parser errors may include user content; never print it.
        if type(error) not in (ValueError, RuntimeError):
            message = 'input or file operation failed; inspect paths and managed changes'
        else:
            message = str(error)
        show(dict(status='ERROR', error=message))
        return 1
