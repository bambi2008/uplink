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
    markTurn:()=>{},markTurnOnce:()=>{},cancelTurnTrace:()=>{},beginTurnTrace:()=>({id:'test-turn'}),
    cancelFillerWarmup:()=>{},
    streamingReply:null,
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
    turnCommitSent:false, turnFinishStarted:false, turnCommitTimer:null, usingFallback:false,
    finals:'recognized words', lastPartial:'', asrWS:null,
    runtimeFeatures:{fast_eot:true}, lastAsrWasFinal:true, lastAsrUpdateAt:Date.now(),
    lastVoiceActivityAt:Date.now(), eotCandidateAt:0,
    earDiag:()=>{}, setStatus:()=>{}, fbStop:()=>{},
    finishTurn:()=>{finished++;},
  });
  vm.runInContext(section('function pauseGraceMs','function seaTick'),ctx);
  vm.runInContext(section('function armMicSilence','function startMicWatch'),ctx);

  ctx.closeAudioTurn();
  assert.equal(ctx.turnClosing,true);
  assert.ok([...timers.tasks.values()][0].delay<=550);

  ctx.resumeAudioTurn();
  assert.equal(ctx.turnClosing,false);
  assert.equal(timers.tasks.size,0);

  ctx.closeAudioTurn();
  const commitDelay=[...timers.tasks.values()][0].delay;
  timers.runDelay(commitDelay);
  assert.equal(ctx.turnCommitSent,true);
  timers.runDelay(60);
  assert.equal(finished,1);
}

function testTurnCompletionClassificationAndDelays(){
  const ctx=makeContext();
  vm.runInContext(section('function classifyTurnCompletion','function seaTick'),ctx);

  assert.equal(ctx.classifyTurnCompletion('I travel to Hong Kong twice a week.'),'strong_complete');
  assert.equal(ctx.classifyTurnCompletion('My company makes smart gardening products'),'neutral');
  for(const value of ['The reason is because','I agree and','I want to','We are developing a']){
    assert.equal(ctx.classifyTurnCompletion(value),'likely_incomplete',value);
  }
  for(const value of ['因为我们现在','然后下一步我们会','我主要想说的是']){
    assert.equal(ctx.classifyTurnCompletion(value),'likely_incomplete',value);
  }
  assert.equal(ctx.computeTurnCommitDelay({paceMode:'normal',completionClass:'strong_complete',asrIsFinal:true,msSinceLastAsrUpdate:200}),350);
  assert.equal(ctx.computeTurnCommitDelay({paceMode:'normal',completionClass:'neutral',asrIsFinal:true,msSinceLastAsrUpdate:200}),550);
  assert.equal(ctx.computeTurnCommitDelay({paceMode:'normal',completionClass:'likely_incomplete',asrIsFinal:false,msSinceLastAsrUpdate:200}),1300);
  assert.equal(ctx.computeAsrFlushDelay({hasFinal:true,hasPartial:true,heardVoice:true}),60);
  assert.equal(ctx.computeAsrFlushDelay({hasFinal:false,hasPartial:true,heardVoice:true}),220);
  assert.equal(ctx.computeAsrFlushDelay({hasFinal:false,hasPartial:false,heardVoice:true}),650);
}

function testFastEotCommitsOnlyOnce(){
  const timers=makeTimers();
  let finished=0;
  const ctx=makeContext({
    ...timers, phase:'listening', turnClosing:true, turnCommitSent:false,turnFinishStarted:false,
    turnCommitTimer:null, usingFallback:false, finals:'That is the reason.',lastPartial:'',
    lastAsrWasFinal:true,lastVoiceActivityAt:1,asrWS:null,
    runtimeFeatures:{fast_eot:true},
    computeAsrFlushDelay:({hasFinal,hasPartial,heardVoice})=>hasFinal?60:hasPartial?220:heardVoice?650:0,
    earDiag:()=>{},fbStop:()=>{},finishTurn:()=>{finished++;},
  });
  vm.runInContext(section('function armMicSilence','function startMicWatch'),ctx);
  ctx.commitAudioTurn();
  ctx.commitAudioTurn();
  assert.equal(timers.tasks.size,1);
  timers.runDelay(60);
  assert.equal(finished,1);
  ctx.commitAudioTurn();
  assert.equal(finished,1);
}

function testAsrUpdateReschedulesPendingCommit(){
  const timers=makeTimers();
  const ctx=makeContext({
    ...timers,Date,phase:'listening',turnClosing:true,turnCommitSent:false,turnFinishStarted:false,
    turnCommitTimer:null,eotCandidateAt:Date.now(),finals:'The reason is because ',lastPartial:'',
    lastAsrWasFinal:true,lastAsrUpdateAt:Date.now(),sGet:()=> 'normal',runtimeFeatures:{fast_eot:true},
    earDiag:()=>{},setStatus:()=>{},usingFallback:false,asrWS:null,markTurn:()=>{},
  });
  vm.runInContext(section('function classifyTurnCompletion','function seaTick'),ctx);
  vm.runInContext(section('function earDiag','function keepPreRoll'),ctx);
  vm.runInContext(section('function armMicSilence','function startMicWatch'),ctx);
  ctx.scheduleTurnCommit();
  const first=[...timers.tasks.values()][0].delay;
  assert.ok(first>1000);
  ctx.finals='We work with overseas clients. ';
  ctx.noteAsrUpdate('We work with overseas clients.',true);
  assert.equal(timers.tasks.size,1);
  const second=[...timers.tasks.values()][0].delay;
  assert.ok(second<=350);
}

function testFastEotFlagRestoresLegacyTiming(){
  const timers=makeTimers();
  const ctx=makeContext({
    ...timers,sGet:()=> 'normal',phase:'listening',turnClosing:false,turnCommitSent:false,
    turnFinishStarted:false,turnCommitTimer:null,runtimeFeatures:{fast_eot:false},
    pauseGraceMs:()=>950,
    finals:'Legacy sentence',lastPartial:'',usingFallback:false,asrWS:null,
    earDiag:()=>{},setStatus:()=>{},fbStop:()=>{},finishTurn:()=>{},
  });
  vm.runInContext(section('function armMicSilence','function startMicWatch'),ctx);
  ctx.closeAudioTurn();
  assert.equal([...timers.tasks.values()][0].delay,950);
  timers.runDelay(950);
  assert.equal([...timers.tasks.values()][0].delay,500);
}

function testFallbackAsrUpdatesUnifiedState(){
  const source=section('function fbStart','function fbStop');
  assert.match(source,/noteAsrUpdate\(interim\|\|live,\(sawFinal\|\|!!finals\.trim\(\)\)&&!interim\)/);
  assert.match(source,/if\(live&&live!==previous&&turnClosing&&!turnCommitSent\)resumeAudioTurn\(\)/);
}

function testThirtyUtteranceEotPolicyCorpus(){
  const ctx=makeContext();
  vm.runInContext(section('function classifyTurnCompletion','function seaTick'),ctx);
  const shortComplete=[
    'That makes sense.','I agree with you.','It was really helpful.','I work in Shenzhen.',
    'My daughter loves it.','We finished it yesterday.','I prefer the first one.',
    'The meeting starts tomorrow.','I have already tried it.','我下周要去香港见客户。',
  ];
  const mediumComplete=[
    'My company makes smart gardening products for small apartments',
    'I travel to Hong Kong twice a week for client meetings',
    'We are building a modular system that is easier to maintain',
    'The main reason is that our overseas clients asked for it',
    'I started learning English because I want to speak more naturally',
    'Our team tested the new design with three different customers',
    'I usually think for a moment before I finish a long sentence',
    'The product is useful for people who do not have a large garden',
    'We changed the plan after receiving feedback from the sales team',
    '我想用更自然的英语向海外客户介绍我们的产品',
  ];
  const pausePrefixes=[
    'My company mainly focuses on','The reason is because','We are currently developing a',
    'I would like to talk about','Our next product will','The customer asked for',
    'We have been working with','The most difficult part is','因为我们现在','然后下一步我们会',
  ];
  assert.equal(shortComplete.length+mediumComplete.length+pausePrefixes.length,30);
  assert.ok(shortComplete.every(value=>ctx.classifyTurnCompletion(value)==='strong_complete'));
  assert.ok(mediumComplete.every(value=>ctx.classifyTurnCompletion(value)!=='likely_incomplete'));
  assert.ok(pausePrefixes.every(value=>ctx.classifyTurnCompletion(value)==='likely_incomplete'));
  const strongTotal=300+ctx.computeTurnCommitDelay({paceMode:'normal',completionClass:'strong_complete',asrIsFinal:true,msSinceLastAsrUpdate:200});
  assert.ok(strongTotal<=700);
}

function streamingPlayerContext(){
  class FakeSourceBuffer{
    constructor(){this.updating=false;this.appended=[];this.listeners={};}
    addEventListener(name,fn){this.listeners[name]=fn;}
    appendBuffer(buffer){this.appended.push(buffer);this.updating=true;}
    finishAppend(){this.updating=false;if(this.listeners.updateend)this.listeners.updateend();}
  }
  class FakeMediaSource{
    static isTypeSupported(type){return type==='audio/mpeg';}
    constructor(){this.readyState='closed';this.listeners={};this.buffer=new FakeSourceBuffer();this.ended=0;}
    addEventListener(name,fn){this.listeners[name]=fn;}
    addSourceBuffer(type){assert.equal(type,'audio/mpeg');return this.buffer;}
    open(){this.readyState='open';this.listeners.sourceopen();}
    endOfStream(){this.ended++;this.readyState='ended';}
  }
  class FakeAudio{
    constructor(){this.paused=true;this.playCalls=0;}
    play(){this.playCalls++;this.paused=false;return Promise.resolve();}
    pause(){this.paused=true;}
  }
  class FakeWebSocket{
    static OPEN=1;
    constructor(){this.readyState=0;this.sent=[];}
    send(value){this.sent.push(JSON.parse(value));}
    close(){this.readyState=3;}
  }
  const ctx=makeContext({
    MediaSource:FakeMediaSource,Audio:FakeAudio,WebSocket:FakeWebSocket,Blob:class {},
    URL:{createObjectURL:()=> 'blob:test',revokeObjectURL:()=>{}},
    location:{protocol:'http:',host:'127.0.0.1:8800'},performance:{now:()=>100},
    setTimeout:()=>1,clearTimeout:()=>{},interruptVersion:3,active:true,
    playing:false,playbackOwner:-1,curAudio:null,audioUnlocked:true,semanticHasPlayed:false,
    speakingStartedAt:0,bargeNoiseFloor:0.003,micNoiseFloor:0.003,phase:'thinking',genDone:false,
    setHalo:()=>{},setStatus:()=>{},earDiag:()=>{},markTurn:()=>{},startListening:()=>{},
    scheduleFillerWarmup:()=>{},
  });
  vm.runInContext(section('class StreamingReplyPlayer','/* ---------- \u8bbe\u7f6e\u5f39\u7a97 ---------- */'),ctx);
  return ctx;
}

async function testStreamingPlayerQueuesAndEnds(){
  const ctx=streamingPlayerContext();
  const player=vm.runInContext("new StreamingReplyPlayer({replyId:'r1',generation:3,onFallback:()=>{}})",ctx);
  player.mediaSource.open();
  player.onChunk(new Uint8Array([1,2]).buffer);
  player.onChunk(new Uint8Array([3,4]).buffer);
  assert.equal(player.sourceBuffer.appended.length,1);
  assert.equal(player.chunks.length,1);
  player.sourceBuffer.finishAppend();
  await Promise.resolve();
  assert.equal(player.audio.playCalls,1);
  assert.equal(player.sourceBuffer.appended.length,2);
  player.replyFinished=true;
  player.sourceBuffer.finishAppend();
  assert.equal(player.mediaSource.ended,1);
}

function testStreamingTimeoutEndsOnlyWhenPlaybackReallyStarts(){
  const ctx=streamingPlayerContext();
  const player=vm.runInContext("new StreamingReplyPlayer({replyId:'r1',generation:3,onFallback:()=>{}})",ctx);
  player.mediaSource.open(); player.firstChunkTimer=17;
  player.onChunk(new Uint8Array([1]).buffer);
  assert.equal(player.firstChunkTimer,17);
  player.onPlaying();
  assert.equal(player.firstChunkTimer,null);
}

function testStreamWaitsForBlobPlayerToReleaseOwnership(){
  const ctx=streamingPlayerContext();
  ctx.playing=true; ctx.curAudio=null;
  const player=vm.runInContext("new StreamingReplyPlayer({replyId:'r1',generation:3,onFallback:()=>{}})",ctx);
  player.firstChunk=true; player.tryPlay();
  assert.equal(player.audio.playCalls,0);
  ctx.playing=false; player.tryPlay();
  assert.equal(player.audio.playCalls,1);
}

function testLatePlayingCannotReviveFailedStream(){
  const ctx=streamingPlayerContext();
  const player=vm.runInContext("new StreamingReplyPlayer({replyId:'r1',generation:3,onFallback:()=>{}})",ctx);
  player.failed=true; player.onPlaying();
  assert.equal(player.hasPlayed,false);
  assert.equal(ctx.curAudio,null);
}

function testStartedStreamCanDrainBufferedAudioAfterUpstreamFailure(){
  const ctx=streamingPlayerContext();
  const player=vm.runInContext("new StreamingReplyPlayer({replyId:'r1',generation:3,onFallback:()=>{}})",ctx);
  player.mediaSource.open(); player.hasPlayed=true; player.firstChunk=true;
  player.onChunk(new Uint8Array([1]).buffer);
  player.onChunk(new Uint8Array([2]).buffer);
  assert.equal(player.sourceBuffer.appended.length,1);
  player.fail('upstream closed');
  player.sourceBuffer.finishAppend();
  assert.equal(player.sourceBuffer.appended.length,2);
  player.sourceBuffer.finishAppend();
  assert.equal(player.mediaSource.ended,1);
}

function testPartialStreamHandsOffQueuedBlobBeforeListening(){
  const ctx=streamingPlayerContext();
  let pumped=0, listened=0;
  ctx.audioQueue=[Promise.resolve({})]; ctx.genDone=true;
  ctx.pumpQueue=()=>{pumped++;}; ctx.startListening=()=>{listened++;};
  const player=vm.runInContext("new StreamingReplyPlayer({replyId:'r1',generation:3,onFallback:()=>{}})",ctx);
  player.hasPlayed=true; player.onEnded();
  assert.equal(pumped,1);
  assert.equal(listened,0);
}

function testUnplayedStreamCannotClobberBlobPlaybackOnLateEnded(){
  const ctx=streamingPlayerContext();
  const blobAudio={id:'blob'};
  ctx.playing=true; ctx.playbackOwner=3; ctx.curAudio=blobAudio;
  const player=vm.runInContext("new StreamingReplyPlayer({replyId:'r1',generation:3,onFallback:()=>{}})",ctx);
  player.failed=true; player.onEnded();
  assert.equal(ctx.playing,true);
  assert.equal(ctx.playbackOwner,3);
  assert.equal(ctx.curAudio,blobAudio);
}

function testStreamingPreconnectDefersTaskUntilFirstSegment(){
  const ctx=streamingPlayerContext();
  const player=vm.runInContext("new StreamingReplyPlayer({replyId:'r1',generation:3,voice:'Jake',model:'speech-2.8-turbo',languageBoost:'auto',emotion:'',onFallback:()=>{}})",ctx);
  player.ws.readyState=1;
  player.onMessage({data:JSON.stringify({type:'ready'})});
  assert.deepEqual(player.ws.sent,[]);

  player.speak('你好，今天过得怎么样？',{languageBoost:'Chinese',emotion:'happy'});
  assert.equal(player.ws.sent.length,2);
  assert.deepEqual(player.ws.sent.map(command=>command.type),['begin_reply','speak']);
  assert.equal(player.ws.sent[0].language_boost,'Chinese');
  assert.equal(player.ws.sent[0].emotion,'happy');
  assert.equal(player.ws.sent[1].text,'你好，今天过得怎么样？');

  const enqueueSource=section('function enqueueSpeech','async function prepFillers');
  assert.match(enqueueSource,/if\(isFirst&&!streamingReply\)streamingReply=beginStreamingReply\(text\)/);
}

function testStreamingPlayerDropsOldChunksAndFallbacksOnce(){
  const ctx=streamingPlayerContext();
  ctx.fallbackCount=0;
  const player=vm.runInContext("new StreamingReplyPlayer({replyId:'r1',generation:3,onFallback:()=>{fallbackCount++;}})",ctx);
  player.mediaSource.open(); player.speak('First sentence.');
  ctx.interruptVersion=4;
  player.onChunk(new Uint8Array([1]).buffer);
  assert.equal(player.sourceBuffer.appended.length,0);
  player.fail('before audio'); player.fail('again');
  assert.equal(ctx.fallbackCount,1);
}

function testFailedPreconnectLeavesListeningRecoveryToBlobQueue(){
  const finishSource=section('  finish(){','  fail(reason){');
  assert.doesNotMatch(finishSource,/startListening/);
  const handler=section('async function handleUserSpeech','function cleanForDisplay');
  assert.match(handler,/streamingReply&&!streamingReply\.failed&&!streamingReply\.cancelled/);
  assert.match(handler,/else pumpQueue\(\)/);
}

function testStreamingPlayerCancelAndUnsupportedFallback(){
  const ctx=streamingPlayerContext();
  const player=vm.runInContext("new StreamingReplyPlayer({replyId:'r1',generation:3,onFallback:()=>{}})",ctx);
  player.ws.readyState=1; player.cancel();
  assert.equal(player.cancelled,true);
  assert.equal(player.ws.sent.at(-1).type,'cancel');
  ctx.MediaSource.isTypeSupported=()=>false;
  assert.equal(vm.runInContext('StreamingReplyPlayer.supported()',ctx),false);
  const selection=section('function beginStreamingReply','function enqueueSpeech');
  assert.match(selection,/!StreamingReplyPlayer\.supported\(\)/);
  vm.runInContext(selection,ctx);
  ctx.runtimeFeatures={stream_tts:false,low_latency:true};
  assert.equal(ctx.beginStreamingReply('A complete sentence.'),null);
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

async function testFirstSpeakableSplitUsesSafeBoundaries(){
  const encoder=new TextEncoder();
  const chunks=[
    '[neutral] This response keeps going without punctuation until it reaches a useful natural boundary',
  ];
  let index=0;
  const ctx=makeContext({
    interruptVersion:2,interrupted:false,callAbort:null,curEmotion:'',EMOTIONS:['neutral'],
    hdrs:()=>({}),markTurn:()=>{},fetchLocal:async()=>({ok:true,body:{getReader:()=>({
      read:async()=>index<chunks.length?{done:false,value:encoder.encode(chunks[index++])}:{done:true},
      cancel:()=>{},
    })}}),
  });
  vm.runInContext(section('async function chatStream','/* ---------- \u8bed\u97f3\u5408\u6210\u64ad\u653e\u961f\u5217 ---------- */'),ctx);
  const emitted=[];
  await ctx.chatStream([],value=>emitted.push(value));
  assert.ok(emitted.length>=2);
  assert.equal(emitted.join(' '),'This response keeps going without punctuation until it reaches a useful natural boundary');

  chunks.splice(0,chunks.length,'[neutral] Please repeat this later [echo: This complete target phrase stays together]');
  index=0; emitted.length=0;
  await ctx.chatStream([],value=>emitted.push(value));
  assert.equal(emitted.length,1);
  assert.match(emitted[0],/\[echo: This complete target phrase stays together\]/);
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
    credentialAvailable:()=>true,
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

function testChineseAndEnglishFirstSegmentsUseTurbo(){
  const ctx=makeContext({runtimeFeatures:{low_latency:true}});
  vm.runInContext(section('function firstSegmentTtsModel','function enqueueSpeech'),ctx);
  assert.equal(ctx.firstSegmentTtsModel(true),'speech-2.8-turbo');
  assert.equal(ctx.firstSegmentTtsModel(false),null);
  ctx.runtimeFeatures.low_latency=false;
  assert.equal(ctx.firstSegmentTtsModel(true),null);

  const enqueueSource=section('function enqueueSpeech','async function prepFillers');
  assert.doesNotMatch(enqueueSource,/hasCJK/);
  assert.match(enqueueSource,/firstSegmentTtsModel\(isFirst\)/);
}

function testAcknowledgementIsNotSemanticFirstAudio(){
  let now=100, posted=null;
  const ctx=makeContext({
    performance:{now:()=>now}, runtimeFeatures:{latency_trace:true}, turnTraceSequence:0,
    fetch:async(path,options)=>{posted=JSON.parse(options.body);return {ok:true};},
    console:{...console,info:()=>{}},
  });
  vm.runInContext(section('class TurnLatencyTrace','/* ---------- \u8bbe\u7f6e\u5f39\u7a97 ---------- */'),ctx);
  const trace=vm.runInContext("new TurnLatencyTrace('microphone')",ctx);
  now=200; trace.mark('last_effective_voice');
  now=300; trace.mark('ack_audio_playing');
  assert.equal(trace.finalized,false);
  now=700; trace.mark('semantic_audio_playing',{tts_path:'blob'});
  assert.equal(trace.finalized,true);
  assert.equal(posted.semantic_first_audio_ms,500);
  assert.equal(posted.stages_ms.ack_audio_playing,100);
}

function testFillerWarmupStaysOffCriticalStartupPath(){
  const startSource=section('async function startCall','function startTimer');
  assert.doesNotMatch(startSource,/connectASR\(\);\s*prepFillers\(\)/);
  const queueSource=section('async function pumpQueue','/* \u64ad\u653e\u4e00\u6bb5\u8bed\u97f3');
  assert.match(queueSource,/startListening\(\); scheduleFillerWarmup\(\)/);
  const fillerSource=section('async function prepFillers','let lastFiller');
  assert.match(fillerSource,/const acks=\['Mm-hm\.',\s*'Right\.',\s*'Got it\.'\]/);
  assert.match(fillerSource,/const thinks=\['Hmm\.\.\.',\s*'\(emm\)'\]/);
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
testTurnCompletionClassificationAndDelays();
testFastEotCommitsOnlyOnce();
testAsrUpdateReschedulesPendingCommit();
testFastEotFlagRestoresLegacyTiming();
testFallbackAsrUpdatesUnifiedState();
testThirtyUtteranceEotPolicyCorpus();
await testStreamingPlayerQueuesAndEnds();
testStreamingTimeoutEndsOnlyWhenPlaybackReallyStarts();
testStreamWaitsForBlobPlayerToReleaseOwnership();
testLatePlayingCannotReviveFailedStream();
testStartedStreamCanDrainBufferedAudioAfterUpstreamFailure();
testPartialStreamHandsOffQueuedBlobBeforeListening();
testUnplayedStreamCannotClobberBlobPlaybackOnLateEnded();
testStreamingPreconnectDefersTaskUntilFirstSegment();
testStreamingPlayerDropsOldChunksAndFallbacksOnce();
testFailedPreconnectLeavesListeningRecoveryToBlobQueue();
testStreamingPlayerCancelAndUnsupportedFallback();
testVoiceTakeoverNeedsSustainedSpeech();
testInterruptInvalidatesOldWork();
testLiveCoachingPromptIsEphemeral();
testBackendFailuresAreReportedTogether();
await testStaleStreamCannotQueueSpeech();
await testFirstSpeakableSplitUsesSafeBoundaries();
await testStalePlaybackCannotReopenMic();
await testInterruptedSynthesisCannotPlayLateAudio();
testOldCallTimersCannotEnterNewCall();
await testLateMicrophonePermissionCannotReplaceNewCall();
await testScoringCannotResumeAfterHangup();
await testResolvedTimeoutClearsItsTimer();
testStartLockPrecedesAsyncSetup();
testChineseAndEnglishFirstSegmentsUseTurbo();
testAcknowledgementIsNotSemanticFirstAudio();
testFillerWarmupStaysOffCriticalStartupPath();
await testReconnectResetsRuntimeBeforeOpeningStream();

console.log('turn-taking state tests passed');
