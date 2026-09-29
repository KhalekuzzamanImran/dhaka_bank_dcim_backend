from __future__ import annotations

import json
import logging
import time
from contextvars import ContextVar

from django.conf import settings
from django.db import connection


logger = logging.getLogger("dcim.query_monitoring")
_explain_running = ContextVar("dcim_explain_running", default=False)


def _setting(name, default):
    return getattr(settings, name, default)


def _compact_sql(sql: str) -> str:
    return " ".join(str(sql).split())[: int(_setting("QUERY_MONITORING_MAX_SQL_LENGTH", 4000))]


def _should_explain(request, sql: str, elapsed_ms: float) -> bool:
    if not _setting("QUERY_MONITORING_EXPLAIN_ENABLED", False):
        return False
    if _explain_running.get() or elapsed_ms < float(_setting("QUERY_MONITORING_EXPLAIN_SLOW_MS", 250)):
        return False
    if not sql.lstrip().upper().startswith("SELECT"):
        return False
    paths = tuple(_setting("QUERY_MONITORING_EXPLAIN_PATHS", ()))
    return not paths or any(request.path.startswith(path) for path in paths)


class QueryMonitoringMiddleware:
    """Collect request query timings and optionally explain slow SELECTs.

    EXPLAIN ANALYZE is deliberately opt-in because it executes the SELECT a
    second time and can add material overhead to a production request.
    Parameters are never logged; the SQL remains parameterized for the plan.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not _setting("QUERY_MONITORING_ENABLED", False):
            return self.get_response(request)

        query_count = 0
        query_time_ms = 0.0
        slow_queries = 0
        explain_count = 0

        def execute_wrapper(execute, sql, params, many, context):
            nonlocal query_count, query_time_ms, slow_queries, explain_count
            started = time.perf_counter()
            result = None
            try:
                result = execute(sql, params, many, context)
            except BaseException:
                raise
            finally:
                elapsed_ms = (time.perf_counter() - started) * 1000
                query_count += 1
                query_time_ms += elapsed_ms
                slow_threshold = float(_setting("QUERY_MONITORING_SLOW_MS", 100))
                if elapsed_ms < slow_threshold:
                    pass
                else:
                    slow_queries += 1
                    compact_sql = _compact_sql(sql)
                    logger.warning(
                        "slow_db_query path=%s method=%s duration_ms=%.2f sql=%s",
                        request.path,
                        request.method,
                        elapsed_ms,
                        compact_sql,
                    )

                    if _should_explain(request, sql, elapsed_ms):
                        token = _explain_running.set(True)
                        try:
                            with connection.cursor() as cursor:
                                cursor.execute(
                                    f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}",
                                    params,
                                )
                                plan = cursor.fetchone()[0]
                            explain_count += 1
                            logger.warning(
                                "slow_db_query_plan path=%s duration_ms=%.2f plan=%s",
                                request.path,
                                elapsed_ms,
                                json.dumps(plan, default=str, separators=(",", ":")),
                            )
                        except Exception:
                            logger.exception("slow_db_query_explain_failed path=%s", request.path)
                        finally:
                            _explain_running.reset(token)
            return result

        started = time.perf_counter()
        with connection.execute_wrapper(execute_wrapper):
            response = self.get_response(request)
        request_time_ms = (time.perf_counter() - started) * 1000

        logger.info(
            "request_db_summary path=%s method=%s status=%s request_ms=%.2f query_count=%d query_ms=%.2f slow_queries=%d explains=%d",
            request.path,
            request.method,
            getattr(response, "status_code", "unknown"),
            request_time_ms,
            query_count,
            query_time_ms,
            slow_queries,
            explain_count,
        )
        response["X-DB-Query-Count"] = str(query_count)
        response["X-DB-Query-Time-Ms"] = f"{query_time_ms:.2f}"
        response["X-Request-Time-Ms"] = f"{request_time_ms:.2f}"
        return response
