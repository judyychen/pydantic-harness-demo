"""Dedicated Next experiment transport for the official Pydantic Workspace protocol.

Coder, file transfer, path handling, shell tools, and the model loop stay upstream.
Lifecycle and preview are fixed host operations for a dedicated E2B instance.
"""
import asyncio
import json
import os
import re
import shlex
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from pydantic_ai.workspaces import CommandResult, Workspace, WorkspaceRef, WorkspaceUnavailableError, WorkspaceTimeoutError

def seed_files(root):
    """Keep .gitignore; never seed credentials, dependencies or build caches."""
    for file in sorted(root.rglob('*')):
        parts=file.relative_to(root).parts
        if any(p in ('node_modules','.next','.git','__pycache__') or (p.startswith('.') and p!='.gitignore') for p in parts):
            continue
        if file.is_file() and not file.is_symlink() and file.suffix not in ('.pem','.key'):
            yield file


async def checked(backend, command, *, shell=False, timeout=30):
    result = await backend.run(command, shell=shell, timeout=timeout)
    if result.exit_code:
        raise WorkspaceUnavailableError('Workspace command failed; inspect the restricted experiment log.')
    return result.stdout


async def snapshot_files(backend):
    """Official filesystem text view; not a source backup. Do not follow symlinks."""
    workspace = Workspace(backend)
    root = await workspace.working_dir()
    result, total = {}, 0
    async def visit(folder):
        nonlocal total
        for entry in sorted(await workspace.list_dir(folder), key=lambda x:x.name):
            if entry.name.startswith('.') or entry.name in ('node_modules','dist','__pycache__'): continue
            if await workspace.realpath(entry.path) != entry.path: continue
            if entry.is_dir:
                await visit(entry.path)
            else:
                size=entry.size
                if size is None:  # POSIX fallback listings omit size.
                    size=int((await checked(backend,['wc','-c',entry.path])).split()[0])
                if size>500_000: continue
                raw = await workspace.read_bytes(entry.path)
                try: text = raw.decode('utf-8')
                except UnicodeError: continue
                total += len(raw)
                if len(result) >= 1000 or total > 8_000_000:
                    raise WorkspaceUnavailableError('Text view exceeds this experiment limit; source export is separate.')
                result[entry.path[len(root)+1:]] = text
    await visit(root)
    return result


GIT_SCRIPT = '''set -eu
if [ ! -e .git ]; then git init -q -b main; fi
test -d .git && test ! -L .git
test "$(git rev-parse --show-toplevel)" = "$(pwd -P)"
git -c core.hooksPath=/dev/null -c core.fsmonitor=false add --all -- . \
  ':(exclude).env*' ':(exclude)**/.env*' ':(exclude)**/*.pem' ':(exclude)**/*.key' \
  ':(exclude).pydantic-ai-harness' ':(exclude).dreamable-next-spike.json' \
  ':(exclude)retention-probe' ':(exclude)cloud-git-probe' \
  ':(glob,exclude)**/node_modules/**' ':(glob,exclude)**/.ssh/**' \
  ':(glob,exclude)**/.next/**' ':(exclude)**/tsconfig.tsbuildinfo'
if ! git -c core.hooksPath=/dev/null -c core.fsmonitor=false diff --cached --quiet; then
  git -c core.hooksPath=/dev/null -c core.fsmonitor=false \
    -c user.name=Dreamable-Spike -c user.email=spike@invalid.local \
    -c commit.gpgsign=false commit -q -m "$1"
fi
git rev-parse HEAD
'''


async def checkpoint(backend, state, run_id, *, label=None):
    """Fixed host operation. No model-chosen credentials or repository remote."""
    ident=str(uuid.UUID(run_id))
    message = ('checkpoint(app): '+label[:160]+' [run '+ident+']') if label else 'spike checkpoint '+ident
    if not backend.remote:
        # Offline Git checks use a disposable local workspace.
        process = await asyncio.create_subprocess_exec('sh','-c',GIT_SCRIPT,'checkpoint',message,
            cwd=state/'workspace',stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        out, _ = await process.communicate()
        if process.returncode: raise WorkspaceUnavailableError('Local Git checkpoint failed.')
        commit = out.decode().strip()
    else:
        commit = (await checked(backend.backend,['sh','-c',GIT_SCRIPT,'checkpoint',message])).strip()
    if not re.fullmatch('[0-9a-f]{40,64}',commit): raise WorkspaceUnavailableError('Git did not return a commit ID.')
    return commit


async def stop_shell_jobs(backend, runtime=None):
    """Stop the registered Harness process groups using Node's standard OS API.

    The official shell controller's `kill --` is not supported by Alpine /bin/sh.
    This host stop path also handles background jobs that already returned to the model.
    """
    script=(runtime or getattr(backend,'runtime_dir','/runtime'))+'/stop-jobs.mjs'
    result=await checked(backend,['node',script,await backend.working_dir()],timeout=15)
    return int(result.strip())


class E2BWorkspace:
    """Application-owned lifecycle around the official E2B backend; no custom E2B protocol."""
    remote = True
    runtime_dir = '/opt/dreamable-next-spike-runtime'
    skills_dir = runtime_dir + '/skills'

    def __init__(self, state, runtime, *, seed=None, seed_allowed=True):
        self.state, self.runtime = state, runtime
        self.seed = seed or runtime.parent/'next-app'
        self.preview_ready = False
        self.lock = asyncio.Lock()
        self.backend = self.sandbox = None
        self.ready = self.stopping = False
        self.error = None
        self.reference_path = state/'sandbox.json'
        self.reference = json.loads(self.reference_path.read_text()) if self.reference_path.exists() else {
            'owner':uuid.uuid4().hex,'sandbox_id':None,'phase':'disconnected',
            'purpose':'pydantic-next-spike','seed_allowed':seed_allowed}
        if self.reference.get('purpose') != 'pydantic-next-spike':
            raise WorkspaceUnavailableError('Reference is not owned by the Next experiment')
        self.seed_allowed = bool(self.reference.get('seed_allowed', False))
        self.recovery_required = self.reference.get('needs_recovery',False)
        if self.reference['phase'] not in ('paused','disconnected'):
            self.reference['phase']='disconnected'

    def config(self):
        missing = [key for key in ('E2B_API_URL','E2B_API_KEY','E2B_TEMPLATE_ID') if not os.getenv(key)]
        if missing: return missing, {}
        url = urlsplit(os.environ['E2B_API_URL'])
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('E2B_API_URL must be a clean HTTPS control-plane URL.')
        timeout = int(os.getenv('E2B_SANDBOX_TIMEOUT','3600'))
        if not 60 <= timeout <= 86400: raise ValueError('E2B_SANDBOX_TIMEOUT must be 60..86400 seconds.')
        return [], {'api_url':os.environ['E2B_API_URL'].rstrip('/'),'api_key':os.environ['E2B_API_KEY'],
                    'timeout':timeout,'request_timeout':30}

    def status(self):
        try: missing, _ = self.config(); invalid = None
        except (ValueError,TypeError): missing=[]; invalid='配置格式错误，请检查接口地址和有效期。'
        return {'provider':'e2b','phase':self.reference['phase'],'configured':not missing and not invalid,
                'missing':missing,'error':invalid or self.error,'ready':self.ready,'preview_ready':self.preview_ready,'seed_allowed':self.seed_allowed,
                'error_stage':self.reference.get('last_error_stage'),'error_type':self.reference.get('last_error_type'),
                'purpose':self.reference['purpose'],'sandbox_id':self.reference['sandbox_id']}

    @property
    def preview_url(self):
        return 'https://' + self.sandbox.get_host(3000) if self.ready and self.preview_ready else None

    @property
    def ref(self):
        return self.backend.ref if self.backend else None

    def save_reference(self, **values):
        self.reference.update(values)
        temp = self.reference_path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.reference,indent=2))
        temp.replace(self.reference_path)

    async def ensure(self):
        if self.stopping or not self.ready:
            raise WorkspaceUnavailableError('Cloud workspace is not connected; use Connect / Resume in the workbench.')

    async def working_dir(self):
        await self.ensure()
        return await self.backend.working_dir()

    async def run(self, command, *, shell=False, env=None, timeout=None):
        await self.ensure()
        return await self.backend.run(command,shell=shell,env=env,timeout=timeout)

    # Python 3.12 checks Protocol members statically; __getattr__ would select shell fallback.
    async def read_bytes(self, path):
        await self.ensure()
        return await self.backend.read_bytes(path)

    async def write_bytes(self, path, data):
        await self.ensure()
        return await self.backend.write_bytes(path,data)

    async def stat(self, path):
        await self.ensure()
        return await self.backend.stat(path)

    async def list_dir(self, path):
        await self.ensure()
        return await self.backend.list_dir(path)

    async def make_dir(self, path):
        await self.ensure()
        return await self.backend.make_dir(path)

    async def remove(self, path):
        await self.ensure()
        return await self.backend.remove(path)

    async def exists(self, path):
        await self.ensure()
        return await self.backend.exists(path)

    async def refresh_state(self):
        """Read the control plane; a paused envd is not a missing sandbox."""
        if not self.reference['sandbox_id']:
            return None
        missing, opts = self.config()
        if missing: raise WorkspaceUnavailableError('Missing configuration: '+', '.join(missing))
        if self.reference.get('api_url') and self.reference['api_url'] != opts['api_url']:
            raise WorkspaceUnavailableError('Saved sandbox belongs to a different endpoint; use separate experiment state.')
        from e2b import AsyncSandbox
        try:
            info = await AsyncSandbox.get_info(self.reference['sandbox_id'],
                **{key:opts[key] for key in ('api_url','api_key','request_timeout')})
            metadata = getattr(info,'metadata',None) or {}
            if metadata.get('owner') != self.reference['owner'] or metadata.get('purpose') != 'pydantic-next-spike':
                raise WorkspaceUnavailableError('Sandbox ownership metadata does not match this experiment')
            state = getattr(info.state,'value',info.state)
            if state not in ('running','paused'):
                raise WorkspaceUnavailableError('Sandbox is not running or paused')
        except Exception as exc:
            self.ready = self.preview_ready = False
            self.error = '查询沙箱状态失败：'+type(exc).__name__+'。实例引用已保留。'
            self.save_reference(phase='error',last_error_stage='checking-state',last_error_type=type(exc).__name__)
            raise WorkspaceUnavailableError(self.error) from None
        self.error = None
        if state == 'paused':
            self.ready = self.preview_ready = False
            self.save_reference(phase='paused',last_error_stage=None,last_error_type=None)
        return state

    async def resume(self, *, start_preview=True, allow_resume=True):
        async with self.lock:
            missing, opts = self.config()
            if missing: raise WorkspaceUnavailableError('Missing configuration: '+', '.join(missing))
            state = await self.refresh_state()
            if not allow_resume and state in (None,'paused'):
                return
            if self.ready:
                if state == 'running':
                    # Explicit user/Agent activity renews; status polling never calls resume.
                    await self.sandbox.set_timeout(opts['timeout'])
                    self.stopping=False
                    if start_preview and not self.preview_ready:
                        await self.start_preview()
                        self.preview_ready=True
                        self.save_reference(phase='ready')
                    self.save_reference(last_error_stage=None,last_error_type=None)
                    return
                self.ready=False
            import e2b
            from pydantic_ai_harness.e2b_sandbox import E2BSandboxBackend
            if self.reference.get('api_url') and self.reference['api_url'] != opts['api_url']:
                raise WorkspaceUnavailableError('Saved sandbox belongs to a different endpoint; use separate experiment state.')
            self.error=None
            stage='connecting'
            self.save_reference(phase=stage,last_error_stage=None,last_error_type=None)
            try:
                if self.reference['sandbox_id']:
                    sandbox = await e2b.AsyncSandbox.connect(self.reference['sandbox_id'],**opts)
                else:
                    sandbox = await e2b.AsyncSandbox.create(template=os.environ['E2B_TEMPLATE_ID'],
                        metadata={'owner':self.reference['owner'],'purpose':'pydantic-next-spike'},
                        lifecycle={'on_timeout':{'action':'pause','keep_memory':False}},**opts)
                self.sandbox=sandbox
                # Persist the ID immediately: a failed probe must not create a second instance.
                stage='provisioning'
                self.save_reference(sandbox_id=sandbox.sandbox_id,api_url=opts['api_url'],phase=stage)
                info=await sandbox.get_info()
                metadata=getattr(info,'metadata',None) or {}
                if metadata.get('owner') != self.reference['owner'] or metadata.get('purpose') != 'pydantic-next-spike':
                    raise WorkspaceUnavailableError('Sandbox ownership metadata does not match this experiment')
                self.backend=E2BSandboxBackend(sandbox=sandbox,working_dir='/workspace')
                await self.provision()
                if self.recovery_required:
                    await stop_shell_jobs(self.backend,self.runtime_dir)
                    self.recovery_required=False
                    self.save_reference(needs_recovery=False)
                if start_preview:
                    stage='starting-preview'
                    self.save_reference(phase=stage)
                    await self.start_preview()
                    self.preview_ready=True
                self.ready=True
                self.stopping=False
                self.save_reference(phase='ready' if self.preview_ready else 'connected-empty')
            except Exception as exc:
                self.ready=self.preview_ready=False
                label={'connecting':'连接沙箱','provisioning':'准备工作区','starting-preview':'启动预览'}[stage]
                self.error=label+'失败：'+type(exc).__name__+'。实例引用已保留；重试不会主动另建实例。'
                self.save_reference(phase='error',last_error_stage=stage,last_error_type=type(exc).__name__)
                raise WorkspaceUnavailableError(self.error) from None

    async def provision(self):
        # Dedicated test instance only; never adopt a real user project.
        # veFaaS injects envd into arbitrary images; this image starts with root only.
        account = await self.sandbox.commands.run(
            'set -eu; id user >/dev/null 2>&1 || useradd --create-home --shell /bin/bash user',
            user='root',timeout=15)
        if account.exit_code: raise WorkspaceUnavailableError('Image must allow an unprivileged Workspace user.')
        probe = await self.sandbox.commands.run(
            'set -eu; id user >/dev/null; for x in node npm git tar bash sh setsid realpath rg sha256sum; do command -v "$x" >/dev/null; done; realpath -- /tmp >/dev/null; node -e "if(Number(process.versions.node.split(\'.\')[0])<22)process.exit(1)"',
            user='user',timeout=20)
        if probe.exit_code: raise WorkspaceUnavailableError('Image needs user, Node >=22, git, tar, bash/sh, setsid, GNU realpath and rg.')
        owned = await self.sandbox.commands.run(
            'set -eu; test ! -L /workspace; test ! -L /data; test ! -L /opt/dreamable-next-spike-runtime; mkdir -p /workspace /data',user='root',timeout=15)
        if owned.exit_code: raise WorkspaceUnavailableError('Image must allow host SDK provisioning as root.')
        facade=Workspace(self.backend)
        marker='/workspace/.dreamable-next-spike.json'
        if await facade.exists(marker):
            ownership=json.loads(await facade.read_text(marker))
            if ownership.get('owner') != self.reference['owner']:
                raise WorkspaceUnavailableError('Workspace belongs to another experiment; refusing to overwrite it.')
        else:
            if await facade.list_dir('/workspace') or await facade.list_dir('/data'):
                raise WorkspaceUnavailableError('Use a dedicated empty workspace; existing files will not be overwritten.')
            result=await self.sandbox.commands.run('chown user:$(id -gn user) /workspace /data',user='root',timeout=10)
            if result.exit_code: raise WorkspaceUnavailableError('Cannot give the test user access to the workspace.')
            ownership={'owner':self.reference['owner'],'seeded':False}
            await facade.write_text(marker,json.dumps(ownership))
        if not ownership['seeded'] and self.seed_allowed:
            if not (self.seed/'package.json').is_file() or not (self.seed/'package-lock.json').is_file():
                raise WorkspaceUnavailableError('Next app seed and lockfile are not prepared')
            for file in seed_files(self.seed):
                await facade.write_bytes('/workspace/'+file.relative_to(self.seed).as_posix(),file.read_bytes())
            ownership['seeded']=True
            await facade.write_text(marker,json.dumps(ownership))
        result=await self.sandbox.commands.run('mkdir -p /opt/dreamable-next-spike-runtime',user='root',timeout=10)
        if result.exit_code: raise WorkspaceUnavailableError('Cannot provision preview runtime.')
        for file in sorted(self.runtime.rglob('*')):
            if file.is_file() and not file.is_symlink() and file.name!='Dockerfile':
                await self.sandbox.files.write(self.runtime_dir+'/'+file.relative_to(self.runtime).as_posix(),file.read_bytes(),user='root')
        result=await self.sandbox.commands.run('chown -R root:root /opt/dreamable-next-spike-runtime; chmod -R a-w,a+rX /opt/dreamable-next-spike-runtime',user='root',timeout=10)
        if result.exit_code: raise WorkspaceUnavailableError('Cannot protect fixed runtime.')

    async def start_preview(self):
        # Fixed host-owned Next dev process, never registered as a Coder job.
        facade=Workspace(self.backend)
        if not await facade.exists('/workspace/package.json'):
            raise WorkspaceUnavailableError('Recovery workspace is empty; restore remote source before preview')
        health=['node','-e',"fetch('http://127.0.0.1:3000/api/health',{signal:AbortSignal.timeout(2000)}).then(r=>r.json()).then(v=>{if(v.purpose!=='pydantic-next-spike'||v.ready!==true)process.exit(1)}).catch(()=>process.exit(1))"]
        async def healthy():
            try:
                return (await self.backend.run(health,timeout=8)).exit_code==0
            except WorkspaceTimeoutError:
                # A cold Next compile can outlast one probe; readiness keeps waiting.
                return False
        if await healthy():
            return
        # The image owns dependency installation; the Agent never runs npm install.
        install="set -eu; cd /workspace; digest=$(sha256sum package-lock.json | cut -d ' ' -f1); if [ ! -f node_modules/next/package.json ] || [ \"$(cat /tmp/dreamable-next-installed-lock 2>/dev/null || true)\" != \"$digest\" ]; then npm ci --include=dev --no-audit --no-fund; printf '%s' \"$digest\" > /tmp/dreamable-next-installed-lock; fi"
        await checked(self.backend,install,shell=True,timeout=180)
        allowed=self.sandbox.get_host(3000)+',127.0.0.1,localhost'
        command='cd /workspace; setsid nohup env HOSTNAME=0.0.0.0 NEXT_DEV_HOST=0.0.0.0 PORT=3000 NEXT_ALLOWED_DEV_ORIGINS='+shlex.quote(allowed)+' npm run dev >/tmp/dreamable-next-preview.log 2>&1 </dev/null &'
        await checked(self.backend,command,shell=True,timeout=15)
        deadline=asyncio.get_running_loop().time()+90
        while asyncio.get_running_loop().time()<deadline:
            if await healthy():
                return
            await asyncio.sleep(.5)
        raise WorkspaceUnavailableError('Next preview did not become ready on port 3000; inspect its sandbox log')

    async def stop(self):
        self.stopping=True
        if self.backend and self.ready:
            try:
                await stop_shell_jobs(self.backend,self.runtime_dir)
            except Exception:
                # Stop the environment as fallback rather than claim unverified process cancellation.
                try:
                    await self.sandbox.pause(keep_memory=False)
                except Exception:
                    self.ready=False
                    self.recovery_required=True
                    self.error='停止和暂停尚未确认；必须先核对火山实例，不能直接继续任务。'
                    self.save_reference(phase='error',needs_recovery=True)
                    raise WorkspaceUnavailableError(self.error) from None
                self.ready=False
                self.save_reference(phase='paused',checkpoint_pending=True)
                raise WorkspaceUnavailableError('Sandbox paused as stop fallback; resume to save a Git checkpoint.') from None

    async def pause(self):
        async with self.lock:
            if await self.refresh_state() in (None,'paused'):
                return
            if not self.ready: raise WorkspaceUnavailableError('Connect the running test sandbox first.')
            await self.stop()
            try:
                await self.sandbox.pause(keep_memory=False)
                if await self.refresh_state() != 'paused':
                    raise WorkspaceUnavailableError('Cloud pause was not confirmed')
            except Exception:
                self.ready=self.preview_ready=False
                self.recovery_required=True
                self.error='暂停未确认；先核对火山实例，再重新连接。'
                self.save_reference(phase='error',needs_recovery=True)
                raise WorkspaceUnavailableError(self.error) from None


async def export_source(backend, state):
    """Source + .git export, not a VM or database snapshot. No Git remote configured."""
    import hashlib
    ident=str(uuid.uuid4())
    commit=await checkpoint(backend,state,ident)
    excludes=['--exclude=./.env*','--exclude=*/.env*','--exclude=*.pem','--exclude=*.key',
        '--exclude=./.pydantic-ai-harness','--exclude=./.dreamable-next-spike.json',
        '--exclude=node_modules','--exclude=.next','--exclude=tsconfig.tsbuildinfo','--exclude=./.ssh',
        '--exclude=./retention-probe','--exclude=./cloud-git-probe']
    target=state/'exports'/(ident+'.tar.gz')
    target.parent.mkdir(exist_ok=True)
    if backend.remote:
        remote='/tmp/dreamable-spike-source-'+ident+'.tar.gz'
        facade=Workspace(backend.backend)
        try:
            await checked(backend.backend,['tar',*excludes,'-czf',remote,'-C','/workspace','.'])
            size=(await facade.stat(remote)).size
            if size is None or size>50_000_000: raise WorkspaceUnavailableError('Source export exceeds 50 MB experiment limit.')
            raw=await facade.read_bytes(remote)
        finally:
            await facade.remove(remote)
    else:
        process=await asyncio.create_subprocess_exec('tar',*excludes,'-czf',str(target),'-C',str(state/'workspace'),'.',
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        await process.communicate()
        if process.returncode: raise WorkspaceUnavailableError('Source archive failed.')
        raw=target.read_bytes()
    temp=target.with_suffix('.tmp')
    temp.write_bytes(raw)
    temp.replace(target)
    value={'id':ident,'code_version':commit,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),
           'contains':['source','.git'],'excludes':['credentials','dependencies','agent logs','business database']}
    target.with_suffix('.json').write_text(json.dumps(value,indent=2))
    return value
