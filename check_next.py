"""Offline Next wiring check: no cloud/model calls, only temporary files/Git."""
import asyncio
import json
import os
import subprocess
import tarfile
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import workspace as wiring
from pydantic_ai.workspaces import CommandResult

ROOT = Path(__file__).resolve().parent

async def check():
    checks=[]
    with tempfile.TemporaryDirectory(prefix='dreamable-next-offline-') as directory:
        root=Path(directory)
        seed=root/'seed';seed.mkdir()
        for name, text in {'package.json':'{}','package-lock.json':'{}','.gitignore':'.next/\nnode_modules/\n','app/page.tsx':'seed page'}.items():
            p=seed/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
        for name in ('.env.local','.next/cache','node_modules/next/package.json'):
            p=seed/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('excluded test data')
        assert {p.relative_to(seed).as_posix() for p in wiring.seed_files(seed)}=={'package.json','package-lock.json','.gitignore','app/page.tsx'}
        checks.append('seed retains .gitignore and excludes credentials/dependencies/.next')

        # Two independent fake SDK instances; actual filesystem data lives only in temp.
        class Facade:
            def __init__(self,backend): self.backend=backend
            def path(self,name): return self.backend.disk/name.lstrip('/')
            async def exists(self,name): return self.path(name).exists()
            async def read_text(self,name): return self.path(name).read_text()
            async def write_text(self,name,text): await self.write_bytes(name,text.encode())
            async def write_bytes(self,name,raw):
                p=self.path(name);p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
            async def list_dir(self,name):
                p=self.path(name);return list(p.iterdir()) if p.exists() else []
        class Backend:
            def __init__(self,disk): self.disk=disk;self.commands=[];self.preview=False;self.ref=SimpleNamespace(id=disk.name)
            async def working_dir(self): return '/workspace'
            async def run(self,command,*,shell=False,env=None,timeout=None):
                self.commands.append((command,shell))
                if isinstance(command,list) and command[:2]==['node','-e']:
                    return CommandResult(exit_code=0 if self.preview else 1,stdout='',stderr='')
                if isinstance(command,str) and 'npm run dev' in command:
                    assert shell and 'PORT=3000' in command and 'NEXT_ALLOWED_DEV_ORIGINS=' in command
                    self.preview=True
                return CommandResult(exit_code=0,stdout='0\n',stderr='')
        sandboxes={}
        async def create(**kwargs):
            name='next-'+str(len(sandboxes)+1)
            backend=Backend(root/name)
            sandbox=SimpleNamespace(sandbox_id=name,backend=backend,
                commands=SimpleNamespace(run=AsyncMock(return_value=CommandResult(exit_code=0,stdout='',stderr=''))),
                files=SimpleNamespace(write=AsyncMock()),state='running',
                get_host=lambda port:f'{port}-next.test.invalid',is_running=AsyncMock(return_value=True),
                set_timeout=AsyncMock(),pause=AsyncMock(return_value=True))
            sandboxes[name]=sandbox
            sandbox.get_info=AsyncMock(side_effect=lambda:SimpleNamespace(metadata=kwargs['metadata'],state=sandbox.state))
            async def pause(**kwargs): sandbox.state='paused';return True
            sandbox.pause=AsyncMock(side_effect=pause)
            assert kwargs['lifecycle']['on_timeout']=={'action':'pause','keep_memory':False}
            return sandbox
        async def connect(ident,**kwargs): sandboxes[ident].state='running';return sandboxes[ident]
        async def get_info(ident,**kwargs): return await sandboxes[ident].get_info()
        env={'E2B_API_URL':'https://control.test.invalid','E2B_API_KEY':'offline-placeholder','E2B_TEMPLATE_ID':'test-template','E2B_SANDBOX_TIMEOUT':'1800'}
        state=root/'state-a';state.mkdir()
        with patch.dict(os.environ,env,clear=True),patch('e2b.AsyncSandbox.create',new=AsyncMock(side_effect=create)) as created,patch('e2b.AsyncSandbox.connect',new=AsyncMock(side_effect=connect)) as connected,patch('e2b.AsyncSandbox.get_info',new=AsyncMock(side_effect=get_info)),patch('pydantic_ai_harness.e2b_sandbox.E2BSandboxBackend',side_effect=lambda sandbox,working_dir:sandbox.backend),patch.object(wiring,'Workspace',Facade):
            a=wiring.E2BWorkspace(state,ROOT/'runtime',seed=seed)
            await a.resume()
            assert a.preview_url=='https://3000-next.test.invalid' and a.reference['purpose']=='pydantic-next-spike'
            assert Facade(a.backend).path('/workspace/.gitignore').exists()
            # A cold health command may time out once while Next still starts normally.
            original_run=a.backend.run
            health_calls=0
            async def cold_run(command,**kwargs):
                nonlocal health_calls
                if isinstance(command,list) and command[:2]==['node','-e']:
                    health_calls+=1
                    if health_calls==2: raise wiring.WorkspaceTimeoutError('cold probe')
                return await original_run(command,**kwargs)
            a.backend.preview=False
            a.preview_ready=False
            with patch.object(a.backend,'run',side_effect=cold_run):
                await a.resume()  # Covers the already-connected, preview-not-ready path.
            assert health_calls==3 and a.preview_ready
            # A permanently unhealthy service must hit the overall readiness deadline.
            async def unhealthy_run(command,**kwargs):
                if isinstance(command,list) and command[:2]==['node','-e']:
                    raise wiring.WorkspaceTimeoutError('still unavailable')
                return await original_run(command,**kwargs)
            clock=iter([0,1,90])
            fake_asyncio=SimpleNamespace(get_running_loop=lambda:SimpleNamespace(time=lambda:next(clock)),sleep=AsyncMock())
            with patch.object(a.backend,'run',side_effect=unhealthy_run),patch.object(wiring,'asyncio',fake_asyncio):
                try: await a.start_preview()
                except wiring.WorkspaceUnavailableError: pass
                else: raise AssertionError('permanently-unhealthy-preview-accepted')
            checks.append('cold preview timeout retries; connected fast path recovers; permanent failure stays bounded')
            page=Facade(a.backend).path('/workspace/app/page.tsx');page.write_text('real remote edit')
            # Reproduce auto-pause with stale ready=True. Envd health must not block connect.
            cloud=a.sandbox
            cloud.state='paused';cloud.backend.preview=False
            cloud.is_running.side_effect=RuntimeError('paused envd must not be probed')
            await a.resume()
            cloud.is_running.assert_not_awaited()
            assert a.ready and a.preview_ready and cloud.backend.preview
            assert created.await_count==1 and page.read_text()=='real remote edit'
            # A pause preparation must not resume an instance which has already paused.
            cloud.state='paused'
            count=connected.await_count
            await a.resume(start_preview=False,allow_resume=False)
            assert not a.ready and a.reference['phase']=='paused' and connected.await_count==count
            await a.pause()
            assert connected.await_count==count
            await a.resume()
            from e2b import SandboxNotFoundException
            with patch('e2b.AsyncSandbox.get_info',new=AsyncMock(side_effect=SandboxNotFoundException('offline missing'))):
                try: await a.resume()
                except wiring.WorkspaceUnavailableError: pass
                else: raise AssertionError('missing sandbox accepted')
            assert not a.ready and not a.preview_ready and a.reference['last_error_stage']=='checking-state'
            assert a.reference['sandbox_id']==cloud.sandbox_id and created.await_count==1
            await a.resume()
            count=connected.await_count
            with patch('e2b.AsyncSandbox.get_info',new=AsyncMock(return_value=SimpleNamespace(state='paused',metadata={'owner':'another-owner','purpose':'pydantic-next-spike'}))):
                try: await a.resume()
                except wiring.WorkspaceUnavailableError: pass
                else: raise AssertionError('foreign sandbox accepted')
            assert connected.await_count==count and created.await_count==1
            await a.resume()
            # An accepted pause RPC alone must not claim the cloud is paused.
            with patch.object(cloud,'pause',new=AsyncMock(return_value=True)):
                try: await a.pause()
                except wiring.WorkspaceUnavailableError: pass
                else: raise AssertionError('unconfirmed pause accepted')
            assert a.reference['phase']=='error' and not a.ready and not a.preview_ready
            await a.resume()
            checks.append('auto-paused stale handle resumes via control plane; repeated pause never resumes; missing instance retains ID')
            checks.append('foreign ownership refuses connect; pause RPC without paused cloud state stays unconfirmed')
            await a.stop()
            assert a.backend.preview and a.ready, 'Stop must preserve the fixed dev server'
            await a.pause()
            assert not a.ready and a.preview_url is None
            with patch.object(a,'start_preview',side_effect=wiring.WorkspaceUnavailableError('offline service failure')):
                try: await a.resume()
                except wiring.WorkspaceUnavailableError: pass
                else: raise AssertionError('preview-failure-accepted')
            assert a.reference['last_error_stage']=='starting-preview'
            assert a.reference['last_error_type']=='WorkspaceUnavailableError'
            await a.resume()
            assert a.reference['last_error_stage'] is None and a.reference['last_error_type'] is None
            assert page.read_text()=='real remote edit' and created.await_count==1
            bstate=root/'state-b';bstate.mkdir()
            b=wiring.E2BWorkspace(bstate,ROOT/'runtime',seed=root/'MUST_NOT_READ',seed_allowed=False)
            await b.resume(start_preview=False)
            assert b.ready and b.preview_url is None and b.reference['seed_allowed'] is False
            assert not Facade(b.backend).path('/workspace/package.json').exists()
            assert b.reference['sandbox_id']!=a.reference['sandbox_id']
            # Seed permission stays disabled when the recovery Worker restarts.
            restarted=wiring.E2BWorkspace(bstate,ROOT/'runtime',seed=seed,seed_allowed=True)
            assert not restarted.seed_allowed
            checks.append('independent A/B refs; first A seed; no-seed B; host-owned Next lifecycle; stop/pause/resume')

        # Real Git/export behavior is tested in disposable local files.
        local=root/'git-check';(local/'workspace').mkdir(parents=True)
        for name,text in {'kept.txt':'source','.gitignore':'# intentionally no exclusions\n','.next/cache':'cache','node_modules/pkg/file':'dependency',
                          'retention-probe/marker.txt':'isolated retention probe','cloud-git-probe/marker.txt':'isolated Git probe'}.items():
            p=local/'workspace'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
        backend=SimpleNamespace(remote=False)
        commit=await wiring.checkpoint(backend,local,str(uuid.uuid4()))
        tracked=subprocess.check_output(['git','-C',str(local/'workspace'),'ls-files'],text=True).splitlines()
        assert tracked==['.gitignore','kept.txt'] and len(commit)==40
        manifest=await wiring.export_source(backend,local)
        with tarfile.open(local/'exports'/(manifest['id']+'.tar.gz')) as package:
            names=package.getnames()
            assert './.git/HEAD' in names and './.gitignore' in names
            assert not any(part in n.split('/') for n in names for part in ('.next','node_modules','retention-probe','cloud-git-probe'))
        checks.append('real Git/export retains source+.git+.gitignore; excludes dependencies/cache/retention and cloud Git probes')


        # Reproduce the real provider's Decimal cost at the JSON trust boundary.
        # Import with temporary STATE so the live state-next is never opened.
        from decimal import Decimal
        from pydantic_ai import Agent
        from pydantic_ai.models.function import FunctionModel
        from pydantic_ai.messages import ModelResponse, TextPart
        usage_state=root/'usage-state'
        with patch.dict(os.environ,{'SPIKE_STATE_DIR':str(usage_state),'SPIKE_SEED':'0','SPIKE_MAINTENANCE':'0','PYDANTIC_AI_NO_BANNER':'1'}):
            import server
            assert server.STATE==usage_state.resolve()
            def completed(messages,info): return ModelResponse(parts=[TextPart('offline completed result')])
            result=await Agent(FunctionModel(completed)).run('check usage serialization')
            expected=Decimal('0.1234567890123456789')
            result.usage.cost=expected
            run={'id':str(uuid.uuid4()),'started':0,'done':True,'status':'running'}
            await server.persist(result,run,'completed')
            server.record(run)
            server.active=run
            response=await server.status(None)
            restored=json.loads((usage_state/'runs'/(run['id']+'.json')).read_text())
            status=json.loads(response.body)
            assert run['status']=='completed' and response.status_code==200
            for value in (run,restored,status['runs'][0],status['active']):
                assert Decimal(str(value['usage']['cost']))==expected
            assert (await server.saved()).messages
            checks.append('FunctionModel Decimal cost survives real persist/record/status JSON without precision loss')

            # Actual ASGI routes, with no socket, lifespan startup or cloud backend.
            import httpx
            from ag_ui.core import RunAgentInput, UserMessage
            fake_workspace=SimpleNamespace(remote=False,ready=True,preview_url=None,pause=AsyncMock(),
                refresh_state=AsyncMock(return_value='running'),resume=AsyncMock(),status=lambda:{'ready':True})
            with patch.object(server,'workspace',fake_workspace):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://127.0.0.1:7948') as client:
                    server.active={'id':str(uuid.uuid4()),'status':'running','started':0,'done':False}
                    response=await client.post('/spike/maintenance',json={'enabled':True})
                    assert response.status_code==409 and not server.maintenance_mode
                    server.active=run
                    response=await client.post('/spike/maintenance',json={'enabled':True})
                    assert response.status_code==200 and server.maintenance_mode
                    proposed=RunAgentInput(thread_id=server.SESSION,run_id=str(uuid.uuid4()),state={},
                        messages=[UserMessage(id=str(uuid.uuid4()),content='offline maintenance rejection')],
                        tools=[],context=[],forwarded_props={})
                    receipts=set((usage_state/'runs').glob('*.json'))
                    with patch.object(server,'drive',new=AsyncMock()) as drive:
                        response=await client.post('/api/ag-ui',json=proposed.model_dump(mode='json',by_alias=True))
                        assert response.status_code==409 and server.active is run
                        assert set((usage_state/'runs').glob('*.json'))==receipts
                        drive.assert_not_called()
                    status=(await client.get('/spike/status')).json()
                    assert status['workspace']['busy'] and status['workspace']['maintenance']
                    response=await client.post('/spike/maintenance',json={'enabled':False})
                    assert response.status_code==200 and not server.maintenance_mode
                    status=(await client.get('/spike/status')).json()
                    assert not status['workspace']['busy'] and not status['workspace']['maintenance']
                    fake_workspace.remote=True
                    with patch.object(server,'files',new=AsyncMock(return_value={})),\
                         patch.object(server,'checkpoint',new=AsyncMock(return_value='b'*40)),\
                         patch.object(server,'sync_source',new=AsyncMock(return_value={'status':'failed','remote_saved':False,'error_type':'GitPushFailed'})):
                        response=await client.post('/spike/workspace/pause',json={})
                        assert response.status_code==503 and response.json()['git_sync']['status']=='failed'
                        assert '没有暂停' in response.json()['error']
                        fake_workspace.pause.assert_not_awaited()
                        assert not server.control_busy
                    # A real pause route on an already-paused cloud must do no file/Git/resume IO.
                    fake_workspace.refresh_state.return_value='paused'
                    with patch.object(server,'files',new=AsyncMock()) as read_files,\
                         patch.object(server,'checkpoint',new=AsyncMock()) as save_git,\
                         patch.object(server,'sync_source',new=AsyncMock()) as push_git:
                        response=await client.post('/spike/workspace/pause',json={})
                        assert response.status_code==200 and response.json()['already_paused']
                        read_files.assert_not_awaited();save_git.assert_not_awaited();push_git.assert_not_awaited()
                        fake_workspace.pause.assert_not_awaited()
                        assert fake_workspace.resume.await_count==1  # Only the earlier running-then-failed-save path.
                        assert not server.control_busy
            checks.append('ASGI maintenance rejects running tasks/new Runs; completed task permits entry; busy clears on exit')
            checks.append('ASGI pause refuses unconfirmed Git save with 503 and never pauses sandbox')
            checks.append('ASGI already-paused confirmation skips resume, files, Git and cloud pause')

            # Dispatch through the real official Hooks filter, preserving the exact
            # successful tool result even when the subsequent fixed Git stage fails.
            from pydantic_ai.messages import ToolCallPart
            from pydantic_ai.tools import ToolDefinition
            hook_run={'id':str(uuid.uuid4()),'status':'running','started':0,'done':False}
            server.active=hook_run
            ctx=SimpleNamespace(run_id=hook_run['id'])
            successful_result=object()
            written=usage_state/'written.txt';written.write_text('successful file tool effect')
            with patch.object(server,'save_code',new=AsyncMock(side_effect=RuntimeError('offline save failure'))) as save:
                for name in ('write_file','edit_file','shell'):
                    returned=await server.interaction.after_tool_execute(ctx,
                        call=ToolCallPart(tool_name=name,args={'path':str(written)},tool_call_id=str(uuid.uuid4())),
                        tool_def=ToolDefinition(name=name),args={'path':str(written)},result=successful_result)
                    assert returned is successful_result and written.read_text()=='successful file tool effect'
                    save.assert_awaited_with(hook_run,name+' written.txt')
                assert save.await_count==3 and hook_run['stage_save_error']=='RuntimeError'
                returned=await server.interaction.after_tool_execute(ctx,
                    call=ToolCallPart(tool_name='read_file',args={},tool_call_id=str(uuid.uuid4())),
                    tool_def=ToolDefinition(name='read_file'),args={},result=successful_result)
                assert returned is successful_result and save.await_count==3
            assert json.loads((usage_state/'runs'/(hook_run['id']+'.json')).read_text())['stage_save_error']=='RuntimeError'
            checks.append('official Hooks saves write/edit/shell only; failed Git stage preserves successful result and file effect')

            # The real host sync entry point must stay inert when this temporary
            # STATE has no explicit fixed-target configuration.
            assert not server.GIT_CONFIG.exists()
            with patch.object(server,'checkpoint',new=AsyncMock(return_value='a'*40)):
                assert await server.save_code(hook_run,'offline disabled sync')=='a'*40
            assert hook_run['git_sync']['status']=='disabled' and not hook_run['git_sync']['remote_saved']
            assert not (usage_state/'git-sync').exists()
            checks.append('missing temporary Git config leaves real sync disabled without cloud or repository access')

    html=(ROOT/'workbench.html').read_text()
    assert '7948' in (ROOT/'server.py').read_text() and 'dreamable-pydantic-next-m1' in html
    assert "if(run.status==='completed'&&latest.preview)showPreview" not in html
    bundle=(ROOT/'chat-ui/dist/chat.js').read_text()
    assert 'dreamable-pydantic-next-m1' in bundle and 'dreamable-pydantic-m1' not in bundle
    checks.append('isolated session/origin; copied AG-UI bundle; no completion iframe reload')
    print(json.dumps({'passed':checks,'model_called':False,'cloud_called':False},ensure_ascii=False,indent=2))

if __name__=='__main__': asyncio.run(check())
