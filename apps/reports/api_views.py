from __future__ import annotations

from django.core.exceptions import ValidationError
from django.conf import settings
from django.db import transaction
from django.db.models import Prefetch
from django.http import Http404
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.audit import write_audit
from apps.common.access import filter_queryset_for_user
from apps.common.permissions import DCIMRBACPermission
from apps.common.viewsets import ScopedModelViewSet
from apps.notifications.models import NotificationChannel

from .api_serializers import (
    ReportArtifactSerializer,
    ReportDefinitionSchemaSerializer,
    ReportDefinitionSerializer,
    ReportDeliverySerializer,
    ReportJobDetailSerializer,
    ReportJobListSerializer,
    ReportScheduleReadSerializer,
    ReportScheduleRunSerializer,
    ReportScheduleWriteSerializer,
    ReportTemplateReadSerializer,
    ReportTemplateWriteSerializer,
)
from .constants import REPORT_SCHEDULE_FREQUENCY_CHOICES
from .enums import (
    ReportArtifactFormat,
    ReportDefinitionCategory,
    ReportDeliveryStatus,
    ReportJobStatusV2,
    ReportRecipientChannel,
    ReportScheduleStatus,
    ReportTriggerSource,
)
from .models import (
    ReportArtifact,
    ReportDefinition,
    ReportDelivery,
    ReportJob,
    ReportJobStatus,
    ReportSchedule,
    ReportScheduleRun,
    ReportScheduleStatus as LegacyReportScheduleStatus,
    ReportTemplate,
)
from .services.definitions import build_definition_capabilities
from .services.deliveries import report_delivery_summary, retry_report_delivery
from .services.downloads import download_report_artifact
from .services.factory import create_report_job
from .services.permissions import report_job_allowed_actions
from .services.schedules import execute_report_schedule
from .services.selectors import get_active_definitions_queryset
from .services.templates import create_report_template, update_report_template


def _safe_write_audit(*args, **kwargs):
    try:
        return write_audit(*args, **kwargs)
    except Exception:
        return None


def _refresh_template(template):
    return (
        ReportTemplate.objects.select_related("organization", "definition", "updated_by")
        .filter(pk=template.pk)
        .first()
    )


def _refresh_schedule(schedule):
    return (
        ReportSchedule.objects.select_related(
            "organization",
            "data_center",
            "template",
            "template__definition",
            "created_by",
            "updated_by",
            "last_job",
        )
        .prefetch_related("structured_recipients")
        .filter(pk=schedule.pk)
        .first()
    )


def _job_queryset():
    return (
        ReportJob.objects.select_related(
            "organization",
            "data_center",
            "definition",
            "template",
            "template__definition",
            "schedule",
            "requested_by",
        )
        .prefetch_related("artifacts", "deliveries")
        .all()
        .order_by("-created_at")
    )


class ReportDefinitionViewSet(viewsets.ReadOnlyModelViewSet):
    lookup_field = "code"
    lookup_url_kwarg = "code"
    queryset = get_active_definitions_queryset()
    serializer_class = ReportDefinitionSerializer
    permission_classes = [DCIMRBACPermission]
    permission_module = "report"
    search_fields = ["code", "name", "description"]
    ordering_fields = ["category", "name", "code", "created_at", "updated_at", "version"]
    ordering = ["category", "name"]

    def get_queryset(self):
        return get_active_definitions_queryset()

    @action(detail=True, methods=["get"], url_path="parameter-schema")
    def parameter_schema(self, request, code=None):
        definition = self.get_object()
        serializer = ReportDefinitionSchemaSerializer(instance=definition, context=self.get_serializer_context())
        data = serializer.data
        data["parameter_schema"] = definition.parameter_schema or {}
        return Response(data)


class ReportTemplateViewSet(ScopedModelViewSet):
    access_scope = "organization"
    organization_field = "organization"
    queryset = ReportTemplate.objects.select_related("organization", "definition", "updated_by").all().order_by("-created_at")
    permission_classes = [DCIMRBACPermission]
    permission_module = "report"
    serializer_class = ReportTemplateReadSerializer
    search_fields = ["name", "code", "description", "definition__code", "definition__name"]
    ordering_fields = ["created_at", "updated_at", "name", "code", "version", "is_active"]
    ordering = ["-created_at"]
    filterset_fields = ["organization", "definition", "is_active"]

    def get_serializer_class(self):
        if self.action in {"create", "update", "partial_update"}:
            return ReportTemplateWriteSerializer
        return ReportTemplateReadSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        return context

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        read_serializer = ReportTemplateReadSerializer(_refresh_template(instance), context=self.get_serializer_context())
        _safe_write_audit(
            "CREATE",
            "ReportTemplate",
            instance.pk,
            organization=instance.organization,
            actor=request.user,
            new_value=read_serializer.data,
            message="Report template created.",
        )
        headers = self.get_success_headers(read_serializer.data)
        return Response(read_serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        read_serializer = ReportTemplateReadSerializer(_refresh_template(instance), context=self.get_serializer_context())
        _safe_write_audit(
            "UPDATE",
            "ReportTemplate",
            instance.pk,
            organization=instance.organization,
            actor=request.user,
            new_value=read_serializer.data,
            message="Report template updated.",
        )
        return Response(read_serializer.data)

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        template = self.get_object()
        if template.report_jobs.exists() or template.schedules.exists():
            template.is_active = False
            template.save(update_fields=["is_active", "updated_at"])
            _safe_write_audit(
                "REPORT_TEMPLATE_DISABLED",
                "ReportTemplate",
                template.pk,
                organization=template.organization,
                actor=request.user,
                message="Report template disabled instead of deleted because historical jobs exist.",
            )
            return Response(status=status.HTTP_204_NO_CONTENT)
        _safe_write_audit(
            "DELETE",
            "ReportTemplate",
            template.pk,
            organization=template.organization,
            actor=request.user,
            message="Report template deleted.",
        )
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=["post"])
    def generate(self, request, pk=None):
        template = self.get_object()
        if not template.is_active:
            return Response({"detail": "Only active templates can be generated."}, status=status.HTTP_400_BAD_REQUEST)
        parameters = request.data.get("parameters", {}) if isinstance(request.data, dict) else {}
        if parameters is None:
            parameters = {}
        if not isinstance(parameters, dict):
            return Response({"parameters": ["Parameters must be a dictionary/object."]}, status=status.HTTP_400_BAD_REQUEST)
        result = create_report_job(
            definition=template.definition,
            organization=template.organization,
            actor=request.user,
            data_center=None,
            template=template,
            schedule=None,
            trigger_source=ReportTriggerSource.MANUAL,
            requested_by=request.user,
            parameters=parameters,
            runtime_parameters={},
            recipients=None,
            source_event={},
            queue_job=True,
        )
        _safe_write_audit(
            "REPORT_GENERATION_REQUESTED",
            "ReportTemplate",
            template.pk,
            organization=template.organization,
            actor=request.user,
            message=f"Report generation requested for {template.code}.",
        )
        serializer = ReportJobDetailSerializer(result.job, context=self.get_serializer_context())
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class ReportScheduleViewSet(ScopedModelViewSet):
    access_scope = "mixed"
    organization_field = "organization"
    data_center_field = "data_center"
    queryset = ReportSchedule.objects.select_related(
        "organization",
        "data_center",
        "template",
        "template__definition",
        "created_by",
        "updated_by",
        "last_job",
    ).prefetch_related("structured_recipients").all().order_by("-created_at")
    permission_classes = [DCIMRBACPermission]
    permission_module = "report"
    serializer_class = ReportScheduleReadSerializer
    search_fields = ["name", "template__name", "template__code", "template__definition__code", "organization__name", "last_error_message"]
    ordering_fields = ["created_at", "updated_at", "next_run_at", "last_run_at", "last_success_at", "last_failure_at", "name", "status", "frequency"]
    ordering = ["-created_at"]
    filterset_fields = ["organization", "data_center", "template", "template__definition", "status", "frequency", "primary_format"]

    def get_serializer_class(self):
        if self.action in {"create", "update", "partial_update"}:
            return ReportScheduleWriteSerializer
        return ReportScheduleReadSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        return context

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        read_serializer = ReportScheduleReadSerializer(_refresh_schedule(instance), context=self.get_serializer_context())
        _safe_write_audit(
            "CREATE",
            "ReportSchedule",
            instance.pk,
            organization=instance.organization,
            actor=request.user,
            new_value=read_serializer.data,
            message="Report schedule created.",
        )
        headers = self.get_success_headers(read_serializer.data)
        return Response(read_serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        read_serializer = ReportScheduleReadSerializer(_refresh_schedule(instance), context=self.get_serializer_context())
        _safe_write_audit(
            "UPDATE",
            "ReportSchedule",
            instance.pk,
            organization=instance.organization,
            actor=request.user,
            new_value=read_serializer.data,
            message="Report schedule updated.",
        )
        return Response(read_serializer.data)

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        schedule = self.get_object()
        schedule.status = ReportScheduleStatus.DISABLED
        schedule.save(update_fields=["status", "updated_at"])
        _safe_write_audit(
            "REPORT_SCHEDULE_DISABLED",
            "ReportSchedule",
            schedule.pk,
            organization=schedule.organization,
            actor=request.user,
            message="Report schedule disabled.",
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"], url_path="run-now")
    def run_now(self, request, pk=None):
        schedule = self.get_object()
        schedule.last_delivery_status = "PENDING"
        schedule.last_error_message = ""
        schedule.save(update_fields=["last_delivery_status", "last_error_message", "updated_at"])

        from .tasks import deliver_report_schedule_task

        def _queue_delivery():
            deliver_report_schedule_task.delay(str(schedule.pk), None, None, "MANUAL")
            _safe_write_audit(
                "REPORT_SCHEDULE_RUN_NOW_QUEUED",
                "ReportSchedule",
                schedule.pk,
                organization=schedule.organization,
                actor=request.user,
                message=f"Manual report delivery queued for {getattr(getattr(schedule.template, 'definition', None), 'name', None) or getattr(schedule.template, 'name', None) or schedule.name}.",
            )

        transaction.on_commit(_queue_delivery)
        refreshed = _refresh_schedule(schedule) or schedule
        serializer = self.get_serializer(refreshed)
        payload = serializer.data
        payload["detail"] = "Report delivery queued."
        return Response(payload, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=["post"])
    def pause(self, request, pk=None):
        schedule = self.get_object()
        schedule.status = ReportScheduleStatus.PAUSED
        schedule.save(update_fields=["status", "updated_at"])
        _safe_write_audit("REPORT_SCHEDULE_PAUSED", "ReportSchedule", schedule.pk, organization=schedule.organization, actor=request.user, message="Report schedule paused.")
        return Response(self.get_serializer(schedule).data)

    @action(detail=True, methods=["post"])
    def resume(self, request, pk=None):
        schedule = self.get_object()
        schedule.status = ReportScheduleStatus.ACTIVE
        schedule.save(update_fields=["status", "updated_at"])
        _safe_write_audit("REPORT_SCHEDULE_RESUMED", "ReportSchedule", schedule.pk, organization=schedule.organization, actor=request.user, message="Report schedule resumed.")
        return Response(self.get_serializer(schedule).data)

    @action(detail=True, methods=["post"])
    def disable(self, request, pk=None):
        schedule = self.get_object()
        schedule.status = ReportScheduleStatus.DISABLED
        schedule.save(update_fields=["status", "updated_at"])
        _safe_write_audit("REPORT_SCHEDULE_DISABLED", "ReportSchedule", schedule.pk, organization=schedule.organization, actor=request.user, message="Report schedule disabled.")
        return Response(self.get_serializer(schedule).data)

    @action(detail=True, methods=["get"])
    def runs(self, request, pk=None):
        schedule = self.get_object()
        runs = schedule.runs.select_related("schedule", "job", "requested_by").prefetch_related("deliveries", "job__artifacts").order_by("-created_at")[:10]
        serializer = ReportScheduleRunSerializer(runs, many=True, context=self.get_serializer_context())
        return Response(serializer.data)

    @action(detail=True, methods=["get"])
    def deliveries(self, request, pk=None):
        schedule = self.get_object()
        deliveries = (
            ReportDelivery.objects.select_related(
                "job",
                "job__template",
                "job__definition",
                "job__organization",
                "job__data_center",
                "schedule_recipient",
            )
            .filter(job__schedule=schedule)
            .order_by("-created_at")
        )
        serializer = ReportDeliverySerializer(deliveries, many=True, context=self.get_serializer_context())
        return Response(serializer.data)

    @action(detail=True, methods=["get"], url_path="preview-next-runs")
    def preview_next_runs(self, request, pk=None):
        schedule = self.get_object()
        count = request.query_params.get("count", 3)
        try:
            count = max(1, min(10, int(count)))
        except Exception:
            count = 3

        next_runs = []
        reference = schedule.next_run_at or timezone.now()
        for _ in range(count):
            next_run = schedule.calculate_next_run_at(reference_time=reference)
            window_start, window_end = schedule.calculate_execution_window(reference_time=next_run)
            next_runs.append(
                {
                    "scheduled_for": next_run.isoformat() if next_run else None,
                    "window_start": window_start.isoformat() if window_start else None,
                    "window_end": window_end.isoformat() if window_end else None,
                }
            )
            reference = next_run
        return Response({"results": next_runs})


class ReportScheduleRunViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [DCIMRBACPermission]
    permission_module = "report"
    queryset = ReportScheduleRun.objects.select_related(
        "schedule",
        "organization",
        "requested_by",
        "job",
        "schedule__template",
        "schedule__template__definition",
    ).prefetch_related("deliveries", "job__artifacts").all().order_by("-created_at")
    serializer_class = ReportScheduleRunSerializer
    search_fields = ["schedule__name", "schedule__template__definition__code", "schedule__template__code", "schedule__template__name", "error_message", "trigger_source"]
    ordering_fields = ["created_at", "updated_at", "queued_at", "started_at", "completed_at", "status", "window_start", "window_end"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return filter_queryset_for_user(
            super().get_queryset(),
            self.request.user,
            access_scope="organization",
            organization_field="organization",
        )

    @action(detail=True, methods=["get"])
    def deliveries(self, request, pk=None):
        run = self.get_object()
        job = run.job
        deliveries = job.deliveries.select_related("job", "schedule_recipient") if job else ReportDelivery.objects.none()
        serializer = ReportDeliverySerializer(deliveries.order_by("created_at"), many=True, context=self.get_serializer_context())
        return Response(serializer.data)


class ReportJobViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [DCIMRBACPermission]
    permission_module = "report"
    queryset = _job_queryset()
    serializer_class = ReportJobListSerializer
    search_fields = ["template__name", "template__code", "requested_by__username", "requested_by__email", "error_message"]
    ordering_fields = ["created_at", "updated_at", "started_at", "completed_at", "status"]
    ordering = ["-created_at"]
    filterset_fields = ["organization", "data_center", "definition", "template", "schedule", "trigger_source", "status"]

    def get_queryset(self):
        return filter_queryset_for_user(
            super().get_queryset(),
            self.request.user,
            access_scope="mixed",
            organization_field="organization",
            data_center_field="data_center",
        )

    def get_serializer_class(self):
        if self.action == "retrieve":
            return ReportJobDetailSerializer
        return ReportJobListSerializer

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        job = self.get_object()
        if job.status != ReportJobStatus.PENDING:
            return Response({"detail": "Only pending jobs can be cancelled."}, status=status.HTTP_400_BAD_REQUEST)
        job.status = ReportJobStatus.CANCELLED
        job.started_at = job.started_at or timezone.now()
        job.completed_at = timezone.now()
        job.error_message = "Cancelled by user"
        job.save(update_fields=["status", "started_at", "completed_at", "error_message", "updated_at"])
        _safe_write_audit("REPORT_CANCELLED", "ReportJob", job.pk, organization=job.organization, actor=request.user, message="Report job cancelled by user")
        return Response(ReportJobDetailSerializer(job, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        job = self.get_object()
        if job.status != ReportJobStatus.FAILED:
            return Response({"detail": "Only failed jobs can be retried."}, status=status.HTTP_400_BAD_REQUEST)
        job.status = ReportJobStatus.PENDING
        job.error_message = ""
        job.started_at = None
        job.completed_at = None
        job.save(update_fields=["status", "error_message", "started_at", "completed_at", "updated_at"])
        from .tasks import generate_report_job_task

        def _queue_generation():
            generate_report_job_task.delay(str(job.pk))
            _safe_write_audit(
                "REPORT_RETRY_REQUESTED",
                "ReportJob",
                job.pk,
                organization=job.organization,
                actor=request.user,
                message="Report retry queued.",
            )

        transaction.on_commit(_queue_generation)
        return Response(ReportJobDetailSerializer(job, context=self.get_serializer_context()).data)


class ReportArtifactViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [DCIMRBACPermission]
    permission_module = "report"
    queryset = ReportArtifact.objects.select_related("job", "job__organization", "job__data_center", "job__template", "job__definition").all().order_by("-created_at")
    serializer_class = ReportArtifactSerializer
    search_fields = ["original_filename", "checksum_sha256", "job__template__name", "job__template__code"]
    ordering_fields = ["created_at", "size_bytes", "format"]
    ordering = ["-created_at"]
    filterset_fields = ["job", "format"]

    def get_queryset(self):
        return filter_queryset_for_user(
            super().get_queryset(),
            self.request.user,
            access_scope="mixed",
            organization_field="job__organization",
            data_center_field="job__data_center",
        )

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        artifact = self.get_object()
        try:
            return download_report_artifact(artifact, request=request)
        except Http404:
            return Response({"detail": "This artifact is not available for download."}, status=status.HTTP_404_NOT_FOUND)


class ReportDeliveryViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [DCIMRBACPermission]
    permission_module = "report"
    queryset = ReportDelivery.objects.select_related(
        "job",
        "job__organization",
        "job__data_center",
        "job__template",
        "job__definition",
        "schedule_recipient",
    ).all().order_by("-created_at")
    serializer_class = ReportDeliverySerializer
    search_fields = ["recipient", "provider_message_id", "error_message", "job__template__name", "job__template__code"]
    ordering_fields = ["created_at", "queued_at", "started_at", "sent_at", "failed_at", "status", "channel", "retry_count"]
    ordering = ["-created_at"]
    filterset_fields = ["job", "channel", "status"]

    def get_queryset(self):
        return filter_queryset_for_user(
            super().get_queryset(),
            self.request.user,
            access_scope="mixed",
            organization_field="job__organization",
            data_center_field="job__data_center",
        )

    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        delivery = self.get_object()
        try:
            result = retry_report_delivery(delivery=delivery, requested_by=request.user)
        except ValidationError as exc:
            if hasattr(exc, "message_dict"):
                raise DRFValidationError(exc.message_dict)
            raise DRFValidationError(getattr(exc, "messages", [str(exc)]))
        return Response(ReportDeliverySerializer(result, context=self.get_serializer_context()).data)


class ReportOptionsAPIView(APIView):
    permission_classes = [DCIMRBACPermission]
    permission_module = "report"

    def get(self, request):
        definitions = ReportDefinitionSerializer(get_active_definitions_queryset(), many=True, context={"request": request}).data
        payload = {
            "categories": [{"code": code, "label": label} for code, label in ReportDefinitionCategory.choices],
            "definitions": definitions,
            "supported_formats": [{"code": value, "label": value} for value in ReportArtifactFormat.values],
            "schedule_frequencies": [{"code": value, "label": label} for value, label in REPORT_SCHEDULE_FREQUENCY_CHOICES],
            "schedule_statuses": [{"code": value, "label": label} for value, label in ReportScheduleStatus.choices],
            "trigger_sources": [{"code": value, "label": label} for value, label in ReportTriggerSource.choices],
            "job_statuses": [{"code": value, "label": label} for value, label in ReportJobStatusV2.choices],
            "delivery_channels": [{"code": value, "label": label} for value, label in NotificationChannel.choices],
            "delivery_statuses": [{"code": value, "label": label} for value, label in ReportDeliveryStatus.choices],
            "timezone_default": "Asia/Dhaka",
            "retention_defaults": {"artifact_retention_days": getattr(settings, "REPORT_ARTIFACT_RETENTION_DAYS", 90)},
            "maximum_dashboard_range_days": 365,
            "maximum_export_range_days": 90,
        }
        return Response(payload)
