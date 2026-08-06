import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import vm from 'node:vm';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const html=readFileSync(path.join(root,'static','index.html'),'utf8');
const start=html.indexOf('function cleanToken');
const end=html.indexOf('function collectSettings',start);
assert.ok(start>=0&&end>start,'settings helpers not found');

const context=vm.createContext({
  VOICES:[['voice','Voice']],
  sGet:()=>'',
});
vm.runInContext(html.slice(start,end),context);

assert.equal(context.settingUsable('xf_apikey','short-key'),false);
assert.equal(context.settingUsable('xf_apikey','a'.repeat(32)),true);
assert.equal(context.settingUsable('xf_apikey','a'.repeat(16)+' 通讯建立失败'),false);

console.log('settings validation tests passed');
