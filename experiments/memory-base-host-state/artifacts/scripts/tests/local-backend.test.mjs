import assert from 'node:assert/strict';
import { execFile, execFileSync, spawn } from 'node:child_process';
import fs from 'node:fs';
import { mkdtemp, readFile, writeFile } from 'node:fs/promises';
import { createServer } from 'node:net';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { randomUUID } from 'node:crypto';
import { promisify } from 'node:util';
import test from 'node:test';
import { clearInstanceState, publishHostState } from '../host-state.mjs';

const exec = promisify(execFile);
const root = resolve(import.meta.dirname, '../..');
const pause = ms => new Promise(resolve=>setTimeout(resolve,ms));
function processIdentity(pid) {
  if(!Number.isInteger(pid)||pid<1)return null;
  try {
    if(process.platform==='win32'){
      const script=`$p=Get-CimInstance Win32_Process -Filter 'ProcessId=${pid}'; if($p){@{command=$p.CommandLine;created=$p.CreationDate.ToUniversalTime().ToString('o')}|ConvertTo-Json -Compress}`;
      const text=execFileSync('powershell.exe',['-NoProfile','-NonInteractive','-Command',script],{encoding:'utf8',windowsHide:true,timeout:15000});
      return text.trim()?JSON.parse(text):null;
    }
    const stat=fs.readFileSync(`/proc/${pid}/stat`,'utf8').split(') ')[1].split(' ');
    return {created:stat[19],command:fs.readFileSync(`/proc/${pid}/cmdline`,'utf8').replaceAll('\0',' ').trim()};
  }catch{return null;}
}
async function until(check,timeout=30000){
  const deadline=Date.now()+timeout;
  while(Date.now()<deadline){const result=await check();if(result)return result;await pause(50);}
  throw new Error('Fixture did not settle within its bounded wait');
}
const readJson=async path=>{try{return JSON.parse(await readFile(path,'utf8'));}catch{return null;}};

async function stateFailureFixture(mode){
  const data=await mkdtemp(join(root,'.tools/state-test-'));const socket=createServer();
  await new Promise(resolve=>socket.listen(0,'127.0.0.1',resolve));const port=socket.address().port;
  await new Promise(resolve=>socket.close(resolve));
  const preload=join(data,'fault-injection.mjs');const ownership=join(data,'child-identity.json');
  await writeFile(preload,`import fs from 'node:fs';import {execFileSync} from 'node:child_process';import {syncBuiltinESMExports} from 'node:module';
    const processIdentity=${processIdentity.toString()};
    if(process.argv[2]==='run'){
      const original=fs.renameSync;let failures=0;
      fs.renameSync=(from,to)=>{
        if(to===${JSON.stringify(join(data,'host/state.json'))}){
          const next=JSON.parse(fs.readFileSync(from,'utf8'));
          const mode=${JSON.stringify(mode)};
          const inject=(next.phase==='running'&&(mode==='persistent'||mode==='transient'&&failures<3))
            ||(mode==='initial'&&next.phase==='starting'&&!next.childPid)||(mode==='restarting'&&next.phase==='restarting');
          if(inject){
            if(failures===0)fs.writeFileSync(${JSON.stringify(ownership)},JSON.stringify({pid:next.childPid,identity:processIdentity(next.childPid)}));
            failures++;
            fs.appendFileSync(${JSON.stringify(join(data,'rename-injection.jsonl'))},JSON.stringify({attempt:failures,phase:next.phase,code:'EPERM',completeJSON:true})+'\\n');
            const error=new Error('Explicit rename fault injection');error.code='EPERM';error.errno=-4048;throw error;
          }
        }
        return original(from,to);
      };syncBuiltinESMExports();
    }`);
  const env={...process.env,PORT:String(port),HOST:'127.0.0.1',DATA_DIR:data,
    AGENT_BUBBLE_TOKEN:'test-connection-token-with-32-characters',DEEPSEEK_API_KEY:'test-model-key',DASHSCOPE_API_KEY:'',
    ASR_ENABLED:'false',ARTICLE_BROWSER_ENABLED:'false',MEMORY_BASE_URL:'',MEMORY_BASE_TOKEN:'',NODE_OPTIONS:'--import='+pathToFileURL(preload).href};
  const instance=randomUUID();const handle=spawn(process.execPath,[join(root,'scripts/local-backend.mjs'),'run',instance],{cwd:root,env,windowsHide:true,stdio:['ignore','pipe','pipe']});
  let text='';for(const stream of [handle.stdout,handle.stderr])stream.on('data',data=>{text+=data;});
  const exited=new Promise(resolve=>handle.once('exit',(code,signal)=>resolve({code,signal})));
  const stateFile=join(data,'host/state.json');const lockFile=join(data,'host/lock.json');
  const command=action=>exec(process.execPath,[join(root,'scripts/local-backend.mjs'),action],{cwd:root,env,windowsHide:true,timeout:30000});
  async function health(){try{return JSON.parse((await command('status')).stdout).backendHealthy;}catch{return false;}}
  return {data,port,instance,handle,exited,stateFile,lockFile,command,health,async cleanup(){
    if(handle.exitCode===null){await command('stop').catch(()=>{});if(handle.exitCode===null){handle.kill();await exited;}}
    const owned=await readJson(ownership);const actual=processIdentity(owned?.pid);let fallbackCleanup=false;
    if(actual){
      assert.deepEqual(actual,owned.identity,'Never terminate a reused or different process');
      assert.ok(actual.command.includes(join(root,'dist/server/index.js')),'Never terminate an unrelated backend');
      if(process.platform==='win32')await exec('taskkill',['/PID',String(owned.pid),'/T','/F'],{windowsHide:true});
      else process.kill(owned.pid,'SIGTERM');
      fallbackCleanup=true;
    }
    await writeFile(join(data,'launcher-direct.log'),text);
    await writeFile(join(data,'fixture-result.json'),JSON.stringify({mode,instance,launcherPid:handle.pid,launcherExit:handle.exitCode,
      childPid:owned?.pid,childStillAlive:!!processIdentity(owned?.pid),backendHealthy:await health(),fallbackCleanup,
      state:await readJson(stateFile),lock:await readJson(lockFile),boundary:'Actual candidate launcher/backend, explicit rename fault injection; no paid model'},null,2));
    assert.equal(processIdentity(owned?.pid),null);assert.equal(await health(),false);
  }};
}

test('state publication transient EPERM recovers through the actual ready callback', {timeout:90000}, async()=>{
  const fixture=await stateFailureFixture('transient');
  try{
    const running=await until(async()=>{const state=await readJson(fixture.stateFile);return state?.phase==='running'?state:null;});
    assert.equal(running.instance,fixture.instance);assert.equal(await fixture.health(),true);
    const injected=(await readFile(join(fixture.data,'rename-injection.jsonl'),'utf8')).trim().split('\n').map(JSON.parse);
    assert.equal(injected.length,3);assert.ok(injected.every(entry=>entry.completeJSON));
    assert.match((await fixture.command('stop')).stdout,/Backend stopped/);
    assert.equal((await fixture.exited).code,0);
  }finally{await fixture.cleanup();}
});

test('state publication persistent EPERM exits visibly and cleans its owned backend and state', {timeout:90000}, async()=>{
  const fixture=await stateFailureFixture('persistent');
  try{
    const exit=await until(()=>fixture.handle.exitCode!==null?{code:fixture.handle.exitCode}:null,40000);
    assert.equal(exit.code,1);
    assert.equal(await readJson(fixture.stateFile),null);assert.equal(await readJson(fixture.lockFile),null);
    const owned=await readJson(join(fixture.data,'child-identity.json'));assert.equal(processIdentity(owned?.pid),null);
    assert.equal(await fixture.health(),false);
    const log=await readFile(join(fixture.data,'host/logs',`${new Date().toISOString().slice(0,10)}.log`),'utf8');
    assert.match(log,/State publication failed/);assert.ok(!log.includes('test-model-key'));
  }finally{await fixture.cleanup();}
});

for(const mode of ['initial','restarting'])test(`state publication failure at ${mode} ends its instance even without a live child`,{timeout:90000},async()=>{
  const fixture=await stateFailureFixture(mode);
  try{
    if(mode==='restarting'){
      const running=await until(async()=>{const state=await readJson(fixture.stateFile);return state?.phase==='running'?state:null;});
      const identity=processIdentity(running.childPid);
      assert.ok(identity.command.includes(join(root,'dist/server/index.js')));
      assert.deepEqual(processIdentity(running.childPid),identity,'Only terminate the exact captured fixture child');
      process.kill(running.childPid);
    }
    const exit=await until(()=>fixture.handle.exitCode!==null?{code:fixture.handle.exitCode}:null,40000);
    assert.equal(exit.code,1);assert.equal(await readJson(fixture.stateFile),null);assert.equal(await readJson(fixture.lockFile),null);
    assert.equal(await fixture.health(),false);
  }finally{await fixture.cleanup();}
});

for(const code of ['EPERM','EACCES','EBUSY'])test(`atomic state retries ${code} synchronously without partial JSON or stale phases`,async()=>{
  const data=await mkdtemp(join(root,'.tools/state-unit-'));const stateFile=join(data,'state.json');const instance=randomUUID();
  const starting={instance,phase:'starting',payload:'complete starting JSON'};
  publishHostState(stateFile,instance,starting);
  let attempts=0;const pauses=[];
  publishHostState(stateFile,instance,{instance,phase:'running'}, {pause:ms=>pauses.push(ms),rename:(from,to)=>{
    assert.deepEqual(JSON.parse(fs.readFileSync(to,'utf8')),starting);
    assert.equal(JSON.parse(fs.readFileSync(from,'utf8')).phase,'running');
    if(++attempts<=3){const error=new Error('Private body is not user feedback');error.code=code;throw error;}
    fs.renameSync(from,to);
  }});
  assert.equal(attempts,4);assert.deepEqual(pauses,[40,40,40]);
  publishHostState(stateFile,instance,{instance,phase:'stopping'});
  assert.equal((await readJson(stateFile)).phase,'stopping');
});

test('persistent rename retry is bounded and cleanup preserves foreign owner files',async()=>{
  const data=await mkdtemp(join(root,'.tools/state-unit-'));const stateFile=join(data,'state.json');const instance=randomUUID(),foreign=randomUUID();
  const foreignState={instance:foreign,phase:'running'};
  await writeFile(stateFile,JSON.stringify(foreignState));await writeFile(join(data,'lock.json'),JSON.stringify({instance:foreign}));
  await writeFile(join(data,`state.json.${foreign}.tmp`),'foreign temporary');
  await writeFile(join(data,`stop-${instance}.json`),JSON.stringify({instance}));
  let attempts=0;const pauses=[];
  assert.throws(()=>publishHostState(stateFile,instance,{instance,phase:'starting'},{pause:ms=>pauses.push(ms),rename:()=>{
    attempts++;const error=new Error('Injected permanent lock');error.code='EPERM';throw error;
  }}),{code:'EPERM'});
  assert.equal(attempts,6);assert.deepEqual(pauses,Array(5).fill(40));
  assert.deepEqual(clearInstanceState(data,instance),[]);
  assert.deepEqual(await readJson(stateFile),foreignState);assert.equal((await readJson(join(data,'lock.json'))).instance,foreign);
  assert.equal(await readFile(join(data,`state.json.${foreign}.tmp`),'utf8'),'foreign temporary');
  assert.equal(fs.existsSync(join(data,`state.json.${instance}.tmp`)),false);
  assert.equal(fs.existsSync(join(data,`stop-${instance}.json`)),false);
});
test('local launcher: start, duplicate start, crash recovery, graceful stop, unmanaged process', {timeout:120000}, async () => {
  const data = await mkdtemp(join(root,'.tools/host-test-'));
  const socket = createServer();
  await new Promise(resolve=>socket.listen(0,'127.0.0.1',resolve));
  const port = socket.address().port;
  await new Promise(resolve=>socket.close(resolve));
  const env = {...process.env,PORT:String(port),HOST:'127.0.0.1',DATA_DIR:data,
    AGENT_BUBBLE_TOKEN:'test-connection-token-with-32-characters',DEEPSEEK_API_KEY:'test-model-key',DASHSCOPE_API_KEY:'',
    ASR_ENABLED:'false',ARTICLE_BROWSER_ENABLED:'false',MEMORY_BASE_URL:'',MEMORY_BASE_TOKEN:''};
  const command = action => exec(process.execPath,[join(root,'scripts/local-backend.mjs'),action],{cwd:root,env,windowsHide:true,timeout:130000});
  const status = async () => JSON.parse((await command('status')).stdout);
  let external;
  try {
    assert.match((await command('start')).stdout,/started in the background/);
    const initial = await status();
    assert.equal(initial.backendHealthy,true);assert.equal(initial.launcher.phase,'running');
    assert.match((await command('start')).stdout,/already running/);
    assert.equal((await status()).launcher.pid,initial.launcher.pid);
    // This process belongs to the disposable test launcher, not the user's backend.
    process.kill(initial.launcher.childPid);
    let restarted;
    for(let n=0;n<40;n++) {
      await new Promise(resolve=>setTimeout(resolve,250));restarted=await status();
      if(restarted.backendHealthy && restarted.launcher?.phase==='running' && restarted.launcher.childPid!==initial.launcher.childPid)break;
    }
    assert.equal(restarted.backendHealthy,true);assert.notEqual(restarted.launcher.childPid,initial.launcher.childPid);
    assert.match((await command('stop')).stdout,/Backend stopped/);
    const stopped=await status();assert.equal(stopped.backendHealthy,false);assert.equal(stopped.launcher,null);
    const log = await readFile(join(data,'host/logs',`${new Date().toISOString().slice(0,10)}.log`),'utf8');
    assert.match(log,/Stop requested/);assert.match(log,/Backend exited: 0/);
    external=spawn(process.execPath,[join(root,'dist/server/index.js')],{cwd:root,env,windowsHide:true,stdio:['ignore','ignore','ignore','ipc']});
    await new Promise((resolve,reject)=>{external.once('message',resolve);external.once('error',reject);external.once('exit',code=>reject(new Error(`Standalone server exited ${code}`)));});
    assert.match((await command('start')).stdout,/outside this launcher/);
    assert.match((await command('stop')).stdout,/left alone/);
    assert.equal((await status()).backendHealthy,true);assert.equal((await status()).launcher,null);
  } finally {
    await command('stop');
    if(external?.connected) {
      const closed=new Promise(resolve=>external.once('exit',resolve));external.send({type:'shutdown'});await closed;
    }
  }
});
