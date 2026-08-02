from __future__ import annotations

import django_filters

from .models import ReportArtifact, ReportDelivery, ReportDefinition, ReportJob, ReportSchedule, ReportTemplate


class ReportDefinitionFilter(django_filters.FilterSet):
    class Meta:
        model = ReportDefinition
        fields = ["code", "category", "is_active"]


class ReportTemplateFilter(django_filters.FilterSet):
    created_at = django_filters.DateTimeFromToRangeFilter()
    updated_at = django_filters.DateTimeFromToRangeFilter()

    class Meta:
        model = ReportTemplate
        fields = ["organization", "definition", "is_default", "is_active", "created_by"]


class ReportJobFilter(django_filters.FilterSet):
    queued_at = django_filters.DateTimeFromToRangeFilter()
    started_at = django_filters.DateTimeFromToRangeFilter()
    completed_at = django_filters.DateTimeFromToRangeFilter()
    has_artifacts = django_filters.BooleanFilter(method="filter_has_artifacts")
    delivery_status = django_filters.CharFilter(method="filter_delivery_status")

    class Meta:
        model = ReportJob
        fields = [
            "organization",
            "data_center",
            "definition",
            "template",
            "schedule",
            "trigger_source",
            "status",
            "requested_by",
        ]

    def filter_has_artifacts(self, queryset, name, value):
        if value is True:
            return queryset.filter(artifacts__isnull=False).distinct()
        if value is False:
            return queryset.filter(artifacts__isnull=True).distinct()
        return queryset

    def filter_delivery_status(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(deliveries__status=value).distinct()


class ReportScheduleFilter(django_filters.FilterSet):
    next_run_at = django_filters.DateTimeFromToRangeFilter()
    last_run_at = django_filters.DateTimeFromToRangeFilter()
    has_failures = django_filters.BooleanFilter(method="filter_has_failures")

    class Meta:
        model = ReportSchedule
        fields = [
            "organization",
            "data_center",
            "definition",
            "template",
            "status",
            "created_by",
        ]

    def filter_has_failures(self, queryset, name, value):
        if value is True:
            return queryset.filter(consecutive_failure_count__gt=0)
        if value is False:
            return queryset.filter(consecutive_failure_count=0)
        return queryset


class ReportArtifactFilter(django_filters.FilterSet):
    created_at = django_filters.DateTimeFromToRangeFilter()
    expires_at = django_filters.DateTimeFromToRangeFilter()

    class Meta:
        model = ReportArtifact
        fields = [
            "job",
            "artifact_type",
            "format",
            "status",
            "job__organization",
            "job__data_center",
        ]


class ReportDeliveryFilter(django_filters.FilterSet):
    queued_at = django_filters.DateTimeFromToRangeFilter()
    attempted_at = django_filters.DateTimeFromToRangeFilter()
    delivered_at = django_filters.DateTimeFromToRangeFilter()

    class Meta:
        model = ReportDelivery
        fields = [
            "job",
            "schedule",
            "channel",
            "status",
            "recipient_type",
            "job__organization",
            "job__data_center",
        ]
