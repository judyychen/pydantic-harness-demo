import React, {createContext, useContext, useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {HttpAgent} from '@ag-ui/client';
import {CopilotKitProvider, CopilotChatConfigurationProvider, CopilotChatView, CopilotChatUserMessage, useDefaultRenderTool} from '@copilotkit/react-core/v2';
import '@copilotkit/react-core/v2/styles.css';
import './chat.css';

const SESSION='dreamable-pydantic-next-m1';
let accepted=()=>{};
const client=new HttpAgent({agentId:'development',threadId:SESSION,url:new URL('/api/ag-ui',location.href).href,
  fetch:async(url,init)=>{
    const body=JSON.parse(init.body);
    // Server owns history; do not resend a growing transcript or frontend tools.
    body.messages=body.messages.slice(-1);body.tools=[];body.context=[];body.state={};body.forwardedProps={};
    const onAccepted=accepted;
    const response=await fetch(url,{...init,body:JSON.stringify(body)});
    if(response.ok)onAccepted();
    return response;
  }});
const agents={development:client};
const Context=createContext(null);
const IMAGE_TYPES={'png':'image/png','jpg':'image/jpeg','jpeg':'image/jpeg','webp':'image/webp'};
const TEXT_EXTENSIONS='txt md markdown csv json log yaml yml xml html css js jsx ts tsx py sql sh toml ini cfg svg'.split(' ');
const ATTACHMENT_ACCEPT=['.png','.jpg','.jpeg','.webp',...TEXT_EXTENSIONS.map(ext=>'.'+ext)].join(',');
const ATTACHMENT_HINT='一次添加一个附件。PNG、JPEG、WebP 图片最大 4 MB（4,000,000 字节）；UTF-8 文本/代码文件最大 256 KB（256,000 字节）。支持 '+TEXT_EXTENSIONS.join('、')+'；暂不支持 PDF、Word、Excel、ZIP。';
function sizeLabel(size){return size>=1000000?(size/1000000).toFixed(1)+' MB':size>=1000?(size/1000).toFixed(1)+' KB':size+' 字节'}
function readDataUrl(file){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(Error('读取附件失败，请重新选择。'));reader.readAsDataURL(file)})}
function UserMessage(props){
  const parts=Array.isArray(props.message.content)?props.message.content:[];
  const files=parts.filter(p=>p.type==='text').map(p=>p.text.match(/^\[附件文件：([^\n]+)\]\n([\s\S]*)$/)).filter(Boolean);
  if(!files.length)return <CopilotChatUserMessage {...props}/>;
  const body=parts.filter(p=>p.type==='text'&&!/^\[附件文件：[^\n]+\]\n/.test(p.text)).map(p=>p.text).join('\n');
  return <CopilotChatUserMessage {...props} messageRenderer={()=><div className="user-attachment-content">{body&&<CopilotChatUserMessage.MessageRenderer content={body}/>} {files.map((file,index)=><details className="message-attachment" key={index}><summary>附件：{file[1]}</summary><pre>{file[2]}</pre></details>)}</div>}/>;
}
const messageView={userMessage:UserMessage};
const labels={running:'执行中',cancelling:'正在停止',cancelled:'已停止，可继续发送',waiting:'等待你的回答',completed:'已完成',failed:'执行失败',interrupted:'已中断'};
async function json(url,body){
  const r=await fetch(url,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});
  const value=await r.json();if(!r.ok)throw Error(value.error||`请求失败（${r.status}）`);return value;
}
function request(message,runId=crypto.randomUUID()){
  return {threadId:SESSION,runId,messages:[message],tools:[],context:[],state:{},forwardedProps:{}};
}
function Composer(){
  const {draft,setDraft,send,stop,canStop,stopping,sending,ready,task,attachment,attachmentReading,selectAttachment,removeAttachment}=useContext(Context);
  const input=useRef(null);
  const connected=!task?.workspace?.busy&&(task?.workspace?.provider!=='e2b'||task.workspace.ready);
  return <form className="composer" onSubmit={e=>{e.preventDefault();send()}}>
    {attachment&&<div className="attachment-preview">{attachment.kind==='image'?<img src={attachment.url} alt={attachment.name}/>:<span className="attachment-icon" aria-hidden="true">▤</span>}<div><strong>{attachment.name}</strong><small>{sizeLabel(attachment.size)}</small></div><button type="button" onClick={removeAttachment} aria-label="移除附件" title="移除附件">×</button></div>}
    {attachmentReading&&<div className="attachment-reading" role="status">正在读取附件… <button type="button" onClick={removeAttachment}>取消</button></div>}
    <textarea aria-label="给开发助手发消息" placeholder="描述你想做的产品，也可以随时补充或提问…" value={draft}
      onChange={e=>setDraft(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();send()}}}/>
    <div className="composer-actions"><input ref={input} className="attachment-input" type="file" accept={ATTACHMENT_ACCEPT} aria-label="选择图片或文件" onChange={e=>{const file=e.target.files?.[0];e.target.value='';if(file)selectAttachment(file)}}/>
      <button className="add-attachment" type="button" title={ATTACHMENT_HINT} aria-label="添加图片或文件" onClick={()=>input.current?.click()}>＋ 附件</button>
      <small>{!connected?'先连接右侧沙箱':labels[task?.active?.status]||(Object.keys(task?.pending||{}).length?'等待你的回答':'准备就绪')}</small>
      <button type="button" onClick={stop} disabled={!canStop||stopping} aria-label="停止任务" aria-busy={stopping}>{stopping?'停止中…':'■ 停止'}</button>
      <button className="send" type="submit" disabled={!connected||!ready||(!draft.trim()&&!attachment)||attachmentReading||sending||stopping}>{task?.active&&!task.active.done?'发送补充':'发送'}</button>
    </div>
  </form>;
}
const toolNames={shell:'执行项目命令',list_files:'查看项目文件',read_file:'读取文件',write_file:'写入文件',edit_file:'修改文件',load_capability:'加载开发规则',grep:'搜索代码',glob:'查找文件',ask_user_question:'需要你补充信息'};
function Tool({name,toolCallId,parameters,status,result}){
  const {send,task,ready,sending,stopping}=useContext(Context);
  const [submitted,setSubmitted]=useState(false),[choices,setChoices]=useState({});
  const [materials,setMaterials]=useState([]);
  useEffect(()=>{if(name==='ask_user_question')fetch('/spike/visual/'+encodeURIComponent(toolCallId)).then(r=>r.ok?r.json():{candidates:[]}).then(v=>setMaterials(v.candidates)).catch(()=>setMaterials([]))},[name,toolCallId]);
  const questions=parameters?.questions||[];
  const waiting=!!task?.pending?.[toolCallId];
  const enabled=waiting&&(!task?.active||task.active.done)&&ready&&!sending&&!stopping&&!submitted&&!task?.workspace?.busy&&(task?.workspace?.provider!=='e2b'||task.workspace.ready);
  async function choose(text){setSubmitted(true);if(!await send(text))setSubmitted(false)}
  function select(q,label){setChoices(old=>({...old,[q.header]:q.multi_select?(old[q.header]?.includes(label)?old[q.header].filter(x=>x!==label):[...(old[q.header]||[]),label]):[label]}))}
  if(name==='ask_user_question')return <div className="question-card"><strong>{waiting?'请选择':'选择记录'}</strong>{questions.map((q,i)=><div key={q.header||i}><p>{q.question}</p><div className={materials.length?"question-options visual-options":"question-options"}>{q.options?.map(o=>{const material=materials.find(m=>m.label===o.label);return <div key={o.label}>{material&&<div className="visual-sample">{material.image&&<a href={material.image.url} target="_blank" rel="noreferrer" aria-label={'放大'+o.label+'截图'}><img src={material.image.url} alt={o.label+'预览图'} loading="lazy"/></a>}<a href={material.html.url} target="_blank" rel="noreferrer">打开{o.label}预览</a></div>}<button type="button" disabled={!enabled} aria-pressed={choices[q.header]?.includes(o.label)||false} onClick={()=>questions.length===1&&!q.multi_select?choose(o.label):select(q,o.label)}><strong>{o.label}</strong><span>{o.description}</span></button></div>})}</div></div>)}
    {(questions.length>1||questions[0]?.multi_select)&&waiting&&<button type="button" disabled={!enabled||questions.some(q=>!choices[q.header]?.length)} onClick={()=>choose(questions.map(q=>q.header+'：'+choices[q.header].join('、')).join('\n'))}>确认选择</button>}
    <small>{waiting?(submitted?'已提交，正在处理…':'点击卡片即可提交；也可以在输入框说自己的想法。'):(result?'已回答或已取消。':'正在准备选项…')}</small></div>;
  return <details className="tool-card"><summary>{toolNames[name]||name}<span>{status==='complete'?'已返回':'处理中'}</span></summary><pre>{JSON.stringify(parameters,null,2)}</pre>{result&&<pre>{result}</pre>}</details>;
}
function Chat(){
  const [messages,setMessages]=useState([]),[draft,setDraft]=useState(''),[task,setTask]=useState(null);
  const [ready,setReady]=useState(false),[sending,setSending]=useState(false),[stopping,setStopping]=useState(false),[error,setError]=useState('');
  const [attachment,setAttachment]=useState(null),[attachmentReading,setAttachmentReading]=useState(false);
  const attachmentRef=useRef(null),attachmentSequence=useRef(0),attachmentReadingRef=useRef(false);
  const sendingRef=useRef(false),stopRef=useRef(false),taskRef=useRef(null);
  const active=task?.active;
  const lastRun=active||task?.runs?.at(-1);
  const visibleError=error||(lastRun?.status==='failed'&&lastRun.error_reason?lastRun.error_reason+'请查看右侧记录中的文件改动与保存状态。':'');
  const canStop=!!((active&&!active.done)||Object.keys(task?.pending||{}).length);
  function updateTask(v){taskRef.current=v;setTask(v)}
  async function refreshTask(){const v=await json('/spike/status');updateTask(v);return v}
  async function sync(){if(!client.isRunning){const ms=await json('/spike/transcript');if(!client.isRunning)client.setMessages(ms)}}
  function fail(e){setError(e.message);window.parent.spikeNotice?.(e.message)}
  function removeAttachment(){attachmentSequence.current++;attachmentRef.current=null;setAttachment(null);attachmentReadingRef.current=false;setAttachmentReading(false);document.querySelector('textarea')?.focus()}
  async function selectAttachment(file){
    const id=++attachmentSequence.current;
    attachmentReadingRef.current=true;setAttachmentReading(true);setError('');
    try{
      if(file.name.length>180||/[\/\\\x00-\x1f]/.test(file.name))throw Error('文件名过长或包含不支持的字符，请重命名后重试。');
      const ext=file.name.split('.').at(-1).toLowerCase();
      const imageType=IMAGE_TYPES[ext];
      if(!imageType&&!TEXT_EXTENSIONS.includes(ext))throw Error('暂不支持该文件类型。请添加 PNG、JPEG、WebP 图片或 UTF-8 文本/代码文件；PDF、Word、Excel、ZIP 暂不支持。');
      const limit=imageType?4000000:256000;
      if(!file.size)throw Error('不能添加空文件。');
      if(file.size>limit)throw Error(imageType?'图片不能超过 4 MB（4,000,000 字节）。':'文本文件不能超过 256 KB（256,000 字节）。');
      if(!imageType){let content;try{content=new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer())}catch{throw Error('文件不是有效的 UTF-8 文本，请转换编码后重试。')}if(/[\x00-\x08\x0b\x0c\x0e-\x1f]/.test(content))throw Error('文件包含二进制内容，请添加 UTF-8 文本文件。')}
      const url=await readDataUrl(file);
      if(id!==attachmentSequence.current)return;
      const selected={id,name:file.name,size:file.size,kind:imageType?'image':'document',mimeType:imageType||'text/plain',url,value:url.slice(url.indexOf(',')+1)};
      attachmentRef.current=selected;setAttachment(selected);document.querySelector('textarea')?.focus();
    }catch(e){if(id===attachmentSequence.current)fail(e)}
    finally{if(id===attachmentSequence.current){attachmentReadingRef.current=false;setAttachmentReading(false)}}
  }
  useDefaultRenderTool({render:props=><Tool {...props}/>},[]);
  useEffect(()=>{
    const sub=client.subscribe({onMessagesChanged:({messages})=>setMessages([...messages]),onRunErrorEvent:({event})=>fail(Error(event.message))});
    window.spikeSetTaskState=updateTask;
    window.spikeSync=()=>sync().catch(fail);
    window.spikeDraft=(text,append=false)=>{setDraft(old=>append?text+'\n'+old:text);document.querySelector('textarea')?.focus()};
    Promise.all([sync(),refreshTask()]).then(()=>setReady(true)).catch(fail);
    const poll=window.parent===window?setInterval(()=>refreshTask().catch(fail),1200):null;
    return()=>{sub.unsubscribe();if(poll)clearInterval(poll)};
  },[]);
  async function stop(){
    if(stopRef.current||!canStop||taskRef.current?.active?.status==='cancelling')return;
    stopRef.current=true;setStopping(true);setError('');
    try{await json('/spike/stop',{});await refreshTask();await sync()}
    catch(e){fail(e)}finally{stopRef.current=false;setStopping(false)}
  }
  window.spikeStop=stop;
  async function send(text=draft){
    const selectedAttachment=attachmentRef.current;
    if(!ready||(!text.trim()&&!selectedAttachment)||attachmentReadingRef.current||sendingRef.current||stopRef.current||taskRef.current?.active?.status==='cancelling')return false;
    if(taskRef.current?.workspace?.busy){fail(Error('工作区操作中，请稍后发送。'));return false}
    if(taskRef.current?.workspace?.provider==='e2b'&&!taskRef.current.workspace.ready){fail(Error('请先连接或恢复右侧沙箱。'));return false}
    const body=text.trim()?text:'请查看附件。';
    const message={id:crypto.randomUUID(),role:'user',content:body};
    if(selectedAttachment)message.content=[{type:'text',text:body},{type:selectedAttachment.kind,source:{type:'data',mimeType:selectedAttachment.mimeType,value:selectedAttachment.value},...(selectedAttachment.kind==='document'?{metadata:{filename:selectedAttachment.name}}:{})}];
    const sendingToken=Symbol();
    sendingRef.current=sendingToken;setSending(true);setError('');
    const release=()=>{if(sendingRef.current===sendingToken){sendingRef.current=false;setSending(false)}};
    const accept=()=>{setDraft(old=>old===text?'':old);if(attachmentRef.current===selectedAttachment){attachmentRef.current=null;setAttachment(null)}release()};
    try{
      const current=await refreshTask();
      if(current.active&&!current.active.done){
        await json('/spike/steer',request(message,current.active.id));client.addMessage(message);accept();
        window.parent.spikeNotice?.('已收到补充；将在下一次模型判断时处理。');
      }else{
        client.addMessage(message);accepted=accept;
        await client.runAgent({runId:crypto.randomUUID()});
        await refreshTask();await sync();
      }
      return true;
    }catch(e){fail(e);await sync().catch(()=>{});return false}
    finally{release()}
  }
  return <Context.Provider value={{draft,setDraft,send,stop,canStop,stopping:stopping||active?.status==='cancelling',sending,ready,task,attachment,attachmentReading,selectAttachment,removeAttachment}}>
    {visibleError&&<div role="alert" className="error">{visibleError}</div>}
    <CopilotChatView className="chat-view" messageView={messageView} messages={messages} isRunning={!!(active&&!active.done)} input={Composer} welcomeScreen={false} autoScroll="pin-to-bottom" scrollView={{scrollToBottomButton:{'aria-label':'回到最新消息'}}}/>
  </Context.Provider>;
}
// Local Spike: direct connection to the same-origin Python server; no hosted Copilot service.
createRoot(document.getElementById('root')).render(
  <CopilotKitProvider agents__unsafe_dev_only={agents} agentId="development" enableInspector={false} showIntelligenceIndicator={false}>
    <CopilotChatConfigurationProvider agentId="development" threadId={SESSION} labels={{chatDisclaimerText:'',chatInputPlaceholder:'描述你想做的产品…',assistantMessageToolbarCopyMessageLabel:'复制回复'}}>
      <Chat/>
    </CopilotChatConfigurationProvider>
  </CopilotKitProvider>
);
