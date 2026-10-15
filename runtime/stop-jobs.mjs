// Host control only. Stop registered Coder jobs, never the fixed preview or unrelated processes.
import fs from 'node:fs';
import path from 'node:path';
const base=path.join(process.argv[2],'.pydantic-ai-harness/shell');
const jobs=[];
const isSupervisor=(pid,dir)=>{
 try{return fs.readFileSync('/proc/'+pid+'/cmdline').toString().split('\0').includes(dir)}
 catch(e){if(e.code==='ENOENT')return false;throw e}
};
const signal=(group,name)=>{try{process.kill(-group,name);return true}catch(e){if(e.code==='ESRCH')return false;throw e}};
const liveGroup=group=>fs.readdirSync('/proc').some(name=>{
 if(!/^\d+$/.test(name))return false;
 try{const stat=fs.readFileSync('/proc/'+name+'/stat','utf8');const [state,,pgrp]=stat.slice(stat.lastIndexOf(')')+2).split(' ');return Number(pgrp)===group&&!['Z','X'].includes(state)}
 catch(e){if(e.code==='ENOENT')return false;throw e}
});
if(fs.existsSync(base))for(const entry of fs.readdirSync(base,{withFileTypes:true})){
 if(!entry.isDirectory())continue; // Both persistent shell IDs and explicit background job IDs have handles.
 const dir=path.join(base,entry.name),handle=path.join(dir,'handle'),status=path.join(dir,'status.json');
 if(!fs.existsSync(handle))continue;
 if(fs.existsSync(status)&&Number.isInteger(JSON.parse(fs.readFileSync(status)).exit_code))continue;
 const [pid,group]=fs.readFileSync(handle,'utf8').trim().split(/\s+/).map(Number);
 if(!Number.isInteger(pid)||pid<2||group!==pid)throw Error('Unverified process group');
 if(!isSupervisor(pid,dir))continue; // Stale PID after filesystem-only resume.
 fs.writeFileSync(path.join(dir,'stop'),'');
 if(signal(group,'SIGTERM'))jobs.push({pid,group,dir});
}
if(jobs.length)await new Promise(resolve=>setTimeout(resolve,2000));
for(const {pid,group,dir} of jobs){
 if(!liveGroup(group))continue; // Zombies cannot execute or write, even when kill(0) reports them.
 if(!isSupervisor(pid,dir))throw Error('Supervisor exited but process group remains; pause required');
 signal(group,'SIGKILL');
}
console.log(jobs.length);
