"""Two-process restart/SIGKILL regression. FunctionModel + temporary SQLite/Git only.

The simulated external effect is a file outside the project workspace. This proves
no automatic replay in this demo, not production exactly-once or cloud recovery.
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parent
CALL='restart-choice'
EFFECT_CALL='restart-side-effect'


def load(path): return json.loads(path.read_text())
def save(path,value): path.write_text(json.dumps(value,ensure_ascii=False,indent=2))


async def worker(mode,state):
    # Set before importing server: the running state-next is never opened.
    os.environ['SPIKE_STATE_DIR']=str(state)
    os.environ['SPIKE_SEED']='0'
    os.environ['PYDANTIC_AI_NO_BANNER']='1'
    import httpx
    import server
    from pydantic_ai import Agent, DeferredToolRequests
    from pydantic_ai.messages import ToolCallPart, ToolReturnPart
    from pydantic_ai.models.function import FunctionModel, DeltaToolCall
    from pydantic_ai.workspaces import WorkspaceRef
    from pydantic_ai_harness import AskUser
    from pydantic_ai_harness.step_persistence import StepPersistence
    from starlette.requests import Request

    info=load(state/'scenario.json')
    effect_path=state/'simulated-external-effect.json'
    calls_path=state/'function-model-calls.json'
    def model_called(): save(calls_path,load(calls_path)+1 if calls_path.exists() else 1)
    def effect_count(): return load(effect_path)['count'] if effect_path.exists() else 0

    class TemporaryWorkspace:
        remote=False
        ready=True
        stopping=False
        preview_url=None
        recovery_required=False
        ref=WorkspaceRef(provider='offline-worker-restart',id=state.name)
        async def working_dir(self): return '/workspace'
        async def resume(self): self.stopping=False
        async def stop(self): self.stopping=True
        async def run(self,*args,**kwargs): raise AssertionError('No workspace command transport is expected')
        def status(self): return {'provider':'offline','ready':True,'phase':'ready','configured':True,'missing':[]}
    server.workspace=TemporaryWorkspace()
    project=state/'workspace';project.mkdir(exist_ok=True)
    if not (project/'source.txt').exists(): (project/'source.txt').write_text('temporary source\n')

    entered=asyncio.Event()
    options={'questions':[{'header':'风格','question':'选择一个方向','options':[
        {'label':'清雅茶室','description':'暖白与茶绿。'},
        {'label':'现代管理','description':'浅灰与蓝色。'}],'multi_select':False}]}
    async def reply(messages,agent_info):
        model_called()
        if mode.startswith('wait'):
            answers=[p for m in messages for p in m.parts if isinstance(p,ToolReturnPart) and p.tool_name=='ask_user_question']
            if answers:
                assert answers[-1].tool_call_id==CALL
                assert answers[-1].content=={'风格':['清雅茶室']}
                yield '已接回原问题：清雅茶室'
            else:
                yield {0:DeltaToolCall(name='ask_user_question',json_args=json.dumps(options,ensure_ascii=False),tool_call_id=CALL)}
        elif mode=='crash-start':
            yield {0:DeltaToolCall(name='apply_external_effect',json_args='{}',tool_call_id=EFFECT_CALL)}
        else:
            # A deterministic replacement queries the actual effect before continuing.
            assert effect_count()==1
            yield '已核对外部结果为一次；不重复写入。'
    server.agent=Agent(FunctionModel(stream_function=reply),capabilities=[server.interaction,AskUser(answerer=None),
        StepPersistence(store=server.steps,capture_frontier=True)],output_type=[str,DeferredToolRequests])
    if mode=='crash-start':
        @server.agent.tool_plain
        async def apply_external_effect() -> str:
            save(effect_path,{'count':effect_count()+1,'operation_id':info['operation_id']})
            entered.set()
            await asyncio.Event().wait()  # Result has deliberately not returned/persisted.
            return 'unreachable'

    def payload(run_id,text): return {'threadId':server.SESSION,'runId':run_id,'messages':[{'id':str(uuid.uuid4()),'role':'user','content':text}],
        'tools':[],'context':[],'state':{},'forwardedProps':{}}
    async with server.app.router.lifespan_context(server.app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://offline') as client:
            if mode=='wait-start':
                result=await client.post('/api/ag-ui',json=payload(info['run_id'],'先让我选择'))
                assert result.status_code==200
                status=(await client.get('/spike/status')).json()
                assert status['active']['status']=='waiting' and CALL in status['pending']
                history=await server.saved()
                assert any(isinstance(p,ToolCallPart) and p.tool_call_id==CALL for m in history.messages for p in m.parts)
                save(state/'result-start.json',{'pid':os.getpid(),'status':'waiting','pending_call':CALL,'model_calls':load(calls_path)})
            elif mode=='wait-resume':
                status=(await client.get('/spike/status')).json()
                assert CALL in status['pending'] and status['runs'][-1]['status']=='waiting'
                transcript=(await client.get('/spike/transcript')).json()
                assert '先让我选择' in json.dumps(transcript,ensure_ascii=False)
                before=load(calls_path)
                replay=await client.post('/api/ag-ui',json=payload(info['run_id'],'清雅茶室'))
                assert replay.status_code==409 and load(calls_path)==before
                result=await client.post('/api/ag-ui',json=payload(str(uuid.uuid4()),'清雅茶室'))
                assert result.status_code==200
                status=(await client.get('/spike/status')).json()
                assert status['active']['status']=='completed' and status['pending']=={}
                history=await server.saved()
                answers=[p for m in history.messages for p in m.parts if isinstance(p,ToolReturnPart) and p.tool_name=='ask_user_question']
                assert len(answers)==1 and answers[0].tool_call_id==CALL
                save(state/'result-resume.json',{'pid':os.getpid(),'pending_restored':True,'same_call_answered_once':True,
                    'same_run_replay_http':replay.status_code,'model_calls':load(calls_path),'status':'completed'})
            elif mode=='crash-start':
                task=asyncio.create_task(client.post('/api/ag-ui',json=payload(info['run_id'],'执行一次外部操作')))
                await asyncio.wait_for(entered.wait(),15)
                supplement=payload(info['run_id'],'重启后先核对，不重复执行')
                body=json.dumps(supplement).encode()
                async def receive(): return {'type':'http.request','body':body,'more_body':False}
                receipt=await server.steer(Request({'type':'http','method':'POST','path':'/spike/steer',
                    'headers':[(b'content-type',b'application/json')]},receive))
                assert receipt.status_code==202
                effect=await server.steps.get_tool_effect(run_id=info['run_id'],tool_call_id=EFFECT_CALL)
                assert effect is not None and effect.status!='completed'
                snapshot=await server.steps.latest_snapshot(run_id=info['run_id'],include_interrupted=True)
                assert snapshot is not None
                assert not any(isinstance(p,ToolReturnPart) and p.tool_call_id==EFFECT_CALL for m in snapshot.messages for p in m.parts)
                save(state/'kill-ready.json',{'pid':os.getpid(),'effect_count':effect_count(),'effect_status':effect.status,
                    'run_status':server.active['status'],'receipt_status':json.loads(receipt.body)['status']})
                await task  # Parent sends SIGKILL rather than invoking graceful cleanup.
            else:
                before=load(calls_path)
                status=(await client.get('/spike/status')).json()
                old=next(r for r in status['runs'] if r['id']==info['run_id'])
                assert old['status']=='interrupted' and old['done'] and status['pending']=={}
                assert old['steering'][0]['status']=='not_delivered'
                effect=await server.steps.get_tool_effect(run_id=info['run_id'],tool_call_id=EFFECT_CALL)
                assert effect is not None and effect.status!='completed'
                assert effect_count()==1 and load(calls_path)==before, 'Startup must not replay model or side effect'
                replay=await client.post('/api/ag-ui',json=payload(info['run_id'],'重新执行旧Run'))
                assert replay.status_code==409 and effect_count()==1 and load(calls_path)==before
                response=await client.post('/api/ag-ui',json=payload(str(uuid.uuid4()),'只核对已有结果，然后继续'))
                assert response.status_code==200 and effect_count()==1
                assert (await client.get('/spike/status')).json()['active']['status']=='completed'
                save(state/'result-restart.json',{'pid':os.getpid(),'startup_status':old['status'],
                    'queued_input_status':old['steering'][0]['status'],'unresolved_tool_status':effect.status,
                    'same_run_replay_http':replay.status_code,'no_automatic_model_or_effect_replay':True,
                    'effect_count_after_explicit_continue':effect_count(),'model_calls':load(calls_path)})


def check():
    report={'model_provider_called':False,'cloud_called':False,'workspace':'temporary files and real SQLite/Git','scenarios':{}}
    env={**os.environ,'PYDANTIC_AI_NO_BANNER':'1'}
    with tempfile.TemporaryDirectory(prefix='dreamable-worker-restart-') as directory:
        root=Path(directory)
        for name in ('wait','crash'):
            state=root/name;state.mkdir()
            save(state/'scenario.json',{'run_id':str(uuid.uuid4()),'operation_id':str(uuid.uuid4())})
        def run(mode,state):
            result=subprocess.run([sys.executable,__file__,'--worker',mode,str(state)],env=env,text=True,capture_output=True,timeout=30)
            assert result.returncode==0, result.stdout+result.stderr
        run('wait-start',root/'wait');run('wait-resume',root/'wait')
        first=load(root/'wait/result-start.json');second=load(root/'wait/result-resume.json')
        assert first['pid']!=second['pid'] and second['model_calls']==first['model_calls']+1
        report['scenarios']['ask_user_process_restart']={'before':first,'after':second}
        state=root/'crash'
        process=subprocess.Popen([sys.executable,__file__,'--worker','crash-start',str(state)],env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            deadline=time.monotonic()+20
            while not (state/'kill-ready.json').exists():
                if process.poll() is not None:
                    stdout,stderr=process.communicate();raise AssertionError(stdout+stderr)
                if time.monotonic()>deadline: raise AssertionError('Effect never reached the unpersisted-result window')
                time.sleep(.05)
            before=load(state/'kill-ready.json')
            assert before['effect_count']==1 and before['run_status']=='running' and before['receipt_status']=='queued'
            process.kill();process.communicate(timeout=5)
            assert process.returncode<0
        finally:
            if process.poll() is None: process.kill();process.communicate()
        run('crash-restart',state)
        after=load(state/'result-restart.json')
        assert before['pid']!=after['pid'] and after['effect_count_after_explicit_continue']==1
        report['scenarios']['hard_kill_unknown_effect']={'before':before,'kill_exit_code':process.returncode,'after':after,
            'external_effect_simulated':True,'production_exactly_once_verified':False}
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':
    if len(sys.argv)==4 and sys.argv[1]=='--worker': asyncio.run(worker(sys.argv[2],Path(sys.argv[3])))
    else: check()
