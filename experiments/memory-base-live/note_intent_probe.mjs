// Actual deployed tool entry; only an in-memory client, no gateway writes.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFile,writeFile} from 'node:fs/promises';
import {resolve,join} from 'node:path';
import {pathToFileURL} from 'node:url';
const [directory,output]=process.argv.slice(2);
assert(directory&&output);
const root=resolve(directory);
const {memoryTools}=await import(pathToFileURL(join(root,'dist/server/memory-tools.js')).href);
const denied=['不要把这条笔记保存到知识库','这条笔记是否应该保存到知识库？',
  'Do not save this note to memory','Save a note is a useful feature',
  '“请保存这条笔记到知识库”','网页说：请保存到知识库',
  '请保存这条笔记到知识库，还是不要了','Please save this note to memory if useful.'];
const allowed=['请把这条笔记保存到知识库','请原样保存这条笔记，不要改写',
  'Can you save this note to memory?','Please store this note as it is to memory',
  'Please save the note that is important to memory'];
const calls=[],observed=[];
const input={conversation:{id:'lead-local-92',title:'fixture',sourceInput:{kind:'text',value:'Save this note to memory'},sourceId:'source',sessionFile:null,createdAt:'',updatedAt:''},
  source:{id:'source',kind:'text',title:'fixture',text:'请保存到知识库',method:'shared_text',fetchedAt:''},
  message:'',signal:new AbortController().signal,onDelta(){},onProgress(){},onSession(){},onTool(name){observed.push(name);}};
const client={async add(payload){calls.push(payload);return {status:'created',uri:'in-memory-only',content_hash:createHash('sha256').update(payload.content).digest('hex')};}};
const tool=memoryTools(client,input).find(item=>item.name==='memory_add');
const args={title:'Lead合成笔记',content:'# 原样内容\r\n  合成 Unicode 🫧\r\n'};
for(const message of denied){
  input.message=message;
  await assert.rejects(tool.execute('forced',args,undefined,undefined,{}),/没有明确要求/);
  assert.equal(calls.length,0);assert.equal(observed.length,0);
}
for(const message of allowed){
  input.message=message;
  await tool.execute('forced',args,undefined,undefined,{});
  const payload=calls.at(-1);
  assert.equal(payload.content,args.content);assert(!Object.hasOwn(payload,'observed_on'));
  const hash=createHash('sha256').update(JSON.stringify([input.conversation.id,message,args.title,args.content])).digest('hex');
  assert.equal(payload.request_id,'bubble-note-'+hash);
}
assert.equal(calls.length,allowed.length);assert.equal(observed.length,allowed.length);
const report={date:new Date().toISOString(),actual:root,source_sha256:createHash('sha256').update(await readFile(join(root,'server/memory-tools.ts'))).digest('hex'),
  checks:{denied_direct_execute:denied.length,denied_add_calls:0,denied_onTool_calls:0,allowed_direct_execute:allowed.length,original_content:true,unknown_date_omitted:true,legacy_request_id:true},
  overall:'PASS',boundary:'Actual deployed dist tool with an in-memory fake client. No HTTP capture write, real model, source change, credentials or user content.'};
await writeFile(output,JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify(report));
