from __future__ import annotations

import os

from django.http import FileResponse
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.common.audit import write_audit
from apps.common.viewsets import ScopedModelViewSet

from .filters import ReportJobFilter, ReportScheduleFilter
from .models import ReportJob, ReportJobStatus, ReportSchedule, ReportTemplate
from .services.jobs import ReportJobService
from .serializers import (
    ReportJobCreateSerializer,
    ReportJobDetailSerializer,
    ReportJobGenerateSerializer,
    ReportJobListSerializer,
    ReportJobRetrySerializer,
    ReportScheduleSerializer,
    ReportScheduleRunNowSerializer,
    ReportTemplateSerializer,
)


def _safe_write_audit(*args, **kwargs):
    try:
        return write_audit(*args, **kwargs)
    except Exception:
        return None


class ReportTemplateViewSet(ScopedModelViewSet):
    access_scope = "organization"
    organization_field = "organization"
    queryset = ReportTemplate.objects.select_related("organization", "definition", "created_by").all().order_by("-created_at")
    serializer_class = ReportTemplateSerializer
    permission_module = "report"
    audit_resource_type = "ReportTemplate"
    filterset_fields = ["organization", "is_active"]
    search_fields = ["name", "code"]
    ordering_fields = ["created_at", "updated_at", "name", "code"]
    ordering = ["-created_at"]


class ReportJobViewSet(ScopedModelViewSet):
    access_scope = "mixed"
    queryset = (
        ReportJob.objects.select_related(
            "organization",
            "data_center",
            "definition",
            "template",
            "schedule",
            "requested_by",
            "parent_job",
        )
        .prefetch_related("artifacts", "deliveries")
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
            ReportJob.objects.select_related(
                "organization",
                "data_center",
                "definition",
                "template",
                "schedule",
                "requested_by",
                "parent_job",
            )
            .prefetch_related("artifacts", "deliveries")
            .filter(pk=job.pk)
            .first()
        )

    def _enqueue_generation(self, job, request, *, audit_action: str):
        if not job.requested_by_id and request.user.is_authenticated:
            job.requested_by = request.user
            job.save(update_fields=["requested_by", "updated_at"])
        queued = ReportJobService.enqueue_job(job, requested_by=request.user)
        return ReportJobDetailSerializer(self._refresh_job_from_db(queued), context=self.get_serializer_context()).data

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
        retry_job = ReportJobService.create_retry_job(failed_job=job, requested_by=request.user)
        job.status = ReportJobStatus.COMPLETED
        job.error_message = ""
        job.error_code = ""
        if not job.completed_at:
            job.completed_at = timezone.now()
        job.save(update_fields=["status", "error_message", "error_code", "completed_at", "updated_at"])
        data = ReportJobDetailSerializer(self._refresh_job_from_db(retry_job), context=self.get_serializer_context()).data
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
        ReportJobService.cancel_job(job, requested_by=request.user)
        return Response(ReportJobDetailSerializer(self._refresh_job_from_db(job), context=self.get_serializer_context()).data)

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        job = self.get_object()
        if not job.is_downloadable:
            return Response(
                {"detail": "This report is not available for download."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        _safe_write_audit(
            "REPORT_DOWNLOADED",
            "ReportJob",
            job.pk,
            organization=job.organization,
            actor=request.user,
            message="Report downloaded",
        )
        artifact = getattr(job, "primary_artifact", None)
        if artifact and getattr(artifact, "file", None):
            file_handle = artifact.file.open("rb")
            filename = os.path.basename(artifact.file.name)
        else:
            file_handle = job.file.open("rb")
            filename = os.path.basename(job.file.name)
        return FileResponse(
            file_handle,
            as_attachment=True,
            filename=filename,
        )


class ReportScheduleViewSet(ScopedModelViewSet):
    access_scope = "mixed"
    organization_field = "organization"
    data_center_field = "data_center"
    queryset = ReportSchedule.objects.select_related(
        "organization",
        "data_center",
        "definition",
        "template",
        "created_by",
        "last_job",
        "last_job__definition",
        "last_job__template",
        "last_job__requested_by",
    ).prefetch_related("recipient_entries").all().order_by("-created_at")
    serializer_class = ReportScheduleSerializer
    permission_module = "report"
    audit_resource_type = "ReportSchedule"
    filterset_class = ReportScheduleFilter
    search_fields = [
        "name",
        "report_type",
        "organization__name",
        "organization__code",
        "definition__code",
        "definition__name",
        "template__name",
        "template__code",
        "created_by__username",
        "created_by__email",
        "last_error_message",
    ]
    ordering_fields = [
        "created_at",
        "updated_at",
        "next_run_at",
        "last_run_at",
        "last_success_at",
        "name",
        "report_type",
        "frequency",
        "status",
    ]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        if self.action == "run_now":
            return ReportScheduleRunNowSerializer
        return super().get_serializer_class()

    @action(detail=True, methods=["post"])
    def run_now(self, request, pk=None):
        schedule = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        run_now_job = ReportJobService.create_run_now_job(
            organization=schedule.organization,
            data_center=schedule.data_center,
            definition=schedule.definition,
            template=schedule.template,
            parameters=schedule.parameters,
            primary_format=schedule.primary_format,
            attachment_formats=schedule.attachment_formats,
            requested_by=request.user,
            schedule=schedule,
            scheduled_for=timezone.now(),
            recipients=list(schedule.recipient_entries.filter(is_active=True).values("channel", "recipient_type", "destination", "display_name")),
            enqueue=True,
        )
        schedule.last_delivery_status = "PENDING"
        schedule.last_error_message = ""
        schedule.save(update_fields=["last_delivery_status", "last_error_message", "updated_at"])
        from apps.reports.tasks import deliver_report_schedule_task

        transaction.on_commit(lambda: deliver_report_schedule_task.delay(str(schedule.pk)))
        refreshed = (
            ReportSchedule.objects.select_related(
                "organization",
                "data_center",
                "definition",
                "template",
                "created_by",
                "last_job",
                "last_job__definition",
                "last_job__template",
                "last_job__requested_by",
            ).prefetch_related("recipient_entries")
            .filter(pk=schedule.pk)
            .first()
        )
        payload = ReportScheduleSerializer(refreshed or schedule, context=self.get_serializer_context()).data
        payload["detail"] = "Report delivery queued."
        payload["job_id"] = str(run_now_job.pk)
        return Response(payload, status=status.HTTP_202_ACCEPTED)
