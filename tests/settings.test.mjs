import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import vm from 'node:vm';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const html=readFileSync(path.join(root,'static','index.html'),'utf8');
const start=html.indexOf('function cleanToken');
const end=html.indexOf('function applyCredentialMode',start);
assert.ok(start>=0&&end>start,'settings helpers not found');

const context=vm.createContext({
  VOICES:[['voice','Voice']],
  sGet:()=>'',
});
vm.runInContext(html.slice(start,end),context);

assert.equal(context.settingUsable('xf_apikey','short-key'),false);
assert.equal(context.settingUsable('xf_apikey','a'.repeat(32)),true);
assert.equal(context.settingUsable('xf_apikey','a'.repeat(16)+' 通讯建立失败'),false);
assert.equal(context.credentialCollision('1234567890','1234567890'),true);
assert.equal(context.credentialCollision('minimax-key','doubao-token'),false);
vm.runInContext("serverManagedCredentials=true;serverCredentialStatus={ds_key:true,mm_tts_key:true}",context);
assert.equal(vm.runInContext('communicationsReady()',context),true);
vm.runInContext("serverCredentialStatus={ds_key:true,mm_tts_key:false,mm_key:false}",context);
assert.equal(vm.runInContext('communicationsReady()',context),false);
vm.runInContext("serverCredentialStatus={ds_key:false,mm_tts_key:false,mm_key:true}",context);
assert.equal(vm.runInContext('communicationsReady()',context),true);

const nativeContext=vm.createContext({
  location:{protocol:'capacitor:',hostname:'localhost'},
  VOICES:[['voice','Voice']],
  sGet:()=>'',
});
vm.runInContext(html.slice(start,end),nativeContext);
assert.equal(vm.runInContext('serverManagedCredentials',nativeContext),true);

const voiceSection=html.slice(html.indexOf('function renderVoices'),html.indexOf("$('keySave').onclick"));
assert.doesNotMatch(voiceSection,/saveLocalSettings\(\)/);
assert.doesNotMatch(voiceSection,/sSet\('mm_key'/);
assert.match(voiceSection,/auditionKey/);
assert.match(voiceSection,/credentialCollision/);

const saveSection=html.slice(html.indexOf("$('keySave').onclick"),html.indexOf('\/\* ---------- 后端 API ---------- \*\/'));
assert.match(saveSection,/if\(!iseReady\)\{ xfIseKey=''; xfIseSecret=''; \}/);
assert.doesNotMatch(saveSection,/讯飞评测 APIKey 不完整/);

let restoreAttempts=0;
const restoredValues={};
const restoreContext=vm.createContext({
  console,Promise,
  SETTING_KEYS:['mm_key'],
  TOKEN_SETTING_KEYS:new Set(['mm_key']), VOICES:[['voice','Voice']],
  latinHeaderOk:()=>true,
  cleanToken:value=>String(value||'').trim(),
  sGet:key=>restoredValues[key]||'',
  sSet:(key,value)=>{restoredValues[key]=value;},
  setStatus:()=>{},
  fetchLocal:async()=>{
    restoreAttempts++;
    if(restoreAttempts===1) throw new Error('server still starting');
    return {ok:true,json:async()=>({settings:{mm_key:'restored-key'}})};
  },
});
const restoreStart=html.indexOf('let settingsReady');
const restoreEnd=html.indexOf('function localServerHint',restoreStart);
vm.runInContext(html.slice(restoreStart,restoreEnd),restoreContext);
assert.equal(await restoreContext.ensureLocalSettings(),false);
assert.equal(await restoreContext.ensureLocalSettings(),true);
assert.equal(restoreAttempts,2);
assert.equal(restoredValues.mm_key,'restored-key');

console.log('settings validation tests passed');
