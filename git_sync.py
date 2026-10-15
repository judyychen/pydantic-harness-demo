"""Host-only GitHub synchronization of one cloud HEAD; no credentials enter the sandbox.

Configuration: {"enabled": true, "remote": REMOTE, "branch": BRANCH}.
Run the entirely local Git checks with: python git_sync.py --self-check
"""
import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import uuid

REMOTE = 'https://github.com/judyychen/pydantic-harness-demo.git'
REPO = 'judyychen/pydantic-harness-demo'
# Keep generated application checkpoints separate from the workbench source.
BRANCH = 'app-checkpoints'
REF = 'refs/heads/' + BRANCH
URL = 'https://github.com/' + REPO + '/tree/' + BRANCH
MAX_BUNDLE = 20_000_000
_HEAD = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')
_LOCKS = {}
_UNSET = object()
_SECRET = re.compile(
    rb'-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----'
    rb'|(?<![A-Za-z0-9])(?:github_pat_[A-Za-z0-9_]{30,}|gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|e2b_[A-Za-z0-9_-]{24,})'
    rb'|\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'
    rb'|https?://[^\s/:@]+:[^\s/@]+@'
    rb'|(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s/:@]+:[^\s/@]+@'
    rb'|(?i:(?:api[_-]?key|secret[_-]?key|aws_secret_access_key|access[_-]?token|password)["\']?\s*[:=]\s*["\']?[A-Za-z0-9_+/=-]{20,})'
)


class SyncFailure(Exception):
    def __init__(self, code, remote_head=_UNSET):
        super().__init__(code)
        self.remote_head = remote_head


def _config(path):
    if path is None or not Path(path).exists():
        return False
    raw = Path(path).read_bytes()
    if len(raw) > 4096:
        raise SyncFailure('ConfigRejected')
    value = json.loads(raw)
    if not isinstance(value, dict) or type(value.get('enabled')) is not bool:
        raise SyncFailure('ConfigRejected')
    if value['enabled'] and (value.get('remote') != REMOTE or value.get('branch') != BRANCH):
        raise SyncFailure('ConfigRejected')
    return value['enabled']


def _receipt(state):
    path = Path(state) / 'git-sync-status.json'
    return json.loads(path.read_text()) if path.exists() else {}


def _value(status, commit=None, remote_head=None, error=None):
    commit = commit if isinstance(commit, str) and _HEAD.fullmatch(commit) else None
    remote_head = remote_head if isinstance(remote_head, str) and _HEAD.fullmatch(remote_head) else None
    confirmed = status == 'confirmed' and commit == remote_head and bool(commit)
    return {'status': status, 'repo': REPO, 'branch': BRANCH, 'url': URL,
            'local_head': commit, 'remote_head': remote_head,
            'confirmed': confirmed, 'remote_saved': confirmed, 'error_type': error}


def sync_status(state, config_path):
    """Read safe receipt metadata only; never connect, renew, scan source or authenticate."""
    try:
        enabled = _config(config_path)
        old = _receipt(state)
        status = old.get('status', 'pending') if enabled else 'disabled'
        if status not in ('pending', 'confirmed', 'failed', 'disabled'):
            status = 'failed'
        error = old.get('error_type') if enabled else None
        if error is not None and (not isinstance(error, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', error)):
            error = 'ReceiptInvalid'
        return _value(status, old.get('local_head'), old.get('remote_head'), error)
    except Exception as exc:
        return _value('failed', error=str(exc) if isinstance(exc, SyncFailure) else type(exc).__name__)


def _save(state, value):
    target = Path(state) / 'git-sync-status.json'
    temp = target.with_name(target.name + '.' + uuid.uuid4().hex + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    os.chmod(temp, 0o600)
    temp.replace(target)


def _git(repo, *args, check=True):
    # Ignore inherited Git settings and all global/system configuration. gh reads its
    # own local authentication only when Git calls the fixed credential helper.
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT='0',
               GIT_NO_REPLACE_OBJECTS='1')
    command = ['git', '-c', 'core.hooksPath=' + os.devnull,
               '-c', 'transfer.fsckObjects=true',
               '-c', 'credential.helper=', '-c', 'credential.helper=!gh auth git-credential']
    if repo is not None:
        command += ['--git-dir=' + str(repo)]
    result = subprocess.run([*command, *args], env=env, capture_output=True, timeout=45)
    if check and result.returncode:
        raise SyncFailure('GitCommandFailed')
    return result


def _bundle_head(bundle):
    lines = _git(None, 'bundle', 'list-heads', str(bundle)).stdout.decode('ascii').splitlines()
    if len(lines) != 1:
        raise SyncFailure('BundleRefsRejected')
    fields = lines[0].split()
    if len(fields) != 2 or fields[1] != 'HEAD' or not _HEAD.fullmatch(fields[0]):
        raise SyncFailure('BundleRefsRejected')
    return fields[0]


def _allowed_path(raw, mode):
    if mode not in (b'100644', b'100755'):
        return False  # No submodules or symlinks in this standalone Next experiment.
    name = raw.decode('utf-8')
    parts = name.lower().split('/')
    denied = {'.git', '.gitmodules', '.ssh', '.aws', '.pydantic-ai-harness',
              '.netrc', '.npmrc', '.pypirc', '.git-credentials',
              '.owner.json', '.dreamable-next-spike.json', '.dreamable-spike.json',
              'node_modules', '.next', '__pycache__', '.cache', '.turbo', 'coverage',
              'dist', 'retention-probe', 'cloud-git-probe', 'state-private', 'state-next',
              'late-write-probe.txt', 'tsconfig.tsbuildinfo',
              'credentials.json', 'credentials.yml', 'credentials.yaml'}
    return (not any(ord(c) < 32 or c == '\\' for c in name)
            and not any(p in denied or p.startswith('.env') for p in parts)
            and not parts[-1].endswith(('.pem', '.key', '.p12', '.pfx', '.sqlite', '.sqlite3', '.db')))


def _scan(repo, commit):
    if _git(repo, 'rev-parse', '--is-shallow-repository').stdout.strip() != b'false':
        raise SyncFailure('IncompleteHistoryRejected')
    commits = _git(repo, 'rev-list', commit).stdout.decode('ascii').splitlines()
    if not commits or len(commits) > 1000:
        raise SyncFailure('HistoryLimitExceeded')
    objects = set(commits)
    for revision in commits:
        entries = _git(repo, 'ls-tree', '-r', '-z', revision).stdout.split(b'\0')
        for entry in filter(None, entries):
            metadata, path = entry.split(b'\t', 1)
            mode, kind, oid = metadata.split()
            if kind != b'blob' or not _allowed_path(path, mode):
                raise SyncFailure('HistoryPathRejected')
            objects.add(oid.decode('ascii'))
    if len(objects) > 10_000:
        raise SyncFailure('HistoryLimitExceeded')
    total = 0
    for oid in objects:
        size = int(_git(repo, 'cat-file', '-s', oid).stdout)
        total += size
        if size > MAX_BUNDLE or total > 100_000_000:
            raise SyncFailure('ObjectLimitExceeded')
        if _SECRET.search(_git(repo, 'cat-file', '-p', oid).stdout):
            raise SyncFailure('HistorySecretRejected')


def _remote_head(repo, remote):
    lines = _git(repo, 'ls-remote', '--heads', remote, REF).stdout.decode('ascii').splitlines()
    if not lines:
        return None
    if len(lines) != 1:
        raise SyncFailure('RemoteRefsRejected')
    fields = lines[0].split()
    if len(fields) != 2 or fields[1] != REF or not _HEAD.fullmatch(fields[0]):
        raise SyncFailure('RemoteRefsRejected')
    return fields[0]


def _publish(bundle, commit, directory, remote):
    """Private Git primitive also used against disposable local self-check remotes.

    Production callers always pass REMOTE after strict public configuration validation.
    This function never imports configuration or checks out files from the cloud.
    """
    with tempfile.TemporaryDirectory(prefix='mirror-', dir=directory) as temporary:
        repo = Path(temporary) / 'mirror.git'
        _git(None, 'init', '--bare', '-q', str(repo))
        _git(repo, 'bundle', 'verify', str(bundle))
        if _bundle_head(bundle) != commit:
            raise SyncFailure('BundleHeadMismatch')
        _git(repo, '-c', 'protocol.file.allow=always', 'fetch', '--no-tags', str(bundle), 'HEAD:refs/heads/source')
        _scan(repo, commit)
        before = _remote_head(repo, remote)
        if before:
            exists = _git(repo, 'cat-file', '-e', before + '^{commit}', check=False)
            ancestor = _git(repo, 'merge-base', '--is-ancestor', before, commit, check=False) if exists.returncode == 0 else None
            if ancestor is None or ancestor.returncode != 0:
                raise SyncFailure('NonFastForwardRejected', before)
        pushed = _git(repo, 'push', '--porcelain', remote, 'refs/heads/source:' + REF, check=False)
        if pushed.returncode:
            code = 'NonFastForwardRejected' if b'non-fast-forward' in pushed.stderr or b'fetch first' in pushed.stderr else 'GitPushFailed'
            raise SyncFailure(code, before)
        after = _remote_head(repo, remote)
        if after != commit:
            raise SyncFailure('RemoteHeadMismatch', after)
        return _value('confirmed', commit, after)


async def _cloud_bundle(workspace, commit, target):
    from pydantic_ai.workspaces import Workspace
    facade = Workspace(workspace)
    remote = '/tmp/dreamable-next-git-sync-' + uuid.uuid4().hex + '.bundle'
    try:
        # Fixed executable, repo and revision. No credentials, remote or model command.
        command = ['git', '-C', '/workspace', '-c', 'core.hooksPath=/dev/null',
                   'bundle', 'create', remote, 'HEAD']
        result = await workspace.run(command, timeout=45)
        if result.exit_code:
            raise SyncFailure('CloudBundleFailed')
        size = (await facade.stat(remote)).size
        if size is None or not 0 < size <= MAX_BUNDLE:
            raise SyncFailure('BundleSizeRejected')
        raw = await facade.read_bytes(remote)
        if not isinstance(raw, (bytes, bytearray)) or len(raw) != size:
            raise SyncFailure('BundleReadRejected')
        temp = target.with_name(target.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            temp.write_bytes(bytes(raw))
            os.chmod(temp, 0o600)
            if _bundle_head(temp) != commit:
                raise SyncFailure('BundleHeadMismatch')
            temp.replace(target)
        finally:
            temp.unlink(missing_ok=True)
    finally:
        try:
            await facade.remove(remote)
        except Exception:
            # A paused/unavailable sandbox may prevent cleanup; preserve the original
            # failure instead of making the model believe its file write failed twice.
            pass


async def sync_source(workspace, state, commit, config_path, *, force=False):
    """Confirm one checkpoint remotely; never raise ordinary Git/config failures to a tool."""
    state = Path(state).resolve()
    lock = _LOCKS.setdefault(str(state), asyncio.Lock())
    async with lock:
        previous = sync_status(state, config_path)
        if previous['status'] == 'disabled':
            return previous
        # This is the last confirmed receipt, not a fresh remote observation. A
        # changed HEAD takes the full path; failed same-HEAD attempts remain retryable.
        if not force and previous['confirmed'] and previous['local_head'] == commit:
            return previous
        value = _value('failed', commit, previous.get('remote_head'))
        cancelled = False
        try:
            if not _config(config_path):
                return _value('disabled')
            if not isinstance(commit, str) or not _HEAD.fullmatch(commit):
                raise SyncFailure('CommitRejected')
            directory = state / 'git-sync'
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(directory, 0o700)
            value = _value('pending', commit, previous.get('remote_head'))
            _save(state, value)
            bundle = directory / 'source.bundle'
            cached = False
            if bundle.exists() and 0 < bundle.stat().st_size <= MAX_BUNDLE:
                try:
                    cached = _bundle_head(bundle) == commit
                except Exception:
                    pass
            if not cached:
                await _cloud_bundle(workspace, commit, bundle)
            with ThreadPoolExecutor(max_workers=1) as publisher:
                thread = publisher.submit(_publish, bundle, commit, directory, REMOTE)
                publishing = asyncio.wrap_future(thread)
                while True:
                    try:
                        value = await asyncio.shield(publishing)
                        break
                    except asyncio.CancelledError:
                        # Keep the actual thread future: cancelling its asyncio wrapper
                        # cannot hide a running push or produce an immediate-error loop.
                        cancelled = True
                        if publishing.done():
                            if thread.cancelled():
                                raise SyncFailure('PublisherCancelled')
                            if thread.done():
                                try:
                                    value = thread.result()
                                except asyncio.CancelledError:
                                    raise SyncFailure('PublisherCancelled') from None
                                break
                            publishing = asyncio.wrap_future(thread)
            value['bundle_sha256'] = hashlib.sha256(bundle.read_bytes()).hexdigest()
        except asyncio.CancelledError:
            cancelled = True
            value = _value('failed', commit, previous.get('remote_head'), 'SyncCancelled')
        except Exception as exc:
            observed = getattr(exc, 'remote_head', _UNSET)
            value = _value('failed', commit,
                           previous.get('remote_head') if observed is _UNSET else observed,
                           str(exc) if isinstance(exc, SyncFailure) else type(exc).__name__)
        try:
            _save(state, value)
        except Exception:
            value['error_type'] = 'ReceiptPersistFailed'
        if cancelled:
            raise asyncio.CancelledError
        return value


def self_check():
    """Real Git, local remotes only. Public production target validation stays strict."""
    with tempfile.TemporaryDirectory(prefix='next-git-sync-check-') as temporary:
        root = Path(temporary)
        work = root / 'work'
        _git(None, 'init', '-q', '-b', 'main', str(work))
        _git(work / '.git', 'config', 'user.name', 'Local sync check')
        _git(work / '.git', 'config', 'user.email', 'sync@example.invalid')
        remote = root / 'remote.git'
        _git(None, 'init', '--bare', '-q', str(remote))
        def revision(text):
            (work / 'page.txt').write_text(text)
            _git(work / '.git', '--work-tree=' + str(work), 'add', 'page.txt')
            _git(work / '.git', '--work-tree=' + str(work), 'commit', '-qm', text)
            return _git(work / '.git', 'rev-parse', 'HEAD').stdout.decode().strip()
        heads = [revision(text) for text in ('first', 'second', 'third')]
        bundle = root / 'source.bundle'
        _git(work / '.git', 'bundle', 'create', str(bundle), 'HEAD')
        assert _bundle_head(bundle) == heads[-1]
        assert _publish(bundle, heads[-1], root, str(remote))['remote_saved']
        assert _publish(bundle, heads[-1], root, str(remote))['remote_saved']
        assert _git(remote, 'rev-list', '--count', REF).stdout.strip() == b'3'
        assert _git(remote, 'rev-list', REF).stdout.decode().splitlines() == list(reversed(heads))
        older = root / 'older.bundle'
        _git(work / '.git', 'update-ref', 'refs/heads/older', heads[0])
        _git(work / '.git', 'symbolic-ref', 'HEAD', 'refs/heads/older')
        _git(work / '.git', 'bundle', 'create', str(older), 'HEAD')
        try:
            _publish(older, heads[0], root, str(remote))
            raise AssertionError('non-fast-forward accepted')
        except SyncFailure as exc:
            assert str(exc) == 'NonFastForwardRejected'
        _git(work / '.git', 'symbolic-ref', 'HEAD', 'refs/heads/main')
        _git(work / '.git', 'update-ref', 'refs/heads/main', heads[-1])
        (work / '.env.local').write_text('unimportant fixture\n')
        _git(work / '.git', '--work-tree=' + str(work), 'add', '.env.local')
        _git(work / '.git', '--work-tree=' + str(work), 'commit', '-qm', 'forbidden historical path')
        _git(work / '.git', '--work-tree=' + str(work), 'rm', '-q', '.env.local')
        _git(work / '.git', '--work-tree=' + str(work), 'commit', '-qm', 'delete forbidden file')
        unsafe = root / 'unsafe.bundle'
        _git(work / '.git', 'bundle', 'create', str(unsafe), 'HEAD')
        unsafe_head = _bundle_head(unsafe)
        try:
            _publish(unsafe, unsafe_head, root, str(remote))
            raise AssertionError('deleted historical credential path accepted')
        except SyncFailure as exc:
            assert str(exc) == 'HistoryPathRejected'
        assert _remote_head(remote, str(remote)) == heads[-1]
        # Only HEAD is exported: the forbidden-path commits above are deliberately
        # unreachable here. A secret in a deleted historical blob still must fail.
        _git(work / '.git', 'update-ref', 'refs/heads/main', heads[-1])
        revision('const token = "ghp_' + 'X' * 36 + '";')
        secret_head = revision('removed token')
        secret_bundle = root / 'secret.bundle'
        _git(work / '.git', 'bundle', 'create', str(secret_bundle), 'HEAD')
        try:
            _publish(secret_bundle, secret_head, root, str(remote))
            raise AssertionError('deleted historical secret accepted')
        except SyncFailure as exc:
            assert str(exc) == 'HistorySecretRejected'
        assert _remote_head(remote, str(remote)) == heads[-1]
        assert _SECRET.search(b'-----BEGIN PRIVATE KEY-----')
        assert _SECRET.search(b'github_pat_' + b'X' * 40)
        assert _SECRET.search(b'postgresql://user:short@localhost/database')
        assert _SECRET.search(b'{"aws_secret_access_key":"ExampleOnly123456789012345678901234567890"}')
        assert not _allowed_path(b'credentials.yml', b'100644')
        assert not _allowed_path(b'.aws/config', b'100644')
        assert not _SECRET.search(b'const api_key = process.env.API_KEY;')
        class NoCloud:
            async def run(self, *args, **kwargs):
                raise AssertionError('production validation reached cloud')
        config = root / 'config.json'
        config.write_text(json.dumps({'enabled': True, 'remote': str(remote), 'branch': BRANCH}))
        result = asyncio.run(sync_source(NoCloud(), root, heads[-1], config))
        assert not result['remote_saved'] and result['error_type'] == 'ConfigRejected'
        config.write_text(json.dumps({'enabled': True, 'remote': REMOTE, 'branch': 'wrong'}))
        result = asyncio.run(sync_source(NoCloud(), root, heads[-1], config))
        assert result['error_type'] == 'ConfigRejected'
        config.write_text(json.dumps({'enabled': True, 'remote': REMOTE, 'branch': BRANCH}))
        _save(root, _value('confirmed', heads[-1], heads[-1]))
        result = asyncio.run(sync_source(NoCloud(), root, heads[-1], config))
        assert result['remote_saved'] and result['local_head'] == heads[-1]
        async def cancellation_checks():
            global _publish, _cloud_bundle
            original_publish, original_cloud = _publish, _cloud_bundle
            original_wrap = asyncio.wrap_future
            wrappers = []
            def capture_wrap(*args, **kwargs):
                result = original_wrap(*args, **kwargs)
                wrappers.append(result)
                return result
            asyncio.wrap_future = capture_wrap
            directory = root / 'git-sync'
            directory.mkdir(exist_ok=True)
            (directory / 'source.bundle').write_bytes(bundle.read_bytes())
            try:
                for failure in (False, True):
                    entered, release = threading.Event(), threading.Event()
                    def blocked_publish(source, commit, target, url):
                        assert url == REMOTE
                        entered.set()
                        assert release.wait(5), 'self-check did not release publisher'
                        if failure:
                            raise SyncFailure('GitPushFailed', heads[0])
                        return _value('confirmed', commit, commit)
                    _publish = blocked_publish
                    _save(root, _value('confirmed', heads[0], heads[0]))
                    task = asyncio.create_task(sync_source(NoCloud(), root, heads[-1], config))
                    try:
                        for _ in range(500):
                            if entered.is_set():
                                break
                            await asyncio.sleep(.01)
                        assert entered.is_set()
                        pending = sync_status(root, config)
                        assert pending['status'] == 'pending' and pending['local_head'] == heads[-1]
                        assert not pending['remote_saved'] and pending['remote_head'] == heads[0]
                        task.cancel()
                        await asyncio.sleep(.01)
                        task.cancel()
                        await asyncio.sleep(.01)
                        wrappers[-1].cancel()
                        await asyncio.sleep(.01)
                        assert not task.done() and _LOCKS[str(root.resolve())].locked()
                    finally:
                        release.set()
                    try:
                        await task
                        raise AssertionError('real cancellation swallowed')
                    except asyncio.CancelledError:
                        pass
                    final = sync_status(root, config)
                    assert final['local_head'] == heads[-1] and not _LOCKS[str(root.resolve())].locked()
                    assert final['remote_saved'] != failure
                    if failure:
                        assert final['status'] == 'failed' and final['error_type'] == 'GitPushFailed'
                        # Same-SHA failure remains retryable, using the existing bundle.
                        failure = False
                        assert (await sync_source(NoCloud(), root, heads[-1], config))['remote_saved']
                forced_calls = []
                def forced_publish(source, commit, target, url):
                    assert url == REMOTE
                    forced_calls.append(commit)
                    return _value('confirmed', commit, commit)
                _publish = forced_publish
                assert (await sync_source(NoCloud(), root, heads[-1], config, force=True))['remote_saved']
                assert forced_calls == [heads[-1]]
                entered_cloud = asyncio.Event()
                async def blocked_cloud(*args):
                    entered_cloud.set()
                    await asyncio.Event().wait()
                _cloud_bundle = blocked_cloud
                task = asyncio.create_task(sync_source(NoCloud(), root, secret_head, config))
                await asyncio.wait_for(entered_cloud.wait(), 5)
                assert sync_status(root, config)['status'] == 'pending'
                task.cancel()
                try:
                    await task
                    raise AssertionError('cloud cancellation swallowed')
                except asyncio.CancelledError:
                    pass
                assert sync_status(root, config)['error_type'] == 'SyncCancelled'
            finally:
                _publish, _cloud_bundle = original_publish, original_cloud
                asyncio.wrap_future = original_wrap
        asyncio.run(cancellation_checks())
        assert sync_status(root, root / 'missing-config.json')['status'] == 'disabled'
        return {'passed': True, 'checks': ['three-commit history retained', 'repeat push adds no commit',
                'non-fast-forward refused', 'deleted historical credential path refused',
                'deleted historical secret refused', 'explicit secret patterns',
                'production remote/branch cannot be overridden', 'confirmed same-HEAD fast path',
                'pending is truthful during sync', 'repeated cancellation drains publisher and persists outcome',
                'failed same-HEAD retry', 'manual force rechecks same HEAD', 'cloud cancellation records failure'],
                'cloud_called': False, 'github_called': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-check', action='store_true', required=True)
    parser.parse_args()
    print(json.dumps(self_check(), ensure_ascii=False, indent=2))
