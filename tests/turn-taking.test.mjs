import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import vm from 'node:vm';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const html=readFileSync(path.join(root,'static','index.html'),'utf8');

function section(start,end){
  const a=html.indexOf(start), b=html.indexOf(end,a+start.length);
  assert.ok(a>=0,`missing source marker: ${start}`);
  assert.ok(b>a,`missing source marker: ${end}`);
  return html.slice(a,b);
}

function makeContext(extra={}){
  return vm.createContext({
    console,Date,Math,Promise,TextDecoder,TextEncoder,
    ...extra,
  });
}

function makeTimers(){
  let next=1;
  const tasks=new Map();
  return {
    tasks,
    setTimeout(fn,delay){const id=next++;tasks.set(id,{fn,delay});return id;},
    clearTimeout(id){tasks.delete(id);},
    runDelay(delay){
      const found=[...tasks].find(([,task])=>task.delay===delay);
      assert.ok(found,`missing timer with delay ${delay}`);
      tasks.delete(found[0]); found[1].fn();
    },
  };
}

function testPauseCanResume(){
  const timers=makeTimers();
  let finished=0;
  const ctx=makeContext({
    ...timers,
    sGet:()=> 'normal', phase:'listening', turnClosing:false,
    turnCommitSent:false, turnCommitTimer:null, usingFallback:false,
    finals:'recognized words', lastPartial:'', asrWS:null,
    earDiag:()=>{}, setStatus:()=>{}, fbStop:()=>{},
    finishTurn:()=>{finished++;},
  });
  vm.runInContext(section('function pauseGraceMs','function seaTick'),ctx);
  vm.runInContext(section('function armMicSilence','function startMicWatch'),ctx);

  ctx.closeAudioTurn();
  assert.equal(ctx.turnClosing,true);
  assert.equal([...timers.tasks.values()][0].delay,950);

  ctx.resumeAudioTurn();
  assert.equal(ctx.turnClosing,false);
  assert.equal(timers.tasks.size,0);

  ctx.closeAudioTurn();
  timers.runDelay(950);
  assert.equal(ctx.turnCommitSent,true);
  timers.runDelay(500);
  assert.equal(finished,1);
}

function testVoiceTakeoverNeedsSustainedSpeech(){
  let interrupted=0, captured=[];
  const ctx=makeContext({
    phase:'speaking', playing:true, curAudio:{paused:false},
    speakingStartedAt:Date.now()-1000, thinkingStartedAt:0,
    bargeFrames:0, bargePreRoll:[], bargeNoiseFloor:0.003,
    micNoiseFloor:0.003,
    interruptJakeForUser:(source,pre)=>{interrupted++;captured=pre;},
  });
  vm.runInContext(section('function monitorUserTakeover','async function openMic'),ctx);
  const frame=new Int16Array(4096);

  ctx.monitorUserTakeover(frame,0.08,0.01);
  assert.equal(interrupted,0);
  ctx.monitorUserTakeover(frame,0.08,0.01);
  assert.equal(interrupted,1);
  assert.equal(captured.length,2);
}

function testInterruptInvalidatesOldWork(){
  let stopped=0, started=0, flushed=0;
  const ctx=makeContext({
    phase:'thinking', interruptVersion:4, interrupted:false,
    curAudio:{pause:()=>{}}, stopCurrentAudio:()=>{stopped++;},
    audioQueue:[1,2], playing:true, genDone:false, history:[],
    pendingPreRoll:[], startListening:preserve=>{assert.equal(preserve,true);started++;},
    flushPendingPreRoll:()=>{flushed++;}, setStatus:()=>{}, earDiag:()=>{},
  });
  vm.runInContext(section('function interruptJakeForUser',"$('haloWrap').onclick"),ctx);
  const pre=[new Int16Array(4),new Int16Array(4)];
  assert.equal(ctx.interruptJakeForUser('voice',pre),true);
  assert.equal(ctx.interruptVersion,5);
  assert.equal(ctx.audioQueue.length,0);
  assert.equal(ctx.history.length,1);
  assert.equal(ctx.pendingPreRoll.length,2);
  assert.equal(stopped,1); assert.equal(started,1); assert.equal(flushed,1);
}

function testLiveCoachingPromptIsEphemeral(){
  const ctx=makeContext();
  vm.runInContext(section('const LIVE_COACHING_REMINDER','async function handleUserSpeech'),ctx);
  const original=[
    {role:'system',content:'Jake persona'},
    {role:'assistant',content:'How was your weekend?'},
    {role:'user',content:'Yesterday I go to the market.'},
  ];
  const coached=ctx.withLiveCoaching(original);

  assert.notEqual(coached,original);
  assert.equal(coached.length,original.length);
  assert.equal(original[0].content,'Jake persona');
  assert.match(coached[0].content,/TURN-SPECIFIC LIVE COACHING PRIORITY/);
  assert.match(coached[0].content,/begin with ONE brief spoken correction/);
  assert.equal(coached.at(-1).role,'user');
  assert.equal(coached.at(-1).content,original.at(-1).content);

  const handler=section('async function handleUserSpeech','function cleanForDisplay');
  assert.match(handler,/chatStream\(withLiveCoaching\(history\)/);
}

async function testStaleStreamCannotQueueSpeech(){
  let emitted=0, cancelled=false;
  const ctx=makeContext({
    interruptVersion:8, interrupted:false, callAbort:null,
    hdrs:()=>({}), EMOTIONS:['neutral'], curEmotion:'',
  });
  const encoder=new TextEncoder();
  let reads=0;
  ctx.fetchLocal=async()=>({
    ok:true,
    body:{getReader:()=>({
      read:async()=>{
        if(reads++===0){ctx.interruptVersion++;return {done:false,value:encoder.encode('[neutral] This stale sentence must not play.')};}
        return {done:true};
      },
      cancel:()=>{cancelled=true;},
    })},
  });
  vm.runInContext(section('async function chatStream','/* ---------- \u8bed\u97f3\u5408\u6210\u64ad\u653e\u961f\u5217 ---------- */'),ctx);
  const full=await ctx.chatStream([],()=>{emitted++;});
  assert.equal(full,'');
  assert.equal(emitted,0);
  assert.equal(cancelled,true);
}

async function testStalePlaybackCannotReopenMic(){
  let listeningStarts=0;
  const ctx=makeContext({
    playing:false, interruptVersion:2, interrupted:false,
    audioQueue:[{}], active:true, genDone:true, gapFills:0,
    fillerBlobs:[], lastFiller:-1,
    setHalo:()=>{}, setStatus:()=>{}, earDiag:()=>{},
    startListening:()=>{listeningStarts++;},
    playBlob:async()=>{ctx.interruptVersion++;},
    setTimeout:()=>0,
  });
  vm.runInContext(section('async function pumpQueue','/* \u64ad\u653e\u4e00\u6bb5\u8bed\u97f3'),ctx);
  await ctx.pumpQueue();
  assert.equal(listeningStarts,0);
  assert.equal(ctx.playing,false);
}

testPauseCanResume();
testVoiceTakeoverNeedsSustainedSpeech();
testInterruptInvalidatesOldWork();
testLiveCoachingPromptIsEphemeral();
await testStaleStreamCannotQueueSpeech();
await testStalePlaybackCannotReopenMic();

console.log('turn-taking state tests passed');
