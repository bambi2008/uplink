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

function testBackendFailuresAreReportedTogether(){
  let fallbackReason='', scheduled=null;
  const conn={innerHTML:''};
  const ctx=makeContext({
    engQ:['db','xf'], engineFailures:[], engineFailing:false,
    asrWS:null, asrOk:true, asrAcceptingAudio:true, asrTries:2,
    console:{...console,warn:()=>{}}, earDiag:()=>{},
    $:()=>conn, callLater:fn=>{scheduled=fn;return 1;},
    active:true, usingFallback:false, connectASR:()=>{},
    startFallbackASR:reason=>{fallbackReason=reason;},
  });
  vm.runInContext(section('function failEngine','function connectASR'),ctx);

  ctx.failEngine('豆包后端报错：HTTP 401');
  assert.equal(ctx.engQ.length,1);
  assert.match(ctx.engineFailures[0],/豆包.*401/);
  assert.ok(scheduled);
  scheduled();

  ctx.failEngine('讯飞后端报错：授权不可用（10110）');
  assert.match(fallbackReason,/豆包.*401/);
  assert.match(fallbackReason,/讯飞.*10110/);
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
    playing:false, playbackOwner:-1, interruptVersion:2, interrupted:false,
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

async function testInterruptedSynthesisCannotPlayLateAudio(){
  let releaseBlob, played=0;
  const pendingBlob=new Promise(resolve=>{releaseBlob=resolve;});
  const ctx=makeContext({
    playing:false, playbackOwner:-1, interruptVersion:5, interrupted:false,
    audioQueue:[pendingBlob], active:true, genDone:true, gapFills:0,
    fillerBlobs:[], lastFiller:-1, phase:'thinking',
    setHalo:()=>{}, setStatus:()=>{}, earDiag:()=>{}, startListening:()=>{},
    playBlob:async()=>{played++;}, setTimeout:()=>0,
  });
  vm.runInContext(section('async function pumpQueue','/* \u64ad\u653e\u4e00\u6bb5\u8bed\u97f3'),ctx);

  const pumping=ctx.pumpQueue();
  ctx.interruptVersion++;
  ctx.playbackOwner=-1;
  ctx.playing=false;
  ctx.interrupted=false;
  releaseBlob({});
  await pumping;
  assert.equal(played,0);
}

function testOldCallTimersCannotEnterNewCall(){
  let scheduled, ran=0;
  const ctx=makeContext({
    callGeneration:3,
    setTimeout:fn=>{scheduled=fn;return 1;},
  });
  vm.runInContext(section('function callLater','/* ---------- \u8bbe\u7f6e\u5f39\u7a97 ---------- */'),ctx);
  ctx.callLater(()=>{ran++;},300);
  ctx.callGeneration++;
  scheduled();
  assert.equal(ran,0);
}

async function testLateMicrophonePermissionCannotReplaceNewCall(){
  let grantPermission, stopped=0;
  const oldStream={getTracks:()=>[{stop:()=>{stopped++;}}]};
  const ctx=makeContext({
    callGeneration:4, micPipeOk:true,
    micStream:null, audioCtx:null, micSource:null, micNode:null, micSink:null,
    navigator:{mediaDevices:{getUserMedia:()=>new Promise(resolve=>{grantPermission=resolve;})}},
  });
  vm.runInContext(section('async function openMic','let asrOk='),ctx);

  const opening=ctx.openMic();
  ctx.callGeneration++;
  ctx.micPipeOk=true;
  ctx.micStream='new-call-stream';
  grantPermission(oldStream);
  assert.equal(await opening,false);
  assert.equal(stopped,1);
  assert.equal(ctx.micPipeOk,true);
  assert.equal(ctx.micStream,'new-call-stream');
}

async function testScoringCannotResumeAfterHangup(){
  let finishScoring, handled=0, scored=0;
  const ctx=makeContext({
    phase:'listening', turnClosing:false, turnCommitSent:false,
    micSilenceTimer:null, turnCommitTimer:null, interruptVersion:7,
    finals:'hello', lastPartial:'', pendingEcho:'hello',
    echoChunks:[{length:6400}], usingFallback:false, micLastVoice:1,
    active:true, thinkingStartedAt:0,
    clearTimeout:()=>{}, showPartial:()=>{}, listenAgain:()=>{}, fbStop:()=>{},
    setHalo:()=>{}, setStatus:()=>{}, sGet:()=> 'configured',
    iseEvaluate:()=>new Promise(resolve=>{finishScoring=resolve;}),
    addScore:()=>{scored++;}, handleUserSpeech:()=>{handled++;},
  });
  vm.runInContext(section('async function finishTurn','function pcmToB64'),ctx);

  const finishing=ctx.finishTurn();
  assert.equal(ctx.phase,'processing');
  ctx.active=false;
  ctx.interruptVersion++;
  finishScoring({human:'score',forJake:'report'});
  await finishing;
  assert.equal(scored,0);
  assert.equal(handled,0);
}

async function testResolvedTimeoutClearsItsTimer(){
  const timers=makeTimers();
  const ctx=makeContext(timers);
  vm.runInContext(section('function withTimeout','function resetCallRuntime'),ctx);
  assert.equal(await ctx.withTimeout(Promise.resolve('ready'),5000,'test'),'ready');
  assert.equal(timers.tasks.size,0);
}

function testStartLockPrecedesAsyncSetup(){
  const startSource=section('async function startCall','function startTimer');
  assert.match(startSource,/if\(active\|\|starting\|\|\$\('btnStart'\)\.disabled\) return/);
  assert.ok(startSource.indexOf('starting=true')<startSource.indexOf('await ensureLocalSettings()'));
  const cleanupSource=section('function cleanup','async function endCall');
  assert.match(cleanupSource,/starting=false/);
}

async function testReconnectResetsRuntimeBeforeOpeningStream(){
  const cleared=[];
  const ctx=makeContext({
    clearTimeout:id=>cleared.push(id),
    clearInterval:()=>{}, tick:null,
    silenceTimer:11, micSilenceTimer:12, turnCommitTimer:13,
    interrupted:true, audioQueue:[Promise.resolve({})], playing:true, playbackOwner:8, curAudio:{}, genDone:false,
    ttsErrShown:true, curEmotion:'angry', replySeg:4, gapFills:2, pendingEcho:'try me',
    finals:'old words', lastPartial:'old partial', partialEl:{}, echoChunks:[1],
    pendingPreRoll:[1], bargePreRoll:[1], bargeFrames:3,
    thinkingStartedAt:1, speakingStartedAt:1, turnClosing:true, turnCommitSent:true,
    asrRestarting:true, engineFailing:true, interruptVersion:9, callGeneration:2, callAbort:null,
    hdrs:()=>({}), EMOTIONS:['neutral'],
  });
  const encoder=new TextEncoder();
  let reads=0, emitted='';
  ctx.fetchLocal=async()=>({
    ok:true,
    body:{getReader:()=>({
      read:async()=>reads++===0
        ?{done:false,value:encoder.encode('[neutral] Jake is speaking after reconnect.')}
        :{done:true},
      cancel:()=>{throw new Error('fresh reconnect stream was cancelled');},
    })},
  });
  vm.runInContext(section('function resetCallRuntime','async function startCall'),ctx);
  vm.runInContext(section('async function chatStream','/* ---------- \u8bed\u97f3\u5408\u6210\u64ad\u653e\u961f\u5217 ---------- */'),ctx);

  ctx.resetCallRuntime();
  assert.equal(ctx.interrupted,false);
  assert.equal(ctx.audioQueue.length,0);
  assert.equal(ctx.playing,false);
  assert.equal(ctx.playbackOwner,-1);
  assert.equal(ctx.genDone,true);
  assert.equal(ctx.ttsErrShown,false);
  assert.equal(ctx.pendingEcho,null);
  assert.deepEqual(cleared,[11,12,13]);

  const full=await ctx.chatStream([],sentence=>{emitted=sentence;});
  assert.equal(full,'Jake is speaking after reconnect.');
  assert.equal(emitted,'Jake is speaking after reconnect.');

  const startSource=section('async function startCall','function startTimer');
  assert.ok(startSource.indexOf('resetCallRuntime()')<startSource.indexOf('callAbort='));
  assert.ok(startSource.indexOf('resetCallRuntime()')<startSource.indexOf('chatStream(history'));
}

testPauseCanResume();
testVoiceTakeoverNeedsSustainedSpeech();
testInterruptInvalidatesOldWork();
testLiveCoachingPromptIsEphemeral();
testBackendFailuresAreReportedTogether();
await testStaleStreamCannotQueueSpeech();
await testStalePlaybackCannotReopenMic();
await testInterruptedSynthesisCannotPlayLateAudio();
testOldCallTimersCannotEnterNewCall();
await testLateMicrophonePermissionCannotReplaceNewCall();
await testScoringCannotResumeAfterHangup();
await testResolvedTimeoutClearsItsTimer();
testStartLockPrecedesAsyncSetup();
await testReconnectResetsRuntimeBeforeOpeningStream();

console.log('turn-taking state tests passed');
