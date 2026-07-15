from __future__ import annotations

import os

from django.http import FileResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.common.audit import write_audit
from apps.common.viewsets import ScopedModelViewSet

from .filters import ReportJobFilter, ReportScheduleFilter
from .models import ReportJob, ReportJobStatus, ReportSchedule, ReportTemplate
from .serializers import (
    ReportJobCreateSerializer,
    ReportJobDetailSerializer,
    ReportJobGenerateSerializer,
    ReportJobListSerializer,
    ReportJobRetrySerializer,
    ReportScheduleSerializer,
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
    queryset = ReportTemplate.objects.select_related("organization").all().order_by("-created_at")
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

        generate_report_job_task.delay(str(job.pk))
        _safe_write_audit(
            audit_action,
            "ReportJob",
            job.pk,
            organization=job.organization,
            actor=request.user,
            message=f"Report generation queued for {job.report_type or 'unknown'}",
        )
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
        file_handle = job.file.open("rb")
        return FileResponse(
            file_handle,
            as_attachment=True,
            filename=os.path.basename(job.file.name),
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
