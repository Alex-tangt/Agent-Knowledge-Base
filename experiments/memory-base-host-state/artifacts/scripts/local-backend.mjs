import { fork, spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { appendFileSync, closeSync, existsSync, mkdirSync, openSync, readFileSync, unlinkSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { waitForBackend } from './backend-readiness.mjs';
import { memoryEnvironment } from './memory-environment.mjs';
import { clearInstanceState, publishHostState, safeStateCode } from './host-state.mjs';

const launcher = fileURLToPath(import.meta.url);
const root = resolve(dirname(launcher), '..');
process.chdir(root);
if (existsSync(join(root, '.env'))) process.loadEnvFile(join(root, '.env'));
const port = Number(process.env.PORT || 8787);
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('PORT must be between 1 and 65535');
const control = join(resolve(process.env.DATA_DIR || 'data'), 'host');
const logs = join(control, 'logs');
mkdirSync(logs, {recursive:true});
const stateFile = join(control, 'state.json');
const lockFile = join(control, 'lock.json');
const pause = ms => new Promise(r => setTimeout(r, ms));
const read = path => { try { return JSON.parse(readFileSync(path, 'utf8')); } catch { return null; } };
const alive = pid => { try { if (!Number.isInteger(pid) || pid < 1) return false; process.kill(pid, 0); return true; } catch { return false; } };
const managed = () => { const s = read(stateFile); const lock = read(lockFile); return s && s.instance === lock?.instance && alive(s.pid) ? s : null; };
const stopFile = instance => join(control, `stop-${instance}.json`);
const secrets = ['DEEPSEEK_API_KEY','DASHSCOPE_API_KEY','AGENT_BUBBLE_TOKEN','MEMORY_BASE_TOKEN'].map(key=>process.env[key]).filter(Boolean);
function safe(message) { let text = String(message); for (const secret of secrets) text = text.replaceAll(secret, '[redacted]'); return text; }
function log(message) { appendFileSync(join(logs, `${new Date().toISOString().slice(0,10)}.log`), `${new Date().toISOString()} ${safe(message)}\n`); }
async function healthy() {
  try {
    const response = await fetch(`http://127.0.0.1:${port}/api/health`, {headers:{Authorization:`Bearer ${process.env.AGENT_BUBBLE_TOKEN || ''}`},signal:AbortSignal.timeout(1500)});
    const body = await response.json(); return response.ok && body.status === 'ok';
  } catch { return false; }
}

async function daemon(instance) {
  if (!/^[a-f0-9-]{36}$/.test(instance || '')) throw new Error('Invalid launcher instance');
  let lock;
  try { lock = openSync(lockFile, 'wx'); }
  catch (error) {
    if (error.code !== 'EEXIST' || alive(read(lockFile)?.pid)) throw new Error('Another launcher is already running');
    unlinkSync(lockFile); lock = openSync(lockFile, 'wx');
  }
  writeFileSync(lock, JSON.stringify({pid:process.pid,instance})); closeSync(lock);
  let child, stopping = false, failed = false, exitCode = 0, retries = [], monitor, shutdownDeadline;
  const visible = message=>{try{log(message);}catch{console.error(safe(message));}};
  const cleanup = () => {
    clearInterval(monitor);clearTimeout(shutdownDeadline);
    for(const error of clearInstanceState(control,instance))visible(`Owned state cleanup failed (${error.code}).`);
  };
  const finish = () => {cleanup();process.exit(exitCode);};
  const closeChild = () => {
    if(child?.pid && child.exitCode===null && child.signalCode===null){
      if(child.connected)child.send({type:'shutdown'},()=>{});
      else child.kill();
      shutdownDeadline=setTimeout(()=>{visible('Shutdown deadline reached; ending the owned child process.');child.kill();},12000);
    }else finish();
  };
  const publicationFailed = error => {
    if(failed)return;
    failed=true;stopping=true;exitCode=1;clearInterval(monitor);
    visible(`State publication failed (${safeStateCode(error)}); stopping the owned backend.`);
    closeChild();
  };
  const save = (phase, extra={}) => {
    try{
      if(read(lockFile)?.instance!==instance)throw new Error('Launcher lock ownership changed');
      publishHostState(stateFile,instance,{pid:process.pid,instance,phase,childPid:child?.pid,port,startedAt:new Date().toISOString(),...extra});
      return true;
    }catch(error){publicationFailed(error);return false;}
  };
  const shutdown = () => {
    if (stopping) return;
    stopping = true;if(!save('stopping'))return;
    log('Stop requested; closing the backend and website sessions.');closeChild();
  };
  const launch = async () => {
    if (stopping) return;
    let childEnv={...process.env};
    try{childEnv=await memoryEnvironment(process.env);if(childEnv.MEMORY_BASE_TOKEN&&!secrets.includes(childEnv.MEMORY_BASE_TOKEN))secrets.push(childEnv.MEMORY_BASE_TOKEN);}
    catch{log('Knowledge-base credential lookup failed; summaries remain available locally and show a retryable save error.');}
    if(stopping)return;
    const launchedAt = Date.now();
    child = fork(join(root,'dist/server/index.js'), [], {cwd:root,execArgv:[],windowsHide:true,silent:true,env:childEnv});
    for (const stream of [child.stdout,child.stderr]) {
      let pending=''; stream.setEncoding('utf8');
      stream.on('data',data=>{pending+=data;const lines=pending.split('\n');pending=lines.pop();for(const line of lines)if(line.trim())log(line.trimEnd());});
      stream.on('end',()=>{if(pending)log(pending);});
    }
    child.on('message',message=>{if(message?.type==='ready' && !stopping && save('running'))log(`Backend ready after ${Date.now()-launchedAt} ms.`);});
    child.on('error',error=>log(`Backend process error: ${error.message}`));
    child.once('exit',(code,signal)=>{
      log(`Backend exited: ${code ?? signal}.`);
      if (stopping) {finish();return;}
      retries = retries.filter(time=>Date.now()-time<600000); retries.push(Date.now());
      if (retries.length>5) {if(save('failed'))log('Restart limit reached. Inspect logs, then stop and start the backend.');return;}
      if(save('restarting'))setTimeout(launch, Math.min(15000, 1000*2**(retries.length-1)));
    });
    if(save('starting'))log(`Starting backend; process ${child.pid}.`);
  };
  if(!save('starting'))return;
  monitor = setInterval(()=>{if(read(stopFile(instance))?.instance===instance)shutdown();},500);
  process.once('SIGINT',shutdown);process.once('SIGTERM',shutdown);
  launch();
}

const action = process.argv[2] || 'status';
const waiting = (elapsed,phase) => console.log(`Waiting for backend: ${phase || 'starting'} (${Math.round(elapsed/1000)} seconds)...`);
try {
  if (action==='run') await daemon(process.argv[3]);
  else if (action==='start') {
    if (!existsSync(join(root,'dist/server/index.js'))) throw new Error('Run npm run build first');
    const previous = managed();
    if (previous) {
      if (previous.phase==='failed') throw new Error('Backend restart limit reached. Check logs, then stop and start again.');
      const ready = await waitForBackend({inspect:managed,healthy,pause,onWaiting:waiting});
      if (ready!=='ready') throw new Error('Backend is not ready yet. The background launcher remains active; check status and logs.');
      console.log(`Launcher already running; backend healthy at http://127.0.0.1:${port}; PID ${previous.pid}.`);
    } else if (await healthy()) {
      console.log('Backend is already healthy, started outside this launcher. No duplicate process was started.');
    } else {
      const instance=randomUUID();
      const output=openSync(join(logs,'launcher.log'),'a');
      const processHandle=spawn(process.execPath,[launcher,'run',instance],{cwd:root,detached:true,windowsHide:true,stdio:['ignore',output,output],env:process.env});
      processHandle.on('error',()=>{});processHandle.unref();closeSync(output);
      const ready = await waitForBackend({inspect:()=>{const state=managed();return state?.instance===instance ? state : processHandle.exitCode!==null?{phase:'failed'}:null;},healthy,pause,onWaiting:waiting});
      if(ready !== 'ready')throw new Error(processHandle.exitCode!==null?'Background launcher exited before becoming ready; check host logs.':'Backend is not ready yet. The background launcher remains active; check status and logs.');
      console.log(`Backend started in the background: http://127.0.0.1:${port}`);
    }
  } else if (action==='stop') {
    const current=managed();
    if (!current) console.log('No managed launcher is running. A separately started backend is left alone.');
    else {
      writeFileSync(stopFile(current.instance),JSON.stringify({instance:current.instance}));
      for(let n=0;n<70 && managed()?.instance===current.instance;n++)await pause(250);
      if(managed()?.instance===current.instance)throw new Error('Stop has not completed. Inspect host logs.');
      console.log('Backend stopped. Saved conversations and website sessions are retained.');
    }
  } else if (action==='status') {
    const current=managed();console.log(JSON.stringify({backendHealthy:await healthy(),launcher:current ? {pid:current.pid,childPid:current.childPid,phase:current.phase}:null,localUrl:`http://127.0.0.1:${port}`,logs},null,2));
  } else if (action==='logs') {
    const path=join(logs,`${new Date().toISOString().slice(0,10)}.log`);
    console.log(existsSync(path)?safe(readFileSync(path,'utf8').trim().split('\n').slice(-50).join('\n')):'No backend log for today.');
  } else throw new Error('Usage: node scripts/local-backend.mjs start|stop|status|logs');
} catch (error) {console.error(safe(error.message));process.exitCode=1;}
