// Read-only Lead acceptance: real Bubble pi SDK and real gateway, local synthetic model.
import assert from 'node:assert/strict';
import {createHash,randomUUID} from 'node:crypto';
import {createServer} from 'node:http';
import {mkdtemp,readFile,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {pathToFileURL} from 'node:url';

const [bubbleDir,home,python,output]=process.argv.slice(2);
assert(bubbleDir&&home&&python&&output,'Pass Bubble directory, home, Python and report path');
const reportFile=resolve(output);
const root=resolve(bubbleDir);
process.chdir(root);
const {MemoryBaseClient,MemoryBaseError}=await import(pathToFileURL(join(root,'dist/server/memory-base.js')).href);
const {PiAgent}=await import(pathToFileURL(join(root,'dist/server/agent.js')).href);
const {loadConfig}=await import(pathToFileURL(join(root,'dist/server/config.js')).href);
const deployment=JSON.parse(await readFile(join(home,'deployment.json'),'utf8'));
const memoryBase={baseUrl:`http://127.0.0.1:${deployment.ports.gateway}`,home,pythonCommand:python};
const client=new MemoryBaseClient(memoryBase);
const uri='projects/agent-kb/README.md';
const scope='projects/agent-kb';
const original=await client.get(uri);
const reference=await readFile(resolve(deployment.source,'README.md'));
const revision=createHash('sha256').update(reference).digest('hex');
assert.equal(original.revision,revision);
assert.equal(original.content,reference.toString('utf8'));
const first=await client.list({scope,limit:1});
assert(first.next_cursor,'Expected a real project second page');
assert.equal(first.completeness.enumeration,'complete');
const plan=[
  {name:'memory_search',args:{query:'正式个人记忆基座',scope}},
  {name:'memory_list',args:{scope,limit:1}},
  {name:'memory_list',args:{scope,limit:1,cursor:first.next_cursor}},
  {name:'memory_get',args:{uri,scope,expected_revision:'0'.repeat(64)}},
  {name:'memory_get',args:{uri,scope,expected_revision:revision}},
  {name:'memory_list',args:{scope,limit:1,cursor:'0'.repeat(64)+':1'}},
];
const observed=[];let requests=0;let modelFailure;
const model=createServer(async(request,response)=>{
  try{
    const chunks=[];for await(const chunk of request)chunks.push(chunk);
    const body=JSON.parse(Buffer.concat(chunks).toString());
    assert.deepEqual(body.tools.map(tool=>tool.function.name).sort(),['memory_add','memory_get','memory_list','memory_search']);
    assert(body.tools.every(tool=>tool.function.parameters.additionalProperties===false));
    const step=requests++;
    response.writeHead(200,{'content-type':'text/event-stream'});
    const chunk=(delta,finish_reason=null)=>({id:'chatcmpl-local-probe',object:'chat.completion.chunk',created:1,
      model:'deepseek-flash',choices:[{index:0,delta,finish_reason}]});
    if(step<plan.length){
      const tool=plan[step];
      response.write('data: '+JSON.stringify(chunk({role:'assistant',tool_calls:[{index:0,id:'local-'+step,type:'function',function:{name:tool.name,arguments:JSON.stringify(tool.args)}}]}))+'\n\n');
      response.write('data: '+JSON.stringify(chunk({},'tool_calls'))+'\n\n');
    }else{
      const messages=body.messages.filter(message=>message.role==='tool');
      assert(messages.some(message=>message.content.includes(revision)&&message.content.includes('README.md')));
      assert(messages.some(message=>message.content.includes('REVISION_MISMATCH')));
      assert(messages.some(message=>message.content.includes('LIST_CHANGED_RESTART')));
      assert(messages.some(message=>message.content.includes('project_lexical')));
      assert(messages.some(message=>message.content.includes('next_cursor')));
      response.write('data: '+JSON.stringify(chunk({role:'assistant',content:'已完成原文、范围与版本核验。'}))+'\n\n');
      response.write('data: '+JSON.stringify(chunk({},'stop'))+'\n\n');
    }
    response.end('data: [DONE]\n\n');
  }catch(error){modelFailure=error;response.writeHead(500);response.end('Local probe assertion failed');}
});
await new Promise(done=>model.listen(0,'127.0.0.1',done));
const directory=await mkdtemp(join(tmpdir(),'agent-kb-bubble-bridge-lead-'));
const report={date:new Date().toISOString(),bubble_directory:root,source_hashes:{},checks:{},
  boundary:'Real Bubble pi SDK and formal gateway, read-only project/capture calls. Local synthetic SSE model; no paid model, new summary, capture write, source change or service restart.'};
try{
  for(const file of ['server/memory-base.ts','server/memory-tools.ts','server/agent.ts','server/tests/memory-base.test.ts'])
    report.source_hashes[file]=createHash('sha256').update(await readFile(join(root,file))).digest('hex');
  const config={...loadConfig({AGENT_BUBBLE_TOKEN:'local-probe-no-real-bubble-token',DEEPSEEK_API_KEY:'local-model-fixture-key',DATA_DIR:directory}),
    memoryBase,baseUrl:`http://127.0.0.1:${model.address().port}/v1`};
  const now=new Date().toISOString();
  const result=await new PiAgent(config).run({conversation:{id:randomUUID(),title:'Lead只读接入核验',sourceInput:{kind:'text',value:'合成材料'},
    sourceId:'local-fixture',sessionFile:null,createdAt:now,updatedAt:now},source:{id:'local-fixture',kind:'text',title:'合成来源',text:'只读项目接入核验',method:'shared_text',fetchedAt:now},
    message:'请只读查询并核对项目原文和版本，不保存笔记。',signal:AbortSignal.timeout(90000),onDelta(){},onProgress(){},onSession(){},onTool(name){observed.push(name);}});
  if(modelFailure)throw modelFailure;
  assert.equal(result.text,'已完成原文、范围与版本核验。');
  assert.deepEqual(observed,plan.map(step=>step.name));
  assert.equal(requests,plan.length+1);
  report.checks={real_four_closed_tools_schema:true,project_lexical_search:true,real_list_two_pages:true,expected_revision_matches_local_file:true,
    stale_revision_actionable_in_pi:true,stale_cursor_actionable_in_pi:true,no_add_invoked:true};
  report.project_revision=revision;report.tool_calls=observed;report.model_requests=requests;
  // An old capture read additionally proves the bridge retains its URI/body contract.
  // Optional URI is supplied from a receipt, never guessed from application text.
  if(process.env.MEMORY_BRIDGE_CAPTURE_URI){
    const saved=await client.get(process.env.MEMORY_BRIDGE_CAPTURE_URI);
    assert(saved.revision&&saved.content_hash===saved.revision);
    report.checks.legacy_capture_read_hash=true;report.capture_uri=saved.uri;report.capture_revision=saved.revision;
  }
  report.overall='PASS';
}catch(error){
  report.overall='FAIL';report.failure={name:error.name,code:error instanceof MemoryBaseError?error.code:undefined,message:error.message};
  process.exitCode=1;
}finally{
  model.closeAllConnections();await new Promise(done=>model.close(done));
  await writeFile(reportFile,JSON.stringify(report,null,2)+'\n');
  console.log(JSON.stringify({overall:report.overall,checks:report.checks,report:reportFile}));
}
