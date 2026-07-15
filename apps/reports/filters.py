from __future__ import annotations

import django_filters

from .models import ReportJob, ReportSchedule


class ReportJobFilter(django_filters.FilterSet):
    created_at = django_filters.DateTimeFromToRangeFilter()
    completed_at = django_filters.DateTimeFromToRangeFilter()

    class Meta:
        model = ReportJob
        fields = [
            "status",
            "template",
            "organization",
            "data_center",
            "requested_by",
        ]


class ReportScheduleFilter(django_filters.FilterSet):
    next_run_at = django_filters.DateTimeFromToRangeFilter()
    last_run_at = django_filters.DateTimeFromToRangeFilter()

    class Meta:
        model = ReportSchedule
        fields = [
            "organization",
            "data_center",
            "report_type",
            "frequency",
            "output_format",
            "is_active",
            "last_delivery_status",
            "created_by",
        ]
