#!/usr/bin/env node
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import { spawn } from 'node:child_process';

const REQUIRED = ['request_id','story_id','notebook_url','artifact_title','output_path','allow_root'];
const ALLOWED = new Set(['schema_version',...REQUIRED,'receipt_path','expected_format','expected_duration_seconds','cdp_url','ffprobe_bin','timestamp']);
const fail = (message) => { throw new Error(message); };
const inside = (child, root) => { const rel=path.relative(path.resolve(root),path.resolve(child)); return rel !== '..' && !rel.startsWith(`..${path.sep}`) && !path.isAbsolute(rel); };
async function atomicJson(file, value) { await fs.mkdir(path.dirname(file),{recursive:true}); const tmp=`${file}.${process.pid}.tmp`; await fs.writeFile(tmp,JSON.stringify(value,null,2)+'\n'); await fs.rename(tmp,file); }
function run(bin,args){return new Promise((resolve,reject)=>{const p=spawn(bin,args);let out='',err='';p.stdout.on('data',d=>out+=d);p.stderr.on('data',d=>err+=d);p.on('error',reject);p.on('close',c=>c===0?resolve(out):reject(new Error(`${bin} failed rc=${c}: ${err.trim()}`)));});}
async function sha256(file){const b=await fs.readFile(file);return crypto.createHash('sha256').update(b).digest('hex');}
async function probe(file,bin='ffprobe'){
 const raw=await run(bin,['-v','error','-print_format','json','-show_format','-show_streams',file]); const d=JSON.parse(raw); const streams=d.streams||[]; const video=streams.find(s=>s.codec_type==='video'); const audio=streams.find(s=>s.codec_type==='audio');
 if(!video) fail('downloaded artifact has no video stream'); const stat=await fs.stat(file); if(stat.size<1024) fail('downloaded artifact is unexpectedly small');
 return {size_bytes:stat.size,container:String(d.format?.format_name||''),duration_seconds:Number(d.format?.duration||video.duration||0),dimensions:{width:Number(video.width),height:Number(video.height)},codecs:{video:String(video.codec_name||''),audio:audio?String(audio.codec_name||''):null},sha256:await sha256(file)};
}
function validate(req){for(const k of REQUIRED)if(!req[k])fail(`missing required request key: ${k}`);const extra=Object.keys(req).filter(k=>!ALLOWED.has(k));if(extra.length)fail(`unsupported request keys: ${extra.sort().join(', ')}`);if(!String(req.notebook_url).startsWith('https://notebook.google.com/'))fail('notebook_url must be a NotebookLM URL');if(!inside(req.output_path,req.allow_root))fail('output_path is outside configured allow_root');if(req.receipt_path&&!inside(req.receipt_path,req.allow_root))fail('receipt_path is outside configured allow_root');}
function verifyExpected(a,req){if(req.expected_format&&!a.container.toLowerCase().includes(String(req.expected_format).toLowerCase()))fail(`container ${a.container} does not match expected format ${req.expected_format}`);if(req.expected_duration_seconds!=null&&Math.abs(a.duration_seconds-Number(req.expected_duration_seconds))>2)fail(`duration ${a.duration_seconds} differs from expected ${req.expected_duration_seconds}`);}
async function main(){
 const requestFile=process.argv[2]; if(!requestFile)fail('usage: hp-local-download-worker.mjs REQUEST.json'); const req=JSON.parse(await fs.readFile(requestFile,'utf8')); validate(req); const receipt=req.receipt_path||`${requestFile}.receipt.json`; await fs.mkdir(path.dirname(req.output_path),{recursive:true});
 try { const a=await probe(req.output_path,req.ffprobe_bin); verifyExpected(a,req); const r={schema_version:1,request_id:req.request_id,story_id:req.story_id,status:'done',output_path:req.output_path,timestamp:new Date().toISOString(),artifact:a,evidence:{idempotent_existing:true,artifact_title:req.artifact_title,local_worker:true}};await atomicJson(receipt,r);console.log(JSON.stringify(r));return; } catch(e) { if(e?.code!=='ENOENT'&&!String(e.message).includes('No such file')) { try{await fs.unlink(req.output_path);}catch{} } }
 const { chromium }=await import('playwright-core'); const browser=await chromium.connectOverCDP(req.cdp_url||'http://127.0.0.1:9222');
 let lastError; try { const context=browser.contexts()[0]; if(!context)fail('authenticated Chrome context not found'); let page=context.pages().find(p=>p.url()===req.notebook_url||p.url().includes(new URL(req.notebook_url).pathname)); if(!page)page=await context.newPage();
  for(let attempt=1;attempt<=2;attempt++){try{await page.goto(req.notebook_url,{waitUntil:'domcontentloaded',timeout:60000});const artifact=page.getByRole('button',{name:req.artifact_title,exact:false}).first();await artifact.waitFor({state:'visible',timeout:60000});await artifact.click();const dl=page.getByRole('button',{name:/^Download$/i}).first();await dl.waitFor({state:'visible',timeout:30000});const [download]=await Promise.all([page.waitForEvent('download',{timeout:120000}),dl.click()]);await download.saveAs(req.output_path);const failure=await download.failure();if(failure)fail(`download failed: ${failure}`);const a=await probe(req.output_path,req.ffprobe_bin);verifyExpected(a,req);const r={schema_version:1,request_id:req.request_id,story_id:req.story_id,status:'done',output_path:req.output_path,timestamp:new Date().toISOString(),artifact:a,evidence:{idempotent_existing:false,artifact_title:req.artifact_title,suggested_filename:download.suggestedFilename(),selector_attempts:attempt,download_failure:null,local_worker:true}};await atomicJson(receipt,r);console.log(JSON.stringify(r));return;}catch(e){lastError=e;if(attempt===1)await page.reload({waitUntil:'domcontentloaded',timeout:60000}).catch(()=>{});}}
  throw lastError;
 } finally { await browser.close(); }
}
main().catch(async e=>{console.error(e.message||String(e));process.exitCode=1;});
