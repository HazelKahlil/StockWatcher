import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import test from 'node:test';
const source = readFileSync(new URL('../src/stock_watcher/server/static/presentation.js', import.meta.url), 'utf8');
const {candidateTimestamp, retainedCandidates, displayMarketPhase, shanghaiDay, groupRecords} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
test('scan completion never makes retained candidates fresh', () => {
 const state = {candidates:[{rank:1}],source_ts:'2026-09-04T15:00:00+08:00',last_scan_completed_at:'2026-09-06T11:30:00+08:00',service_state:'healthy'};
 assert.equal(candidateTimestamp(state), state.source_ts);
 assert.equal(retainedCandidates(state,new Date('2026-09-06T11:30:00+08:00')),true);
 assert.equal(candidateTimestamp({...state,source_ts:null}),null);
 assert.equal(candidateTimestamp({...state,candidates:[]}),null);
});
test('display dates and weekend use Shanghai even before UTC midnight', () => {
 const now=new Date('2026-09-04T23:30:00Z');
 assert.equal(shanghaiDay(now),'2026-09-05');
 assert.equal(displayMarketPhase({market_state:'morning'},now),'周末休市');
 assert.equal(displayMarketPhase({market_state:'afternoon'},new Date('2026-09-04T06:00:00Z')),'下午盘');
 assert.equal(retainedCandidates({candidates:[{}],source_ts:'2026-09-04T02:00:00Z',service_state:'healthy'},new Date('2026-09-04T07:00:00Z')),false);
});
test('record filtering preserves original ranks, dates and source records', () => {
 const records=[{entry_trade_date:'2026-09-04',rank:3,code:'000001.SZ',name:'示例',status:'pending'},{entry_trade_date:'2026-09-03',rank:2,code:'000002.SZ',name:'演示',status:'settled'}];
 const original=JSON.stringify(records);
 assert.deepEqual(groupRecords(records,'pending','000001.sz'),[['2026-09-04',[records[0]]]]);
 assert.deepEqual(groupRecords(records,'all',' 演示 '),[['2026-09-03',[records[1]]]]);
 assert.equal(JSON.stringify(records),original);
});
