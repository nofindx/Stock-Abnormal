"""Nonblocking heavy-work admission; reads and S2 never acquire this lock.

One process slot and one shared MySQL advisory lock serialize background
writers. The dedicated connection stays open until the whole workflow exits.
Nested workflows on the owning thread reuse the lock (notice -> outbox ->
predictions), avoiding both deadlocks and gaps between update and publish.
"""
from contextlib import contextmanager
from functools import wraps
from threading import Lock, local
from .market_calc_repository import MarketCalcRepository


class BackgroundBusy(RuntimeError):
    pass


_process_slot = Lock()
_owner = local()
LOCK_NAME = "stock-abnormal:background:heavy:v1"


@contextmanager
def heavy_work(repository):
    if getattr(_owner, "active", False):
        yield
        return
    if not _process_slot.acquire(blocking=False):
        raise BackgroundBusy("background workflow already running")
    try:
        # PublicationStore wraps the authoritative calculation repository.
        if getattr(repository, "sqlite_path", None):
            repository = None
        else:
            repository = getattr(repository, "repository", repository)
        if isinstance(repository, MarketCalcRepository) and repository.available:
            with repository.connection() as db:
                acquired = False
                try:
                    with db.cursor() as cursor:
                        cursor.execute("SELECT GET_LOCK(%s, 0)", (LOCK_NAME,))
                        result = cursor.fetchone()[0]
                    if result is None:
                        raise RuntimeError("background shared lock unavailable")
                    if not result:
                        raise BackgroundBusy("another instance owns background workflow")
                    acquired = True
                    _owner.active = True
                    _owner.connection = db
                    yield
                finally:
                    _owner.active = False
                    _owner.connection = None
                    if acquired:
                        with db.cursor() as cursor:
                            cursor.execute("SELECT RELEASE_LOCK(%s)", (LOCK_NAME,))
        else:  # explicit local/test adapters have process-only coordination
            _owner.active = True
            try:
                yield
            finally:
                _owner.active = False
    finally:
        _process_slot.release()


def check_owner():
    """Fail closed before writes if the dedicated lock connection was lost."""
    db = getattr(_owner, "connection", None)
    if db is not None:
        with db.cursor() as cursor:
            cursor.execute("SELECT IS_USED_LOCK(%s) = CONNECTION_ID()", (LOCK_NAME,))
            if cursor.fetchone()[0] != 1:
                raise RuntimeError("background shared lock ownership lost")


def background_task(*, update=False, retry=False):
    """Startup work retries; scheduled work defers without consuming its slot."""
    def decorate(function):
        @wraps(function)
        def run(self, *args, **kwargs):
            while True:
                try:
                    with heavy_work(self._calc_repository):
                        return function(self, *args, **kwargs)
                except BackgroundBusy:
                    if not retry or self._market_scheduler_stop.wait(60):
                        return {"updated": False, "deferred": True, "reason": "background_busy"} if update else False
        return run
    return decorate
