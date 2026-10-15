"""Offline wiring regression, not a model-quality test. Uses SDK FunctionModel.

Real user-language trials are recorded separately in interaction-validation.json.
This check touches only a temporary receipt directory, never the user's project.
"""
import asyncio
import base64
import hashlib
import json
import random
import sqlite3
import struct
import tempfile
import uuid
import zlib
from pathlib import Path

from starlette.requests import Request
from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.messages import BinaryContent, UserPromptPart, ModelResponse, ToolCallPart, ToolReturnPart, TextPart
from pydantic_ai_harness import AskUser
from pydantic_ai_harness.ask_user import AskUserRequest, AskUserResponse, AskUserAnswer, ask_user_result
from pydantic_ai_harness.step_persistence import StepPersistence, SqliteStepStore
from pydantic_ai_harness.step_persistence.conversations import SqliteConversationStore
from pydantic_ai.exceptions import ModelAPIError
import server


async def check_pending_image_api(directory):
    """Exercise the real pending-answer route, adapter and SQLite media round trip."""
    import httpx
    from pydantic_ai.models.function import DeltaToolCall
    from pydantic_ai.workspaces import WorkspaceRef

    # A valid, incompressible PNG crosses the SDK's 64 KiB externalization threshold.
    def chunk(kind, data):
        return struct.pack('!I', len(data))+kind+data+struct.pack('!I', zlib.crc32(kind+data)&0xffffffff)
    pixels=random.Random(20261014).randbytes(160*160*3)
    scanlines=b''.join(b'\x00'+pixels[row*480:(row+1)*480] for row in range(160))
    image=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!IIBBBBB',160,160,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(scanlines))+chunk(b'IEND',b'')
    assert 64*1024 <= len(image) < 4_000_000
    state=Path(directory)/'pending-image';state.mkdir()
    (state/'runs').mkdir();(state/'workspace').mkdir()
    (state/'workspace/source.txt').write_text('offline image answer fixture\n')
    names=('STATE','PENDING','active','pending','steps','conversations','workspace','agent')
    original={name:getattr(server,name) for name in names}
    call_id='image-answer-choice'
    options={'questions':[{'header':'风格','question':'选择一个方向','options':[
        {'label':'清雅茶室','description':'暖白与茶绿。'},
        {'label':'现代管理','description':'浅灰与蓝色。'}]}]}
    model_calls=[]

    class TemporaryWorkspace:
        remote=False
        ready=True
        preview_url=None
        ref=WorkspaceRef(provider='offline-image-answer',id=state.name)
        async def working_dir(self): return '/workspace'
        async def resume(self): pass
        def status(self): return {'provider':'offline','ready':True}

    def images(messages):
        return [item for message in messages for part in message.parts if isinstance(part,UserPromptPart)
                and isinstance(part.content,list) for item in part.content if isinstance(item,BinaryContent)]

    async def reply(messages,info):
        model_calls.append(messages[:])
        answers=[part for message in messages for part in message.parts if isinstance(part,ToolReturnPart)
                 and part.tool_name=='ask_user_question']
        if not answers:
            yield {0:DeltaToolCall(name='ask_user_question',json_args=json.dumps(options,ensure_ascii=False),tool_call_id=call_id)}
        else:
            assert len(answers)==1 and answers[0].tool_call_id==call_id
            assert answers[0].content=={'风格':['清雅茶室']}
            received=images(messages)
            assert len(received)==1 and received[0].data==image and received[0].media_type=='image/png', 'Pending answer must retain the image for the model'
            yield '原问题已回答，参考图字节已收到。'

    def payload(content):
        return {'threadId':server.SESSION,'runId':str(uuid.uuid4()),'state':{},'tools':[],'context':[],
                'forwardedProps':{},'messages':[{'id':str(uuid.uuid4()),'role':'user','content':content}]}

    try:
        server.STATE=state;server.PENDING=state/'pending.json';server.active=None;server.pending={}
        server.workspace=TemporaryWorkspace()
        server.steps=SqliteStepStore(database=state/'agent.sqlite',max_snapshots_per_run=12)
        server.conversations=SqliteConversationStore(database=state/'agent.sqlite')
        server.agent=Agent(FunctionModel(stream_function=reply),capabilities=[server.interaction,AskUser(answerer=None),
            StepPersistence(store=server.steps,capture_frontier=True)],output_type=[str,DeferredToolRequests])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://offline') as client:
            asked=await client.post('/api/ag-ui',json=payload('先让我选择风格'))
            assert asked.status_code==200 and server.active['status']=='waiting' and call_id in server.pending
            answered=await client.post('/api/ag-ui',json=payload([{'type':'text','text':'清雅茶室'},
                {'type':'binary','mimeType':'image/png','data':base64.b64encode(image).decode()}]))
            assert answered.status_code==200 and server.active['status']=='completed' and server.pending=={}
        assert len(model_calls)==2 and len(images(model_calls[-1]))==1
        with sqlite3.connect(state/'agent.sqlite') as db:
            media=db.execute('SELECT sha256,size_bytes,bytes FROM media').fetchall()
            assert len(media)==1 and media[0]==(hashlib.sha256(image).hexdigest(),len(image),image)
            conversation=db.execute('SELECT messages FROM conversations').fetchone()[0]
            snapshots=[row[0] for row in db.execute('SELECT messages FROM snapshots')]
            uri='media+sha256://'+hashlib.sha256(image).hexdigest()
            assert uri in conversation and any(uri in snapshot for snapshot in snapshots)
        snapshot=await server.steps.latest_snapshot(run_id=server.active['id'],include_interrupted=True)
        assert snapshot is not None and images(snapshot.messages)[0].data==image
        assert next(message for message in snapshot.messages if images([message])).timestamp is None
        # A new official store instance resolves persisted references back to binary parts.
        restored=await SqliteConversationStore(database=state/'agent.sqlite').get(conversation_id=server.SESSION)
        assert len(images(restored.messages))==1 and images(restored.messages)[0].data==image
        # AG-UI can produce a user request with no timestamp. Preserve that official
        # message shape, then exercise the actual transcript route twice.
        image_request=next(message for message in restored.messages if images([message]))
        image_request.timestamp=None
        await server.conversations.save(summary=restored.summary,messages=restored.messages)
        reread=await server.saved()
        assert next(message for message in reread.messages if images([message])).timestamp is None
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://offline') as client:
            first=await client.get('/spike/transcript')
            second=await client.get('/spike/transcript')
        assert first.status_code==second.status_code==200
        transcript=first.json()
        assert transcript==second.json(), 'Reopening must preserve transcript order and IDs'
        ids=[message['id'] for message in transcript]
        assert len(ids)==len(set(ids)), 'Transcript IDs must be unique'
        users=[message for message in transcript if message['role']=='user']
        def text(message):
            content=message['content']
            return content if isinstance(content,str) else '\n'.join(part['text'] for part in content if part['type']=='text')
        assert [text(message) for message in users]==['先让我选择风格','清雅茶室'], 'Receipts must not duplicate the answer user message'
        transcript_images=[part for message in users if isinstance(message['content'],list)
                           for part in message['content'] if part['type']=='image']
        assert len(transcript_images)==1
        source=transcript_images[0]['source']
        assert source['type']=='data' and source['mimeType']=='image/png'
        assert base64.b64decode(source['value'],validate=True)==image
        print('PASS: pending-answer API retains valid PNG >=64KiB for FunctionModel, answers original AskUser once, externalizes to official SQLite media and restores bytes')
        print('PASS: transcript API handles a persisted image request with timestamp=None, retains original PNG bytes and returns stable ordered IDs without duplicate user receipts')
    finally:
        if server.active and server.active.get('task') and not server.active['task'].done():
            await server.active['task']
        for name,value in original.items(): setattr(server,name,value)


async def check_text_file_api(directory):
    """A file must reach the model as text and survive a real store reload."""
    import httpx
    from pydantic_ai.workspaces import WorkspaceRef
    state=Path(directory)/'text-file';state.mkdir()
    (state/'runs').mkdir();(state/'workspace').mkdir()
    (state/'workspace/source.txt').write_text('offline file attachment fixture\n')
    names=('STATE','PENDING','active','pending','steps','conversations','workspace','agent')
    original={name:getattr(server,name) for name in names}
    received=[]
    raw='店名：品茗\n要求：保留价格，只修改标题。\n'.encode()

    class TemporaryWorkspace:
        remote=False
        ready=True
        preview_url=None
        ref=WorkspaceRef(provider='offline-text-file',id=state.name)
        async def working_dir(self): return '/workspace'
        async def resume(self): pass
        def status(self): return {'provider':'offline','ready':True}

    async def reply(messages,info):
        received.extend(p.content for m in messages for p in m.parts if isinstance(p,UserPromptPart))
        yield '已读取需求附件。'

    def payload(data=raw,filename='需求.md',text='请按附件解释需求'):
        return {'threadId':server.SESSION,'runId':str(uuid.uuid4()),'state':{},'tools':[],'context':[],
            'forwardedProps':{},'messages':[{'id':str(uuid.uuid4()),'role':'user','content':[
                *([{'type':'text','text':text}] if text else []),
                {'type':'document','source':{'type':'data','mimeType':'text/plain','value':base64.b64encode(data).decode()},
                 'metadata':{'filename':filename}}]}]}

    try:
        server.STATE=state;server.PENDING=state/'pending.json';server.active=None;server.pending={}
        server.workspace=TemporaryWorkspace()
        server.steps=SqliteStepStore(database=state/'agent.sqlite',max_snapshots_per_run=12)
        server.conversations=SqliteConversationStore(database=state/'agent.sqlite')
        server.agent=Agent(FunctionModel(stream_function=reply),capabilities=[server.interaction,
            StepPersistence(store=server.steps,capture_frontier=True)])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://offline') as client:
            response=await client.post('/api/ag-ui',json=payload())
            assert response.status_code==200 and server.active['status']=='completed'
            assert any(isinstance(p,list) and server.FILE_TEXT_PREFIX+'需求.md]\n'+raw.decode() in p for p in received)
            assert not any(isinstance(i,BinaryContent) for p in received if isinstance(p,list) for i in p)
            restored=await SqliteConversationStore(database=state/'agent.sqlite').get(conversation_id=server.SESSION)
            assert any(isinstance(p,UserPromptPart) and isinstance(p.content,list) and raw.decode() in '\n'.join(
                x for x in p.content if isinstance(x,str)) for m in restored.messages for p in m.parts)
            transcript=(await client.get('/spike/transcript')).json()
            assert len([m for m in transcript if m['role']=='user'])==1, 'File receipts must not duplicate history'
            assert '需求.md' in json.dumps(transcript,ensure_ascii=False)
            # Same caption, different files: an earlier failed upload must not
            # consume the later successful file's history entry.
            _,earlier=server.user_input(json.dumps(payload(b'earlier unsent file','旧附件.md')).encode())
            server.write_json(state/'runs'/(str(uuid.uuid4())+'.json'),{
                'started':server.active['started']-1,'id':str(uuid.uuid4()),'prompt':server.message_text(earlier),
                'message':earlier.model_dump(mode='json',by_alias=True,exclude_none=True)})
            transcript=(await client.get('/spike/transcript')).json()
            users=[m for m in transcript if m['role']=='user']
            assert len(users)==2 and '旧附件.md' in json.dumps(users[0],ensure_ascii=False)
            assert '需求.md' in json.dumps(users[1],ensure_ascii=False)
            for data,name in ((b'x'*(server.TEXT_FILE_MAX_BYTES+1),'large.txt'),(b'%PDF-1.0','file.pdf'),
                              (b'\xff','bad.md'),(b'hello\x00world','binary.txt'),(b'x','../secret.txt'),(b'','empty.txt')):
                assert (await client.post('/api/ag-ui',json=payload(data,name))).status_code==400
            too_large=await client.post('/api/ag-ui',content=b' '*(8_000_001),headers={'Content-Type':'application/json'})
            assert too_large.status_code==413
        # Exact byte boundary, BOM, attachment-only prompt, no URL fetch, and count cap.
        for data in (b'x'*server.TEXT_FILE_MAX_BYTES,b'\xef\xbb\xbfhello'):
            _,message=server.user_input(json.dumps(payload(data,'boundary.txt')).encode())
            assert server.message_text(message)=='请按附件解释需求'
        _,message=server.user_input(json.dumps(payload(text='')).encode())
        assert server.message_text(message)=='请查看附件。'
        invalid=payload();invalid['messages'][0]['content'].append(invalid['messages'][0]['content'][-1])
        try: server.user_input(json.dumps(invalid).encode())
        except ValueError: pass
        else: raise AssertionError('Multiple attachments must be rejected')
        invalid=payload();invalid['messages'][0]['content'][-1]['source']={'type':'url','url':'http://private.invalid/file'}
        try: server.user_input(json.dumps(invalid).encode())
        except ValueError: pass
        else: raise AssertionError('Document URLs must not be fetched')
        assert server.inline_image_mime(base64.b64encode(b'\xff\xd8\xff'+b'x'*(server.IMAGE_MAX_BYTES-3)).decode())=='image/jpeg'
        print('PASS: real file API/FunctionModel/SQLite/history round trip, exact 256KB UTF-8 limit, 4MB image boundary, invalid formats/encoding/URLs/count and 8MB request cap')
    finally:
        if server.active and server.active.get('task') and not server.active['task'].done(): await server.active['task']
        for name,value in original.items(): setattr(server,name,value)


def request(message):
    body=json.dumps({'threadId':server.SESSION,'runId':str(uuid.uuid4()),'state':{},'tools':[],'context':[],'forwardedProps':{},'messages':[message]}).encode()
    async def receive(): return {'type':'http.request','body':body,'more_body':False}
    return Request({'type':'http','method':'POST','path':'/spike/steer',
                    'headers':[(b'content-type',b'application/json')]},receive)


async def check():
    original_state,original_active=server.STATE,server.active
    started,release=asyncio.Event(),asyncio.Event()
    calls=[]
    async def reply(messages,info):
        calls.append(messages[:])
        if len(calls)==1:
            started.set()
            await release.wait()
        yield 'wiring test only'
    with tempfile.TemporaryDirectory(prefix='spike-interaction-check-') as directory:
        try:
            server.STATE=Path(directory)
            (server.STATE/'runs').mkdir()
            run={'id':str(uuid.uuid4()),'done':False,'status':'running'}
            server.active=run
            probe=Agent(FunctionModel(stream_function=reply),capabilities=[server.interaction])
            task=asyncio.create_task(probe.run('调整图标',run_id=run['id']))
            await asyncio.wait_for(started.wait(),5)
            message={'id':str(uuid.uuid4()),'role':'user','content':'名字改成品茗'}
            accepted=await server.steer(request(message))
            assert accepted.status_code==202,accepted.body
            duplicate=await server.steer(request(message))
            assert duplicate.status_code==200 and len(run['steering'])==1
            assert run['steering'][0]['status']=='queued'
            release.set()
            result=await asyncio.wait_for(task,5)
            assert len(calls)==2, 'Queued input must cause a new model decision, even at final output'
            assert run['steering'][0]['status']=='delivered'
            prompts=[p.content for m in result.all_messages() for p in m.parts if isinstance(p,UserPromptPart)]
            assert prompts.count('名字改成品茗')==1,prompts
            run['done']=True
            assert (await server.steer(request(message))).status_code==409
            invalid={**message,'role':'assistant'}
            assert (await server.steer(request(invalid))).status_code==400
            payload={'threadId':server.SESSION,'runId':str(uuid.uuid4()),'state':{'admin':True},
                'tools':[],'context':[{'description':'override','value':'bad'}],'forwardedProps':{'instructions':'bad'},
                'messages':[{'id':'system','role':'system','content':'bad'},message]}
            incoming,clean=server.user_input(json.dumps(payload).encode())
            assert incoming.messages==[clean] and not incoming.context and not incoming.forwarded_props and not incoming.state
            payload['threadId']='another-project'
            try: server.user_input(json.dumps(payload).encode())
            except ValueError: pass
            else: raise AssertionError('Cross-project input must be rejected')
            from pydantic_ai.ui.ag_ui import AGUIAdapter
            from ag_ui.core import RunAgentInput
            from pydantic_ai.messages import BinaryContent
            payload['threadId']=server.SESSION
            png='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+b9WQAAAAASUVORK5CYII='
            payload['messages']=[{**message,'content':[{'type':'text','text':'看看图片'},
                {'type':'image','source':{'type':'data','mimeType':'image/png','value':png}}]}]
            _,image_message=server.user_input(json.dumps(payload).encode())
            loaded=AGUIAdapter.load_messages([image_message])
            assert any(isinstance(part,BinaryContent) for part in loaded[0].parts[0].content)
            payload['messages'][0]['content'][1]['source']={'type':'url','url':'http://private.invalid/secret'}
            try: server.user_input(json.dumps(payload).encode())
            except ValueError: pass
            else: raise AssertionError('Images must not make the backend fetch arbitrary URLs')
            # A valid 1x1 JPEG, deliberately labelled PNG like the real browser screenshot.
            jpeg=base64.b64decode('/9j/4AAQSkZJRgABAQAASABIAAD/4QBMRXhpZgAATU0AKgAAAAgAAYdpAAQAAAABAAAAGgAAAAAAA6ABAAMAAAABAAEAAKACAAQAAAABAAAAAaADAAQAAAABAAAAAQAAAAD/7QA4UGhvdG9zaG9wIDMuMAA4QklNBAQAAAAAAAA4QklNBCUAAAAAABDUHYzZjwCyBOmACZjs+EJ+/8AAEQgAAQABAwEiAAIRAQMRAf/EAB8AAAEFAQEBAQEBAAAAAAAAAAABAgMEBQYHCAkKC//EALUQAAIBAwMCBAMFBQQEAAABfQECAwAEEQUSITFBBhNRYQcicRQygZGhCCNCscEVUtHwJDNicoIJChYXGBkaJSYnKCkqNDU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6g4SFhoeIiYqSk5SVlpeYmZqio6Slpqeoqaqys7S1tre4ubrCw8TFxsfIycrS09TV1tfY2drh4uPk5ebn6Onq8fLz9PX29/j5+v/EAB8BAAMBAQEBAQEBAQEAAAAAAAABAgMEBQYHCAkKC//EALURAAIBAgQEAwQHBQQEAAECdwABAgMRBAUhMQYSQVEHYXETIjKBCBRCkaGxwQkjM1LwFWJy0QoWJDThJfEXGBkaJicoKSo1Njc4OTpDREVGR0hJSlNUVVZXWFlaY2RlZmdoaWpzdHV2d3h5eoKDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uLj5OXm5+jp6vLz9PX29/j5+v/bAEMAAgICAgICAwICAwUDAwMFBgUFBQUGCAYGBgYGCAoICAgICAgKCgoKCgoKCgwMDAwMDA4ODg4ODw8PDw8PDw8PD//bAEMBAgICBAQEBwQEBxALCQsQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEP/dAAQAAf/aAAwDAQACEQMRAD8A/byiiiswP//Z')
            for kind in ('binary','image'):
                def attachment(data):
                    return {'type':'binary','mimeType':'image/png','data':data} if kind=='binary' else \
                           {'type':'image','source':{'type':'data','mimeType':'image/png','value':data}}
                payload['messages']=[{**message,'content':[{'type':'text','text':'参考图'},attachment(base64.b64encode(jpeg).decode())]}]
                _,normalized=server.user_input(json.dumps(payload).encode())
                binary=next(part for part in AGUIAdapter.load_messages([normalized])[0].parts[0].content if isinstance(part,BinaryContent))
                assert binary.data==jpeg and binary.media_type=='image/jpeg'
                payload['messages'][0]['content'][1]=attachment(base64.b64encode(b'unknown-image-format').decode())
                try: server.user_input(json.dumps(payload).encode())
                except ValueError: pass
                else: raise AssertionError('Unknown image bytes must be rejected')
            for invalid in ('not base64!',base64.b64encode(b'\xff\xd8\xff'+b'x'*4_000_000).decode()):
                try: server.inline_image_mime(invalid)
                except ValueError: pass
                else: raise AssertionError('Invalid base64 and oversized image bytes must be rejected')
            print('PASS: image/binary inputs normalize real JPEG mislabelled PNG and reject unknown bytes, invalid base64 and >4MB images')
            async def protocol_reply(messages,info): yield '协议接通'
            adapter=AGUIAdapter(agent=Agent(FunctionModel(stream_function=protocol_reply)),
                run_input=RunAgentInput(thread_id=server.SESSION,run_id=str(uuid.uuid4()),messages=[clean],tools=[],context=[],state={},forwarded_props={}))
            events=[event async for event in adapter.run_stream()]
            types=[e.type for e in events]
            assert types[0]=='RUN_STARTED' and types[-1]=='RUN_FINISHED',types
            assert 'TEXT_MESSAGE_CONTENT' in types,types
            # Replay the actual adapter output through the official JS client.
            # FunctionModel is used here only to validate the protocol wiring.
            import subprocess
            sse=''.join([chunk async for chunk in adapter.encode_stream(event_iter(events))])
            js="""
const {HttpAgent}=require('./chat-ui/node_modules/@ag-ui/client');
const assert=require('node:assert/strict'),fs=require('node:fs');
const sse=fs.readFileSync(0,'utf8');
const a=new HttpAgent({url:'http://local.invalid/api/ag-ui',fetch:async()=>new Response(sse,{headers:{'Content-Type':'text/event-stream'}})});
a.addMessage({id:'test',role:'user',content:'协议验证'});
a.runAgent().then(()=>{assert(a.messages.some(m=>m.role==='assistant'&&JSON.stringify(m.content).includes('协议接通')));console.log('PASS: official HttpAgent consumes real Pydantic AG-UI events')}).catch(e=>{console.error(e);process.exitCode=1});
"""
            subprocess.run(['node','-e',js],input=sse,text=True,check=True,cwd=server.ROOT)
            options={'questions':[{'header':'风格','question':'你想用哪种风格？','options':[
                {'label':'清雅茶室','description':'暖米白配茶绿，留白充足。'},
                {'label':'现代管理','description':'浅灰配蓝色，紧凑清晰。'}],'multi_select':False}]}
            def choice_reply(messages,info):
                answers=[p for m in messages for p in m.parts if isinstance(p,ToolReturnPart) and p.tool_name=='ask_user_question']
                if answers:
                    assert answers[-1].content=={'风格':['清雅茶室']}
                    return ModelResponse(parts=[TextPart('已选择清雅茶室，先不改代码。')])
                return ModelResponse(parts=[ToolCallPart(tool_name='ask_user_question',args=options,tool_call_id='style-choice')])
            chooser=Agent(FunctionModel(choice_reply),capabilities=[AskUser(answerer=None)],output_type=[str,DeferredToolRequests])
            paused=await chooser.run('有什么设计风格给我选择？')
            assert isinstance(paused.output,DeferredToolRequests)
            question=AskUserRequest.from_tool_call(paused.output.calls[0])
            answer=ask_user_result(question,AskUserResponse(answers=(AskUserAnswer(header='风格',custom_answer='清雅茶室'),)))
            resumed=await chooser.run(message_history=paused.all_messages(),deferred_tool_results=DeferredToolResults(calls={question.id:answer}))
            assert resumed.output=='已选择清雅茶室，先不改代码。'
            print('PASS: official AskUser choice defers, validates and resumes the same tool call; model is an offline fixture')
            original_steps=server.steps
            try:
                server.steps=SqliteStepStore(database=Path(directory)/'error-check.sqlite')
                async def disconnected(messages,info):
                    raise ModelAPIError('offline','Connection error. sensitive-body-must-not-leak')
                    yield 'unreachable'
                failed_id=str(uuid.uuid4())
                broken=AGUIAdapter(agent=Agent(FunctionModel(stream_function=disconnected),
                    capabilities=[StepPersistence(store=server.steps)]),
                    run_input=RunAgentInput(thread_id=server.SESSION,run_id=failed_id,messages=[clean],tools=[],context=[],state={},forwarded_props={}))
                failures=[event async for event in broken.run_stream(run_id=failed_id)]
                assert failures[-1].type=='RUN_ERROR'
                detail=await server.failure_details(failed_id)
                assert detail['error']=='ModelAPIError' and '连接中断' in detail['error_reason'],detail
                assert 'sensitive-body' not in json.dumps(detail)
                print('PASS: original model connection failure is recovered from official StepStore, with safe human-readable output')
            finally:
                server.steps=original_steps
            await check_pending_image_api(directory)
            await check_text_file_api(directory)
            print('PASS: AG-UI input validation, server-owned context, SDK steering queue, idempotence, delivery, closed-run rejection')

        finally:
            server.STATE,server.active=original_state,original_active


async def event_iter(events):
    for event in events: yield event

if __name__=='__main__': asyncio.run(check())
