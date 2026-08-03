from __future__ import annotations

from django.http import Http404
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.views import APIView
from rest_framework.response import Response

from apps.common.permissions import DCIMRBACPermission
from apps.common.audit import write_audit
from apps.common.viewsets import ScopedModelViewSet

from .filters import ReportJobFilter, ReportScheduleFilter
from .models import ReportArtifact, ReportJob, ReportJobStatus, ReportSchedule, ReportScheduleRun, ReportTemplate
from .serializers import (
    ReportArtifactSerializer,
    ReportJobCreateSerializer,
    ReportJobDetailSerializer,
    ReportJobGenerateSerializer,
    ReportJobListSerializer,
    ReportJobRetrySerializer,
    ReportDashboardQuerySerializer,
    ReportDashboardResponseSerializer,
    ReportScheduleDeliverySerializer,
    ReportScheduleSerializer,
    ReportScheduleRunNowSerializer,
    ReportScheduleRunSerializer,
    ReportTemplateSerializer,
)
from .services.downloads import download_report_artifact, download_report_job_artifact
from .services.configuration import build_report_template_options
from .services.dashboard import get_reporting_dashboard


def _safe_write_audit(*args, **kwargs):
    try:
        return write_audit(*args, **kwargs)
    except Exception:
        return None


class ReportTemplateViewSet(ScopedModelViewSet):
    access_scope = "organization"
    organization_field = "organization"
    queryset = ReportTemplate.objects.select_related("organization").all().order_by("-created_at")
    serializer_class = ReportTemplateSerializer
    permission_module = "report"
    audit_resource_type = "ReportTemplate"
    filterset_fields = ["organization", "is_active"]
    search_fields = ["name", "code"]
    ordering_fields = ["created_at", "updated_at", "name", "code"]
    ordering = ["-created_at"]

    @action(detail=True, methods=["get"])
    def options(self, request, pk=None):
        template = self.get_object()
        payload = build_report_template_options(template)
        return Response(payload)


class ReportJobViewSet(ScopedModelViewSet):
    access_scope = "mixed"
    queryset = (
        ReportJob.objects.select_related("organization", "data_center", "template", "requested_by")
        .all()
        .order_by("-created_at")
    )
    serializer_class = ReportJobDetailSerializer
    permission_module = "report"
    audit_resource_type = "ReportJob"
    filterset_class = ReportJobFilter
    search_fields = ["template__name", "template__code", "requested_by__username", "requested_by__email", "error_message"]
    ordering_fields = ["created_at", "updated_at", "started_at", "completed_at", "status"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        if self.action == "list":
            return ReportJobListSerializer
        if self.action == "retrieve":
            return ReportJobDetailSerializer
        if self.action == "create":
            return ReportJobCreateSerializer
        if self.action == "generate":
            return ReportJobGenerateSerializer
        if self.action == "retry":
            return ReportJobRetrySerializer
        if self.action == "cancel":
            return ReportJobGenerateSerializer
        return ReportJobDetailSerializer

    def _refresh_job_from_db(self, job):
        return (
            ReportJob.objects.select_related("organization", "data_center", "template", "requested_by")
            .filter(pk=job.pk)
            .first()
        )

    def _enqueue_generation(self, job, request, *, audit_action: str):
        if not job.requested_by_id and request.user.is_authenticated:
            job.requested_by = request.user
        if job.status == ReportJobStatus.FAILED:
            job.status = ReportJobStatus.PENDING
            job.error_message = ""
            job.started_at = None
            job.completed_at = None
        if job.file:
            job.file.delete(save=False)
            job.file = None
        job.save(update_fields=["requested_by", "status", "error_message", "started_at", "completed_at", "file", "updated_at"])
        from .tasks import generate_report_job_task

        def _queue_generation():
            generate_report_job_task.delay(str(job.pk))
            _safe_write_audit(
                audit_action,
                "ReportJob",
                job.pk,
                organization=job.organization,
                actor=request.user,
                message=f"Report generation queued for {job.report_type or 'unknown'}",
            )

        transaction.on_commit(_queue_generation)
        return ReportJobDetailSerializer(self._refresh_job_from_db(job), context=self.get_serializer_context()).data

    @action(detail=True, methods=["post"])
    def generate(self, request, pk=None):
        job = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if job.status not in {ReportJobStatus.PENDING, ReportJobStatus.FAILED}:
            return Response(
                {"detail": "Only pending or failed jobs can be generated."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        data = self._enqueue_generation(job, request, audit_action="REPORT_GENERATION_REQUESTED")
        return Response(data)

    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        job = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if job.status != ReportJobStatus.FAILED:
            return Response(
                {"detail": "Only failed jobs can be retried."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        data = self._enqueue_generation(job, request, audit_action="REPORT_RETRY_REQUESTED")
        return Response(data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        job = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if job.status != ReportJobStatus.PENDING:
            return Response(
                {"detail": "Only pending jobs can be cancelled."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if job.file:
            job.file.delete(save=False)
            job.file = None
        job.status = ReportJobStatus.CANCELLED
        job.started_at = job.started_at or timezone.now()
        job.completed_at = timezone.now()
        job.error_message = "Cancelled by user"
        job.save(update_fields=["status", "started_at", "completed_at", "error_message", "file", "updated_at"])
        _safe_write_audit(
            "REPORT_CANCELLED",
            "ReportJob",
            job.pk,
            organization=job.organization,
            actor=request.user,
            message="Report job cancelled by user",
        )
        return Response(ReportJobDetailSerializer(self._refresh_job_from_db(job), context=self.get_serializer_context()).data)

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        job = self.get_object()
        try:
            return download_report_job_artifact(job, request=request)
        except Http404:
            return Response(
                {"detail": "This report is not available for download."},
                status=status.HTTP_404_NOT_FOUND,
            )


class ReportArtifactViewSet(ScopedModelViewSet):
    access_scope = "mixed"
    organization_field = "job__organization"
    data_center_field = "job__data_center"
    queryset = ReportArtifact.objects.select_related("job", "job__organization", "job__data_center").all().order_by("-created_at")
    serializer_class = ReportArtifactSerializer
    permission_module = "report"
    audit_resource_type = "ReportArtifact"
    filterset_fields = ["job", "format", "content_type"]
    search_fields = ["original_filename", "checksum_sha256", "job__template__name", "job__template__code"]
    ordering_fields = ["created_at", "updated_at", "size_bytes", "format"]
    ordering = ["-created_at"]

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        artifact = self.get_object()
        try:
            return download_report_artifact(artifact, request=request)
        except Http404:
            return Response(
                {"detail": "This artifact is not available for download."},
                status=status.HTTP_404_NOT_FOUND,
            )


class ReportScheduleViewSet(ScopedModelViewSet):
    access_scope = "mixed"
    organization_field = "organization"
    data_center_field = "data_center"
    queryset = ReportSchedule.objects.select_related("organization", "data_center", "created_by", "last_job").all().order_by("-created_at")
    serializer_class = ReportScheduleSerializer
    permission_module = "report"
    audit_resource_type = "ReportSchedule"
    filterset_class = ReportScheduleFilter
    search_fields = ["name", "report_type", "organization__name", "organization__code", "created_by__username", "created_by__email", "last_error_message"]
    ordering_fields = ["created_at", "updated_at", "next_run_at", "last_run_at", "last_sent_at", "name", "report_type", "frequency"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        if self.action == "run_now":
            return ReportScheduleRunNowSerializer
        return super().get_serializer_class()

    def get_queryset(self):
        qs = super().get_queryset()
        recent_runs = ReportScheduleRun.objects.select_related("generated_job", "requested_by").prefetch_related("deliveries").order_by("-created_at")
        return qs.prefetch_related(Prefetch("runs", queryset=recent_runs, to_attr="_prefetched_recent_runs"))

    @action(detail=True, methods=["post"])
    def run_now(self, request, pk=None):
        schedule = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
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
                message=f"Manual report delivery queued for {schedule.report_type_label}",
            )

        transaction.on_commit(_queue_delivery)
        refreshed = (
            ReportSchedule.objects.select_related("organization", "data_center", "created_by", "last_job")
            .filter(pk=schedule.pk)
            .first()
        )
        payload = ReportScheduleSerializer(refreshed or schedule, context=self.get_serializer_context()).data
        payload["detail"] = "Report delivery queued."
        return Response(payload, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=["get"])
    def runs(self, request, pk=None):
        schedule = self.get_object()
        runs = schedule.runs.select_related("generated_job", "requested_by").prefetch_related("deliveries").order_by("-created_at")[:10]
        serializer = ReportScheduleRunSerializer(runs, many=True, context=self.get_serializer_context())
        return Response(serializer.data)


class ReportScheduleRunViewSet(ScopedModelViewSet):
    access_scope = "mixed"
    organization_field = "organization"
    queryset = ReportScheduleRun.objects.select_related(
        "schedule",
        "organization",
        "requested_by",
        "generated_job",
    ).all().order_by("-created_at")
    serializer_class = ReportScheduleRunSerializer
    permission_module = "report"
    audit_resource_type = "ReportScheduleRun"
    search_fields = ["schedule__name", "schedule__report_type", "error_message", "trigger_source"]
    ordering_fields = ["created_at", "updated_at", "queued_at", "started_at", "completed_at", "status", "window_start", "window_end"]
    ordering = ["-created_at"]

    @action(detail=True, methods=["get"])
    def deliveries(self, request, pk=None):
        run = self.get_object()
        deliveries = run.deliveries.all().order_by("created_at")
        serializer = ReportScheduleDeliverySerializer(deliveries, many=True, context=self.get_serializer_context())
        return Response(serializer.data)


class ReportDashboardAPIView(APIView):
    permission_classes = [DCIMRBACPermission]
    permission_module = "report"

    def get(self, request):
        serializer = ReportDashboardQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        try:
            payload = get_reporting_dashboard(
                user=request.user,
                organization=serializer.validated_data.get("organization"),
                data_center=serializer.validated_data.get("data_center"),
                start_at=serializer.validated_data.get("start_at"),
                end_at=serializer.validated_data.get("end_at"),
                timezone_name=serializer.validated_data.get("timezone"),
            )
        except ValidationError as exc:
            if hasattr(exc, "message_dict"):
                raise DRFValidationError(exc.message_dict)
            raise DRFValidationError(getattr(exc, "messages", [str(exc)]))
        response_serializer = ReportDashboardResponseSerializer(instance=payload)
        return Response(response_serializer.data)
