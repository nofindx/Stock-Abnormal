"""Local P1/P2/P3 acceptance: no live data sources, MySQL or Redis."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
import multiprocessing
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from .background_work import BackgroundBusy, heavy_work, check_owner
from .market_calc_repository import MarketCalcRepository
from .market_service import MarketService
from . import market_service as market_module
from .read_publications import ReadPublications
from .read_refresh_jobs import ReadRefreshJobs
from .realtime.coordinator import RealtimeCoordinator, _MemoryBackend
from .monitor_service import OfficialMonitorService
from .announcement_service import AnnouncementService
from . import test_production_regression as regression


class BatchRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.repository = MarketCalcRepository({'host': 'test.invalid', 'user': 'test', 'database': 'test'})
        self.repository._initialised = True
        self.queries, self.connections = [], []
        outer = self

        class Cursor:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def execute(self, sql, args): outer.queries.append((sql, args))
            def fetchall(self):
                if 'FROM index_calc_base' in outer.queries[-1][0]:
                    return [{'index_code': '000002.SH', 'index_return_vector': '[{"date":"20261112","return":0.5}]'}]
                return [{'ts_code': '600825.SH', 'symbol': '600825', 'market': 'SSE', 'board': '主板',
                         'isST': 0, 'stock_return_vector': '[{"date":"20261112","return":1.25}]'}]

        @contextmanager
        def connection():
            outer.connections.append(1)
            yield SimpleNamespace(cursor=lambda *_: Cursor())
        self.repository.connection = connection
        self.repository._pymysql = SimpleNamespace(cursors=SimpleNamespace(DictCursor=object))

    def test_s1_inputs_one_connection_two_queries_and_no_unrelated_index(self):
        calcs, indexes = self.repository.read_inputs(['600825.SH'] * 3, lambda _: '000002.SH')
        self.assertEqual(len(self.connections), 1)
        self.assertEqual(len(self.queries), 2)
        self.assertEqual(self.queries[0][1], ('600825.SH',))
        self.assertEqual(self.queries[1][1], ('000002.SH',))
        self.assertEqual(calcs['600825.SH']['stock_return_vector'][0]['return'], 1.25)
        self.assertFalse(calcs['600825.SH']['isST'])
        self.assertEqual(set(indexes), {'000002.SH'})

    def test_empty_pool_never_reads_whole_market(self):
        self.assertEqual(self.repository.read_inputs([], lambda _: '000002.SH'), ({}, {}))
        self.assertEqual(self.repository.get_calcs([]), {})
        self.assertFalse(self.connections)

    def test_monitor_prices_deduplicate_codes_and_use_latest_date(self):
        service = MarketService()
        self.addCleanup(service.close)
        get = Mock(return_value={'A': {'stock_return_vector': [
            {'date': '20261112', 'return': -2}, {'date': '20261111', 'return': 5}]}})
        service._calc_repository = SimpleNamespace(available=True, get_calcs=get)
        self.assertEqual(service.latest_stock_returns(['A', 'A']), {'A': {'value': -2, 'tradeDate': '20261112'}})
        get.assert_called_once_with(['A'])

    def test_s1_detail_keeps_same_body_without_scanning_stock_list(self):
        fixture = regression.ProductionRegressionTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        service = fixture.service
        expected = service.detail('600825.SH', use_realtime=False)
        service._stock_rows = Mock(side_effect=AssertionError('whole-market list requested'))
        read = Mock(return_value=({'600825.SH': deepcopy(fixture.calcs['600825.SH'])}, deepcopy(fixture.indexes)))
        service._calc_repository.read_inputs = read
        store = SimpleNamespace(freeze_read=lambda *_: 'frozen')
        _, payload, _, count = ReadPublications(service, store).capture(
            {'page': 'stock', 'role': 'current', 'security_id': '600825.SH'})
        for key, value in expected.items():
            self.assertEqual(payload['data'][key], value, key)
        self.assertEqual(count, 1)
        read.assert_called_once()
        service._stock_rows.assert_not_called()


class QuoteReuseTests(unittest.TestCase):
    def setUp(self):
        self.service = MarketService()
        self.addCleanup(self.service.close)
        self.service._realtime_coordinator = RealtimeCoordinator(backend=_MemoryBackend())
        self.service._realtime_coordinator._redis = None
        self.source = Mock(side_effect=lambda codes: {
            code: {'current': 10, 'updatedAt': '2026-11-13 11:30:00', 'source': 'fixture'} for code in codes})
        self.service._realtime = SimpleNamespace(fetch_many=self.source)
        self.bucket = patch.object(market_module, 'quote_bucket', return_value='bucket-1')
        self.bucket.start()
        self.addCleanup(self.bucket.stop)

    def test_stock_and_prediction_intersection_only_fetches_missing(self):
        first = self.service._fetch_realtime_many(['A', 'B'])
        second = self.service.realtime_quotes(['B', 'C', 'C'])
        self.assertEqual([call.args[0] for call in self.source.call_args_list], [['A', 'B'], ['C']])
        self.assertEqual(second['quotes']['B'], first['B'])
        self.assertEqual(second['quoteUpdatedAt'], '2026-11-13 11:30:00')
        self.assertFalse(second['persisted'])

    def test_ttl_does_not_extend_on_hit_and_policy_bucket_are_isolated(self):
        with patch('server.services.realtime.coordinator.time.time', return_value=100):
            self.service._fetch_realtime_many(['A'])
        with patch('server.services.realtime.coordinator.time.time', return_value=102.9):
            self.service._fetch_realtime_many(['A'])
        self.assertEqual(self.source.call_count, 1)
        with patch('server.services.realtime.coordinator.time.time', return_value=103):
            self.service._fetch_realtime_many(['A'])
        self.assertEqual(self.source.call_count, 2)
        with patch.dict('os.environ', {'SOURCE_POLICY_HASH': 'other'}):
            self.service._fetch_realtime_many(['A'])
        with patch.object(market_module, 'quote_bucket', return_value='bucket-2'):
            self.service._fetch_realtime_many(['A'])
        self.assertEqual(self.source.call_count, 4)

    def test_five_and_ten_concurrent_requests_fetch_once_then_share(self):
        for count in (5, 10):
            with self.subTest(count=count):
                self.service._realtime_coordinator = RealtimeCoordinator(backend=_MemoryBackend())
                self.service._realtime_coordinator._redis = None
                entered, release = threading.Event(), threading.Event()
                def fetch(codes):
                    entered.set()
                    if not release.wait(3): raise AssertionError('test source timed out')
                    return {code: {'current': 10} for code in codes}
                self.source.side_effect = fetch
                self.source.reset_mock()
                with ThreadPoolExecutor(max_workers=count) as pool:
                    owner = pool.submit(self.service.realtime_quotes, ['A', 'B'])
                    self.assertTrue(entered.wait(2))
                    joined = list(pool.map(lambda _: self.service.realtime_quotes(['A', 'B']), range(count - 1)))
                    self.assertTrue(all(result['coordination'] == 'queued' for result in joined))
                    release.set()
                    self.assertEqual(owner.result()['returnedCount'], 2)
                self.assertEqual(self.service.realtime_quotes(['A', 'B'])['returnedCount'], 2)
                self.assertEqual(self.source.call_count, 1)

    def test_concurrent_different_pools_share_intersection(self):
        entered, release = threading.Event(), threading.Event()
        def fetch(codes):
            if 'A' in codes:
                entered.set()
                self.assertTrue(release.wait(2))
            return {code: {'current': 10} for code in codes}
        self.source.side_effect = fetch
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.service.realtime_quotes, ['A', 'B'])
            self.assertTrue(entered.wait(1))
            second = pool.submit(self.service.realtime_quotes, ['B', 'C']).result()
            self.assertEqual(set(second['quotes']), {'C'})
            self.assertEqual(second['coordination'], 'queued')
            release.set()
            first.result()
        self.assertEqual(set(self.service._fetch_realtime_many(['B', 'C'])), {'B', 'C'})
        self.assertEqual(sorted(code for call in self.source.call_args_list for code in call.args[0]), ['A', 'B', 'C'])

    def test_source_and_cache_failures_never_become_empty_fresh_quotes(self):
        self.source.side_effect = RuntimeError('upstream failed')
        with self.assertRaisesRegex(RuntimeError, 'upstream failed'):
            self.service._fetch_realtime_many(['A'])
        self.source.side_effect = lambda _: {}
        self.assertEqual(self.service._fetch_realtime_many(['A']), {})
        self.source.side_effect = lambda codes: {code: {'current': 11} for code in codes}
        self.assertEqual(self.service._fetch_realtime_many(['A'])['A']['current'], 11)
        self.service._realtime_coordinator.get_many = Mock(side_effect=RuntimeError('cache unavailable'))
        with self.assertRaisesRegex(RuntimeError, 'cache unavailable'):
            self.service._fetch_realtime_many(['B'])
        self.assertEqual(self.source.call_count, 3)


class SimulatedMySQL(MarketCalcRepository):
    """Process-shared advisory-lock simulator, never opens a network socket."""
    def __init__(self, shared_lock):
        super().__init__({'host': 'test.invalid', 'user': 'test', 'database': 'test'})
        self.shared_lock = shared_lock
        self.held = False
        self.open = False
        self.statements = []

    @contextmanager
    def connection(self):
        self.open = True
        try: yield self
        finally: self.open = False

    def cursor(self): return self
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def execute(self, sql, args):
        self.statements.append(sql)
        if 'GET_LOCK' in sql:
            self.held = self.shared_lock.acquire(False)
            self.result = int(self.held)
        elif 'IS_USED_LOCK' in sql:
            self.result = int(self.held)
        elif 'RELEASE_LOCK' in sql:
            self.shared_lock.release()
            self.held, self.result = False, 1
    def fetchone(self): return (self.result,)


def _guard_process(shared_lock, hold, release, results):
    repository = SimulatedMySQL(shared_lock)
    try:
        with heavy_work(repository):
            check_owner()
            results.put('owner')
            hold.set()
            release.wait(4)
    except BackgroundBusy:
        results.put('busy')


class BackgroundWorkTests(unittest.TestCase):
    def test_two_processes_cannot_run_heavy_work_together(self):
        ctx = multiprocessing.get_context('fork')
        lock, hold, release, results = ctx.Lock(), ctx.Event(), ctx.Event(), ctx.Queue()
        first = ctx.Process(target=_guard_process, args=(lock, hold, release, results))
        second = ctx.Process(target=_guard_process, args=(lock, hold, release, results))
        first.start()
        try:
            self.assertTrue(hold.wait(2))
            second.start()
            second.join(3)
            self.assertFalse(second.is_alive())
            self.assertEqual({results.get(timeout=1), results.get(timeout=1)}, {'owner', 'busy'})
        finally:
            release.set()
            first.join(3)
            if first.is_alive(): first.terminate(); first.join()
            if second.pid and second.is_alive(): second.terminate(); second.join()
            results.close()
        self.assertEqual(first.exitcode, 0)
        # Completion released the shared lock; the next scheduled task can run.
        with heavy_work(SimulatedMySQL(lock)):
            check_owner()

    def test_nested_work_reuses_connection_and_exception_releases(self):
        repository = SimulatedMySQL(threading.Lock())
        with self.assertRaisesRegex(ValueError, 'test failure'):
            with heavy_work(repository):
                self.assertTrue(repository.open)
                with heavy_work(repository): check_owner()
                raise ValueError('test failure')
        self.assertFalse(repository.open)
        self.assertEqual(sum('GET_LOCK' in s for s in repository.statements), 1)
        self.assertEqual(sum('RELEASE_LOCK' in s for s in repository.statements), 1)
        with heavy_work(repository): pass

    def test_lost_background_ownership_fails_before_write(self):
        repository = SimulatedMySQL(threading.Lock())
        with heavy_work(repository):
            repository.held = False
            with self.assertRaisesRegex(RuntimeError, 'ownership lost'):
                check_owner()
        with heavy_work(repository): check_owner()

    def test_startup_instances_reuse_success_for_ten_minutes_then_resync(self):
        repository = SimulatedMySQL(threading.Lock())
        repository.upsert_stock_basic = Mock()
        state = {}
        shared = SimpleNamespace(states=lambda: state,
            set_state=lambda key, value: state.update({key: str(value)}))
        service = MarketService()
        self.addCleanup(service.close)
        service._calc_repository = repository
        service._stock_rows = Mock(return_value=[{'ts_code': 'A'}])
        now = service._now()
        with patch('server.data.publication_store.PublicationStore', return_value=shared):
            service._sync_stock_basic_on_startup()
            service._sync_stock_basic_on_startup()
            self.assertEqual(service._stock_rows.call_count, 1)
            from datetime import timedelta
            service.set_clock(lambda: now + timedelta(seconds=601))
            service._sync_stock_basic_on_startup()
            self.assertEqual(service._stock_rows.call_count, 2)
            self.assertEqual(repository.upsert_stock_basic.call_count, 2)

    def test_consumed_market_day_releases_client_cache_only_for_that_day(self):
        from .tushare_client import TushareClient
        client = TushareClient()
        consumed = 'daily:{"trade_date": "20261112"}'
        other = 'daily:{"trade_date": "20261113"}'
        client._cache = {consumed: (0, object()), other: (0, object()), 'trade_cal': (0, object())}
        client.release_daily_cache('20261112')
        self.assertNotIn(consumed, client._cache)
        self.assertIn(other, client._cache)
        self.assertIn('trade_cal', client._cache)

    def test_busy_scheduler_does_not_consume_attempt_or_fail_monitor(self):
        service = MarketService()
        self.addCleanup(service.close)
        service._calc_repository = SimpleNamespace(available=True)
        monitor = object.__new__(OfficialMonitorService)
        monitor.repository = SimpleNamespace(states=lambda: {}, active_snapshot=lambda: {})
        monitor._clock = service._now
        monitor.refreshing = False
        entered, release = threading.Event(), threading.Event()
        def occupy():
            with heavy_work(None):
                entered.set(); release.wait(2)
        with ThreadPoolExecutor(max_workers=1) as pool:
            owner = pool.submit(occupy)
            self.assertTrue(entered.wait(1))
            marker = service._market_last_formal_attempt
            self.assertFalse(service._market_scheduler_tick())
            self.assertEqual(service._market_last_formal_attempt, marker)
            self.assertTrue(service.update_market_data()['deferred'])
            self.assertFalse(service.rebuild_predictions_from_existing_bases())
            self.assertTrue(monitor.refresh()['deferred'])
            self.assertTrue(monitor._background_refresh_pending)
            release.set(); owner.result()
        monitor._refresh_owned = lambda: {'started': True}
        self.assertTrue(monitor.refresh()['started'])
        self.assertFalse(monitor._background_refresh_pending)

    def test_deferred_monitor_reuses_peer_success_instead_of_collecting_again(self):
        from datetime import datetime
        from .monitor_service import MONITOR_RULE_VERSION
        state = {'last_status': 'success', 'last_attempt_date': '2026-11-13',
                 'monitor_rule_version': MONITOR_RULE_VERSION, 'coverage_retry_after': ''}
        monitor = object.__new__(OfficialMonitorService)
        monitor.repository = SimpleNamespace(states=lambda: state,
            active_snapshot=lambda: {"updatedAt": "2026-11-13 00:05:00"})
        monitor._clock = lambda: datetime(2026, 11, 13, 0, 10)
        monitor._background_refresh_pending = True
        monitor._refresh_owned = Mock(return_value={'started': True})
        self.assertTrue(monitor.refresh()['reused'])
        monitor._refresh_owned.assert_not_called()
        self.assertFalse(monitor._background_refresh_pending)
        # An incomplete collection due for retry still runs, even on the same date.
        state['coverage_retry_after'] = '202611130009'
        monitor._background_refresh_pending = True
        self.assertTrue(monitor.refresh()['started'])
        monitor._refresh_owned.assert_called_once()
        # A crashed attempt may have today's attempt date but yesterday's success.
        # It must not be mistaken for a newly completed peer collection.
        state['coverage_retry_after'] = ''
        monitor.repository.active_snapshot = lambda: {"updatedAt": "2026-11-12 00:05:00"}
        monitor._background_refresh_pending = True
        self.assertTrue(monitor.refresh()['started'])
        self.assertEqual(monitor._refresh_owned.call_count, 2)

    def test_notice_batches_bound_workers_preserve_order_and_fail_independently(self):
        monitor = object.__new__(OfficialMonitorService)
        monitor.announcements = object.__new__(AnnouncementService)
        active, peak = 0, 0
        mutex = threading.Lock()
        def extract(url):
            nonlocal active, peak
            with mutex:
                active += 1; peak = max(active, peak)
            time.sleep(.005)
            with mutex: active -= 1
            if url == '3': raise RuntimeError('read failed')
            return {'text': url, 'method': 'text_layer'}
        monitor.announcements.extract_pdf_body = extract
        rows = [{'url': str(i)} for i in range(11)]
        result = list(monitor._issuer_bodies(rows))
        self.assertEqual([row for row, _ in result], rows)
        self.assertEqual(result[3][1]['method'], 'unavailable')
        self.assertEqual(result[10][1]['text'], '10')
        self.assertLessEqual(peak, 2)

    def test_s2_two_workers_four_slots_keep_fifth_job_queued(self):
        release, entered = threading.Event(), threading.Event()
        mutex, active, peak, started = threading.Lock(), 0, 0, []
        def refresh(context):
            nonlocal active, peak
            with mutex:
                active += 1; peak = max(peak, active); started.append(context)
                if active == 2: entered.set()
            release.wait(3)
            with mutex: active -= 1
            return {}, 'view'
        claimed = []
        def job(context, identity=None, claim=False):
            claimed.append(claim)
            return {'job_id': str(context), 'owner': 'worker' if claim else '', 'status': 'queued'}
        store = SimpleNamespace(refresh_job=job, complete_refresh_job=lambda *_: None)
        jobs = ReadRefreshJobs(SimpleNamespace(refresh=refresh), store)
        try:
            for index in range(5): jobs.request(index)
            self.assertTrue(entered.wait(1))
            self.assertEqual(claimed, [True, True, True, True, False])
            self.assertEqual(peak, 2)
        finally:
            release.set(); jobs.close()
        self.assertEqual(sorted(started), [0, 1, 2, 3])
