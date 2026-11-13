"""Reproducible local P1-P3 concurrency sample; no network or production writes.

python -m server.tools.low_resource_check --output /tmp/low-resource.json
Durations/RSS describe this local simulated workload, not cloud SLOs.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import resource
import sys
from threading import Barrier, Lock
import time
from types import SimpleNamespace
from unittest.mock import patch
from server.services.market_service import MarketService
from server.services.realtime.coordinator import RealtimeCoordinator, _MemoryBackend


def run():
    fixture = json.loads((Path(__file__).parents[1] / 'fixtures/production_regression_20261101.json').read_text())
    monitor = fixture['monitor']
    public = {key: value for key, value in monitor.items() if key not in {'resetFacts', 'unresolvedFacts'}}
    size = lambda value: len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode())
    full_bytes, public_bytes = size(monitor), size(public)
    report = {'environment': 'local simulated sources and process-shared cache; no network',
              'release': json.loads((Path(__file__).parents[1] / 'release_info.json').read_text()),
              'monitor_fixture': {'cards': len(monitor.get('items', [])), 'before_bytes': full_bytes,
                  'after_bytes': public_bytes, 'removed_percent': round((1 - public_bytes / full_bytes) * 100, 2)},
              'concurrency': []}
    for users in (5, 10):
        services = [MarketService(), MarketService()]
        memory, mutex, source_calls = _MemoryBackend(), Lock(), []
        def fetch(codes):
            with mutex: source_calls.append(list(codes))
            time.sleep(.04)  # deterministic local stand-in for an upstream round trip
            return {code: {'current': 10, 'updatedAt': '2026-11-13 11:30:00', 'source': 'local-fixture'} for code in codes}
        for service in services:
            service._realtime_coordinator = RealtimeCoordinator(backend=memory)
            service._realtime_coordinator._redis = None
            service._realtime = SimpleNamespace(fetch_many=fetch)
        barrier = Barrier(users)
        def request(index, cold=False):
            if cold: barrier.wait(timeout=5)
            start = time.perf_counter()
            result = services[index % 2].realtime_quotes(['A', 'B', 'INDEX'])
            return time.perf_counter() - start, result
        cpu, wall = time.process_time(), time.perf_counter()
        try:
            with patch('server.services.market_service.quote_bucket', return_value='isolated-local-bucket'):
                with ThreadPoolExecutor(max_workers=users) as pool:
                    cold = list(pool.map(lambda i: request(i, True), range(users)))
                    cold_calls = len(source_calls)
                    warm = list(pool.map(request, range(users)))
            timings = sorted(elapsed * 1000 for elapsed, _ in warm)
            report['concurrency'].append({'users': users, 'service_objects': 2,
                'source_calls_cold': cold_calls, 'source_calls_warm_added': len(source_calls) - cold_calls,
                'source_security_requests': sum(len(codes) for codes in source_calls),
                'cold_queued_partial': sum(result['coordination'] == 'queued' for _, result in cold),
                'warm_complete_responses': sum(result['returnedCount'] == 3 for _, result in warm),
                'warm_p95_ms': round(timings[min(len(timings) - 1, int(len(timings) * .95))], 3),
                'cpu_seconds': round(time.process_time() - cpu, 4),
                'wall_seconds': round(time.perf_counter() - wall, 4),
                'peak_process_rss_mib': round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss /
                    (1024 * 1024 if sys.platform == 'darwin' else 1024), 2)})
            assert cold_calls == 1 and len(source_calls) == 1
            assert all(result['returnedCount'] == 3 for _, result in warm)
        finally:
            for service in services: service.close()
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output')
    args = parser.parse_args()
    result = json.dumps(run(), ensure_ascii=False, indent=2) + '\n'
    if args.output:
        Path(args.output).write_text(result)
    else:
        print(result, end='')
