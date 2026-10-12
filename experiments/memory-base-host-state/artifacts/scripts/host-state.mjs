import { readFileSync, renameSync, unlinkSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const transient=new Set(['EPERM','EACCES','EBUSY']);
const delay=new Int32Array(new SharedArrayBuffer(4));
const sleep=ms=>Atomics.wait(delay,0,0,ms);
const read=path=>{try{return JSON.parse(readFileSync(path,'utf8'));}catch{return null;}};
const validate=instance=>{if(!/^[a-f0-9-]{36}$/.test(instance))throw new Error('Invalid launcher instance');};
export const safeStateCode=error=>transient.has(error?.code)?error.code:'STATE_IO_ERROR';

function bounded(operation,{pause=sleep,attempts=6,delayMs=40}={}){
  for(let attempt=1;;attempt++){
    try{return operation();}
    catch(error){
      if(!transient.has(error.code)||attempt>=attempts)throw error;
      pause(delayMs);
    }
  }
}

// Synchronous retry preserves publication order. Readers see a complete old or
// new JSON file; writing the destination in place would expose partial states.
export function publishHostState(file,instance,value,{rename=renameSync,pause=sleep}={}){
  validate(instance);
  const temporary=`${file}.${instance}.tmp`;
  writeFileSync(temporary,JSON.stringify(value,null,2));
  bounded(()=>rename(temporary,file),{pause});
}

export function clearInstanceState(control,instance){
  validate(instance);
  const failures=[];
  const remove=(name)=>{
    try{bounded(()=>unlinkSync(join(control,name)));}
    catch(error){if(error.code!=='ENOENT')failures.push({file:name,code:safeStateCode(error)});}
  };
  remove(`state.json.${instance}.tmp`);
  if(read(join(control,`stop-${instance}.json`))?.instance===instance)remove(`stop-${instance}.json`);
  if(read(join(control,'state.json'))?.instance===instance)remove('state.json');
  // Release the lock last, after this instance has no more files to remove.
  if(read(join(control,'lock.json'))?.instance===instance)remove('lock.json');
  return failures;
}
