#!/usr/bin/env node
import fs from 'node:fs/promises';
import { constants as fsConstants, createReadStream } from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { spawn } from 'node:child_process';

import { fileURLToPath } from 'node:url';

const REQUIRED = ['request_id','story_id','notebook_url','artifact_title','output_path','receipt_path','allow_root'];
const ALLOWED = new Set(['schema_version',...REQUIRED,'request_token','expected_format','expected_container','expected_duration_seconds','cdp_url','ffprobe_bin','timestamp']);
const fail = (message) => { throw new Error(message); };
const inside = (child, root) => { const rel=path.relative(root, child); return rel === '' || (rel !== '..' && !rel.startsWith(`..${path.sep}`) && !path.isAbsolute(rel)); };
async function safePath(candidate, root, field) {
 if(typeof candidate!=='string'||!path.isAbsolute(candidate)) fail(`${field} must be an absolute path`);
 if(typeof root!=='string'||!path.isAbsolute(root)) fail('allow_root must be an absolute path');
 const lexical=path.resolve(candidate); const lexicalRoot=path.resolve(root);
 // Reject lexical escapes before creating anything supplied by the request.
 if(!inside(lexical,lexicalRoot)) fail(`${field} resolves outside configured allow_root`);
 await fs.mkdir(lexicalRoot,{recursive:true});
 if((await fs.lstat(lexicalRoot)).isSymbolicLink()) fail('allow_root must not be a symlink');
 const realRoot=await fs.realpath(lexicalRoot);
 const relativeParent=path.relative(lexicalRoot,path.dirname(lexical));
 let current=lexicalRoot;
 for(const component of relativeParent.split(path.sep).filter(Boolean)){
  current=path.join(current,component);
  try {
   const stat=await fs.lstat(current);
   if(stat.isSymbolicLink()) fail(`${field} parent must not contain symlinks`);
   if(!stat.isDirectory()) fail(`${field} parent must be a directory`);
  } catch(e) {
   if(e?.code!=='ENOENT') throw e;
   await fs.mkdir(current);
  }
  if(!inside(await fs.realpath(current),realRoot)) fail(`${field} resolves outside configured allow_root`);
 }
 const realParent=await fs.realpath(path.dirname(lexical));
 try { if((await fs.lstat(lexical)).isSymbolicLink()) fail(`${field} must not be a symlink`); } catch(e) { if(e?.code!=='ENOENT') throw e; }
 return path.join(realParent,path.basename(lexical));
}
export async function atomicJson(file, value, allowRoot) {
 const validated=await safePath(file,allowRoot,'receipt_path');
 const dir=path.dirname(validated);
 const dirHandle=await fs.open(dir,fsConstants.O_RDONLY|fsConstants.O_DIRECTORY|fsConstants.O_NOFOLLOW);
 const pinnedDir=`/proc/self/fd/${dirHandle.fd}`;
 const realRoot=await fs.realpath(path.resolve(allowRoot));
 const pinnedRealDir=await fs.realpath(pinnedDir);
 if(!inside(pinnedRealDir,realRoot)){await dirHandle.close();fail('receipt_path parent moved outside configured allow_root');}
 const pinnedFile=path.join(pinnedDir,path.basename(validated));
 try { if((await fs.lstat(pinnedFile)).isSymbolicLink())fail('receipt_path must not be a symlink'); }
 catch(e) { if(e?.code!=='ENOENT'){await dirHandle.close();throw e;} }
 const tmp=path.join(pinnedDir,`.${path.basename(file)}.${process.pid}.${Date.now()}.tmp`); let handle;
 try { handle=await fs.open(tmp,'wx',0o600); await handle.writeFile(JSON.stringify(value,null,2)+'\n'); await handle.sync(); await handle.close(); handle=undefined; await fs.link(tmp,pinnedFile); await fs.unlink(tmp); await dirHandle.sync(); }
 finally { await handle?.close().catch(()=>{}); await fs.unlink(tmp).catch(()=>{}); await dirHandle.close().catch(()=>{}); }
}
export async function publishVerifiedDownload(temporary, destination, allowRoot) {
 const validated=await safePath(destination,allowRoot,'output_path');
 const dir=path.dirname(validated);
 const dirHandle=await fs.open(dir,fsConstants.O_RDONLY|fsConstants.O_DIRECTORY|fsConstants.O_NOFOLLOW);
 const pinnedDir=`/proc/self/fd/${dirHandle.fd}`;
 try {
  const realRoot=await fs.realpath(path.resolve(allowRoot));
  const pinnedRealDir=await fs.realpath(pinnedDir);
  if(!inside(pinnedRealDir,realRoot))fail('output_path parent moved outside configured allow_root');
  const pinnedDestination=path.join(pinnedDir,path.basename(validated));
  try { if((await fs.lstat(pinnedDestination)).isSymbolicLink())fail('output_path must not be a symlink'); }
  catch(e) { if(e?.code!=='ENOENT')throw e; }
  const handle=await fs.open(temporary,'r');
  try { await handle.sync(); } finally { await handle.close(); }
  // link() publishes through the pinned directory without replacing a concurrent artifact.
  await fs.link(temporary,pinnedDestination);
  await fs.unlink(temporary);
  await dirHandle.sync();
 } finally { await dirHandle.close().catch(()=>{}); }
}
function run(bin,args){return new Promise((resolve,reject)=>{const p=spawn(bin,args);let out='',err='';p.stdout.on('data',d=>out+=d);p.stderr.on('data',d=>err+=d);p.on('error',reject);p.on('close',c=>c===0?resolve(out):reject(new Error(`${bin} failed rc=${c}: ${err.trim()}`)));});}
export async function sha256(file){
 const hash=crypto.createHash('sha256');
 for await(const chunk of createReadStream(file))hash.update(chunk);
 return hash.digest('hex');
}
export async function openPinnedArtifact(file){
 const handle=await fs.open(file,fsConstants.O_RDONLY|fsConstants.O_NOFOLLOW);
 try {
  const opened=await handle.stat({bigint:true});
  if(!opened.isFile())fail('existing output must be a regular file');
  return {handle,stat:opened,path:`/proc/${process.pid}/fd/${handle.fd}`};
 } catch(e) { await handle.close().catch(()=>{}); throw e; }
}
export async function openPinnedArtifactIfPresent(file){
 try { return await openPinnedArtifact(file); }
 catch(e) { if(e?.code==='ENOENT')return null; throw e; }
}
function sameOpenedFile(left,right){
 return left.dev===right.dev&&left.ino===right.ino&&left.size===right.size&&left.mtimeNs===right.mtimeNs&&left.ctimeNs===right.ctimeNs;
}
export async function verifyPinnedPathUnchanged(file,pinned,artifact){
 let before;
 try { before=await fs.lstat(file,{bigint:true}); }
 catch(e) { if(e?.code==='ENOENT')fail('existing output changed during verification'); throw e; }
 const openedBefore=await pinned.handle.stat({bigint:true});
 if(before.isSymbolicLink()||!before.isFile()||!sameOpenedFile(before,openedBefore))fail('existing output changed during verification');
 if(await sha256(pinned.path)!==artifact.sha256)fail('existing output changed during verification');
 const after=await fs.lstat(file,{bigint:true});
 const openedAfter=await pinned.handle.stat({bigint:true});
 if(!sameOpenedFile(before,after)||!sameOpenedFile(after,openedAfter))fail('existing output changed during verification');
}
async function probe(file,bin='ffprobe'){
 const raw=await run(bin,['-v','error','-print_format','json','-show_format','-show_streams',file]); const d=JSON.parse(raw); const streams=d.streams||[]; const video=streams.find(s=>s.codec_type==='video'); const audio=streams.find(s=>s.codec_type==='audio');
 if(!video) fail('downloaded artifact has no video stream'); const stat=await fs.stat(file); if(stat.size<1024) fail('downloaded artifact is unexpectedly small');
 return {size_bytes:stat.size,container:String(d.format?.format_name||''),duration_seconds:Number(d.format?.duration||video.duration||0),dimensions:{width:Number(video.width),height:Number(video.height)},codecs:{video:String(video.codec_name||''),audio:audio?String(audio.codec_name||''):null},sha256:await sha256(file)};
}
export function validateNotebookUrl(value) {
 if(typeof value!=='string'||!value.trim()) fail('notebook_url must be a NotebookLM URL');
 let url;
 try { url = new URL(value); } catch { fail('notebook_url must be a NotebookLM URL'); }
 const authority=value.match(/^https:\/\/([^/]+)/)?.[1];
 if(url.protocol!=='https:'||url.hostname!=='notebook.google.com'||authority!=='notebook.google.com'||url.search) fail('notebook_url must be a NotebookLM URL');
 if(!/^\/notebook\/[^/]+\/?$/.test(url.pathname)) fail('notebook_url must be a NotebookLM URL');
 if(url.username||url.password||url.hash) fail('notebook_url must be a NotebookLM URL');
 return url.toString();
}
export function validateCdpUrl(value) {
 if(typeof value!=='string'||!value.trim()||value.includes('\\')||[...value].some(character=>/\s/.test(character)||character.charCodeAt(0)<32))fail('cdp_url must be an HTTP(S) URL without credentials');
 let url;
 try { url=new URL(value); } catch { fail('cdp_url must be an HTTP(S) URL without credentials'); }
 const authority=value.match(/^https?:\/\/([^/?#]+)/)?.[1];
 if(!['http:','https:'].includes(url.protocol)||!authority||!url.hostname||url.username||url.password)fail('cdp_url must be an HTTP(S) URL without credentials');
 return value;
}
export function validateUtcTimestamp(value, field='timestamp'){
 if(typeof value!=='string'||!value.trim())fail(`${field} must be an ISO-8601 UTC timestamp`);
 const parsed=new Date(value);
 if(!Number.isFinite(parsed.getTime())||!/(?:Z|[+-]00:00)$/i.test(value))fail(`${field} must be an ISO-8601 UTC timestamp`);
 return value;
}
export async function validate(raw){
 if(!raw||typeof raw!=='object'||Array.isArray(raw))fail('request must be a JSON object');
 if(raw.schema_version!==undefined&&raw.schema_version!==1)fail('schema_version must be 1');
 for(const k of REQUIRED){
   if(typeof raw[k]!=='string'||!raw[k].trim())fail(`${k} must be a non-empty string`);
 }
 if(raw.request_token!==undefined&&(typeof raw.request_token!=='string'||!raw.request_token.trim()))fail('request_token must be a non-empty string');
 const extra=Object.keys(raw).filter(k=>!ALLOWED.has(k));if(extra.length)fail(`unsupported request keys: ${extra.sort().join(', ')}`);
 const normalized={...raw,notebook_url:validateNotebookUrl(raw.notebook_url)};
 if(raw.cdp_url!==undefined)normalized.cdp_url=validateCdpUrl(raw.cdp_url);
 if(raw.expected_format!==undefined&&raw.expected_format!=='Short')fail('expected_format must be Short');
 if(raw.expected_container!==undefined&&(typeof raw.expected_container!=='string'||!raw.expected_container.trim()))fail('expected_container must be a non-empty media container');
 if(raw.expected_duration_seconds!==undefined&&(typeof raw.expected_duration_seconds!=='number'||!Number.isFinite(raw.expected_duration_seconds)||raw.expected_duration_seconds<=0))fail('expected_duration_seconds must be a positive number');
 if(raw.ffprobe_bin!==undefined&&(typeof raw.ffprobe_bin!=='string'||!raw.ffprobe_bin.trim()))fail('ffprobe_bin must be a non-empty string');
 if(raw.timestamp!==undefined)validateUtcTimestamp(raw.timestamp);
 normalized.output_path=await safePath(raw.output_path,raw.allow_root,'output_path');
 normalized.receipt_path=await safePath(raw.receipt_path,raw.allow_root,'receipt_path');
 return normalized;
}
function verifyExpected(a,req){
 // expected_format names the queued NotebookLM overview, not a media container.
 if(req.expected_container&&!a.container.toLowerCase().split(',').map(x=>x.trim()).includes(String(req.expected_container).toLowerCase())) fail(`container ${a.container} does not include expected container ${req.expected_container}`);
 if(req.expected_duration_seconds!=null&&Math.abs(a.duration_seconds-Number(req.expected_duration_seconds))>2)fail(`duration ${a.duration_seconds} differs from expected ${req.expected_duration_seconds}`);
}
function normalizedContainer(value){return String(value||'').split(',').map(x=>x.trim().toLowerCase()).filter(Boolean).sort().join(',');}
export function verifyExistingReceipt(receipt,req,artifact){
 if(!receipt||receipt.schema_version!==1||receipt.status!=='done')fail('existing output requires a valid completed receipt');
 validateUtcTimestamp(receipt.timestamp,'existing receipt timestamp');
 for(const key of ['request_id','story_id','notebook_url','output_path']){
  if(receipt[key]!==req[key])fail(`existing receipt ${key} does not match request`);
 }
 if(receipt.allow_root!==req.allow_root)fail('existing receipt allow_root does not match request');
 if(req.request_token&&receipt.request_token!==req.request_token)fail('existing receipt request_token does not match request');
 if(req.expected_format&&receipt.video_format!==req.expected_format)fail('existing receipt video_format does not match request');
 if(receipt.evidence?.artifact_title!==req.artifact_title)fail('existing receipt artifact_title does not match request');
 const recorded=receipt.artifact||{};
 for(const key of ['size_bytes','sha256'])if(recorded[key]!==artifact[key])fail(`existing receipt artifact ${key} does not match output`);
 if(normalizedContainer(recorded.container)!==normalizedContainer(artifact.container))fail('existing receipt artifact container does not match output');
 if(Math.abs(Number(recorded.duration_seconds)-artifact.duration_seconds)>0.01)fail('existing receipt artifact duration does not match output');
 if(JSON.stringify(recorded.dimensions)!==JSON.stringify(artifact.dimensions))fail('existing receipt artifact dimensions do not match output');
 if(JSON.stringify(recorded.codecs)!==JSON.stringify(artifact.codecs))fail('existing receipt artifact codecs do not match output');
}
const sleep=(milliseconds)=>new Promise(resolve=>setTimeout(resolve,milliseconds));
export async function verifyExistingArtifact(req,{receiptAttempts=1,receiptDelayMs=50}={}){
 const pinned=await openPinnedArtifactIfPresent(req.output_path);
 if(!pinned)return null;
 try {
  const artifact=await probe(pinned.path,req.ffprobe_bin);
  verifyExpected(artifact,req);
  let priorReceipt;
  for(let attempt=1;attempt<=receiptAttempts;attempt++){
   try { priorReceipt=JSON.parse(await fs.readFile(req.receipt_path,'utf8')); break; }
   catch(e) {
    if(e?.code!=='ENOENT')throw e;
    if(attempt===receiptAttempts)fail('existing output requires a matching receipt');
    await sleep(receiptDelayMs);
   }
  }
  verifyExistingReceipt(priorReceipt,req,artifact);
  await verifyPinnedPathUnchanged(req.output_path,pinned,artifact);
  return {
   ...priorReceipt,
   timestamp:new Date().toISOString(),
   artifact,
   evidence:{...priorReceipt.evidence,idempotent_existing:true,artifact_title:req.artifact_title,local_worker:true},
  };
 } finally { await pinned.handle.close().catch(()=>{}); }
}
async function main(){
 const requestFile=process.argv[2]; if(!requestFile)fail('usage: hp-local-download-worker.mjs REQUEST.json');
 const req=await validate(JSON.parse(await fs.readFile(requestFile,'utf8')));
 const receipt=req.receipt_path;
 const existing=await verifyExistingArtifact(req);
 if(existing){console.log(JSON.stringify(existing));return;}
 const { chromium }=await import('playwright-core'); const browser=await chromium.connectOverCDP(req.cdp_url||'http://127.0.0.1:9222');
 let lastError; try { const context=browser.contexts()[0]; if(!context)fail('authenticated Chrome context not found'); let page=context.pages().find(p=>p.url()===req.notebook_url||p.url().includes(new URL(req.notebook_url).pathname)); if(!page)page=await context.newPage();
  for(let attempt=1;attempt<=2;attempt++){
   const temporary=path.join(path.dirname(req.output_path),`.${path.basename(req.output_path)}.${process.pid}.${crypto.randomUUID()}.part`);
   try {
    await page.goto(req.notebook_url,{waitUntil:'domcontentloaded',timeout:60000});
    const artifact=page.getByRole('button',{name:req.artifact_title,exact:false}).first();
    await artifact.waitFor({state:'visible',timeout:60000});
    await artifact.click();
    const dl=page.getByRole('button',{name:/^Download$/i}).first();
    await dl.waitFor({state:'visible',timeout:30000});
    const [download]=await Promise.all([page.waitForEvent('download',{timeout:120000}),dl.click()]);
    await download.saveAs(temporary);
    const failure=await download.failure();
    if(failure)fail(`download failed: ${failure}`);
    const a=await probe(temporary,req.ffprobe_bin);
    verifyExpected(a,req);
    await publishVerifiedDownload(temporary,req.output_path,req.allow_root);
    const r={schema_version:1,request_id:req.request_id,story_id:req.story_id,...(req.request_token?{request_token:req.request_token}:{}),status:'done',notebook_url:req.notebook_url,...(req.expected_format?{video_format:req.expected_format}:{}),output_path:req.output_path,allow_root:req.allow_root,timestamp:new Date().toISOString(),artifact:a,evidence:{idempotent_existing:false,artifact_title:req.artifact_title,suggested_filename:download.suggestedFilename(),selector_attempts:attempt,download_failure:null,local_worker:true}};
    await atomicJson(receipt,r,req.allow_root);
    console.log(JSON.stringify(r));
    return;
   }catch(e){
    await fs.unlink(temporary).catch(()=>{});
    if(e?.code==='EEXIST'){
     const concurrent=await verifyExistingArtifact(req,{receiptAttempts:40,receiptDelayMs:50});
     if(!concurrent)fail('concurrent output publication disappeared before verification');
     console.log(JSON.stringify(concurrent));
     return;
    }
    lastError=e;
    if(attempt===1)await page.reload({waitUntil:'domcontentloaded',timeout:60000}).catch(()=>{});
   }
  }
  throw lastError;
 } finally { await browser.disconnect(); }
}
const invokedAsScript = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (invokedAsScript) {
  main().catch(async e=>{console.error(e.message||String(e));process.exitCode=1;});
}
