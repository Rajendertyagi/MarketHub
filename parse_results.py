import json
d = json.load(open('test/benchmarks/artifacts3/b8-benchmark-results.json'))

q = d.get('q1q7_quote_scale', {}).get('rows', [])
print('=== Q1-Q7 ===')
for r in q:
    print(r['scenario'], ':', 'total='+str(r['total_alerts']), 'bucket='+str(r['target_bucket']), 'eval='+str(r['evaluated_alerts']), 'p50='+str(r['p50_ms'])+'ms')

print()
sc = d.get('slow_chain_blocking', {}).get('rows', [])
print('=== SLOW CHAIN ===')
for r in sc:
    print(r['scenario'], ':', 'total='+str(r['total_cycle_ms'])+'ms', 'calls='+str(r['call_count']))

print()
rs = d.get('restart_10k', {}).get('rows', [])
print('=== RESTART 10K ===')
for r in rs:
    print('p50='+str(r['p50_ms'])+'ms', 'p95='+str(r['p95_ms'])+'ms', 'p99='+str(r['p99_ms'])+'ms', 'loaded='+str(r['loaded_alert_count']), 'correct='+str(r['correct']))

print()
rp = d.get('replay_10k', {}).get('rows', [])
print('=== REPLAY 10K ===')
for r in rp:
    if 'explain' in r['scenario']:
        print(r['scenario'], ':', 'uses_index='+str(r['uses_index']))
    else:
        print(r['scenario'], ':', 'ps='+str(r.get('page_size','')), 'returned='+str(r.get('returned','')), 'p50='+str(r.get('p50_ms',''))+'ms')

print()
ak = d.get('ack_10k', {}).get('rows', [])
print('=== ACK 10K ===')
for r in ak:
    if 'correctness' in r['scenario']:
        print('pending_after='+str(r['pending_after_ack']), 'correct='+str(r['correct']))
    else:
        print('count='+str(r['event_count']), 'total='+str(r['total_ms'])+'ms', 'p50='+str(r['per_ack_p50_ms'])+'ms', 'throughput='+str(r.get('throughput_ack_per_second','')))
