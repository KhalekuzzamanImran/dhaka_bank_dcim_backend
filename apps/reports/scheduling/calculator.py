from __future__ import annotations

from datetime import datetime, time as time_cls


def _normalize_time(value):
    if isinstance(value, time_cls):
        return value
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        for fmt in ("%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p"):
            try:
                return datetime.strptime(candidate, fmt).time()
            except ValueError:
                continue
    return None


def calculate_next_run(*, recurrence_rule, after, start_at=None, end_at=None):
    from apps.reports.models import ReportSchedule

    delivery_time = _normalize_time((recurrence_rule or {}).get("delivery_time")) or _normalize_time((recurrence_rule or {}).get("time")) or "06:00:00"
    if delivery_time == "06:00:00":
        delivery_time = datetime.strptime("06:00:00", "%H:%M:%S").time()
    schedule = ReportSchedule(
        organization_id=None,
        name="__temporary__",
        report_type=(recurrence_rule or {}).get("report_type") or "device_inventory",
        frequency=(recurrence_rule or {}).get("frequency") or "DAILY",
        delivery_time=delivery_time,
        recurrence_rule=recurrence_rule or {},
        status="ACTIVE",
        is_active=True,
        start_at=start_at,
        end_at=end_at,
    )
    return schedule.calculate_next_run_at(reference_time=after)


def calculate_next_runs(*, recurrence_rule, after, start_at=None, end_at=None, count: int = 5):
    runs = []
    current = after
    for _ in range(count):
        next_run = calculate_next_run(
            recurrence_rule=recurrence_rule,
            after=current,
            start_at=start_at,
            end_at=end_at,
        )
        if not next_run:
            break
        runs.append(next_run)
        current = next_run
    return runs
