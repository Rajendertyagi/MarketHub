import json
d = json.load(open('test/benchmarks/artifacts4/b8-benchmark-results.json'))

print('=== Q1-Q7 ===')
for r in d['q1q7_quote_scale']['rows']:
    s = r['scenario']
    print(s + ': total=' + str(r['total_alerts']) + ' bucket=' + str(r['target_bucket']) + ' eval=' + str(r['evaluated_alerts']) + ' p50=' + str(r['p50_ms']) + 'ms p95=' + str(r['p95_ms']) + 'ms iter=' + str(r['iterations']))

print()
print('=== SLOW CHAIN ===')
for r in d['slow_chain_blocking']['rows']:
    s = r['scenario']
    print(s + ': total=' + str(r['total_cycle_ms']) + 'ms B_fail=' + str(r.get('B_call_count','')) + ' C_called=' + str(r.get('C_was_called','')))

print()
print('=== RESTART 10K ===')
for r in d['restart_10k']['rows']:
    print('p50=' + str(r['p50_ms']) + 'ms p95=' + str(r['p95_ms']) + 'ms loaded=' + str(r['loaded_alert_count']) + ' correct=' + str(r['correct']))

print()
print('=== REPLAY 10K ===')
for r in d['replay_10k']['rows']:
    if 'explain' in r['scenario']:
        continue
    s = r['scenario']
    ps = r.get('page_size', '')
    ret = r.get('returned', '')
    p50 = r.get('p50_ms', '')
    print(s + ': ps=' + str(ps) + ' returned=' + str(ret) + ' p50=' + str(p50) + 'ms')

print()
print('=== ACK 10K ===')
for r in d['ack_10k']['rows']:
    s = r['scenario']
    if 'correctness' in s:
        print('pending_after=' + str(r['pending_after_ack']) + ' correct=' + str(r['correct']))
    else:
        print('count=' + str(r['event_count']) + ' total=' + str(r['total_ms']) + 'ms p50=' + str(r['per_ack_p50_ms']) + 'ms throughput=' + str(r.get('throughput_ack_per_second', '')))
