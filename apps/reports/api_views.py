from __future__ import annotations

import logging
import os
from datetime import datetime
from django.db import IntegrityError, transaction
from django.db.models import Count, Exists, OuterRef, Q, Subquery
from django.http import FileResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.access import filter_queryset_for_user, get_access_scope
from apps.common.audit import write_audit
from apps.common.viewsets import ScopedModelViewSet

from .api_filters import (
    ReportArtifactFilter,
    ReportDeliveryFilter,
    ReportDefinitionFilter,
    ReportJobFilter,
    ReportScheduleFilter,
    ReportTemplateFilter,
)
from .api_serializers import (
    ReportArtifactSerializer,
    ReportDeliverySerializer,
    ReportDefinitionDetailSerializer,
    ReportDefinitionListSerializer,
    ReportDefinitionParameterSchemaSerializer,
    ReportJobActionSerializer,
    ReportJobCreateResponseSerializer,
    ReportJobCreateSerializer,
    ReportJobDetailSerializer,
    ReportJobListSerializer,
    ReportScheduleDetailSerializer,
    ReportScheduleListSerializer,
    ReportScheduleRecipientSerializer,
    ReportScheduleWriteSerializer,
    ReportTemplateDetailSerializer,
    ReportTemplateListSerializer,
    ReportTemplateWriteSerializer,
)
from .domain import (
    ReportArtifactStatus,
    ReportArtifactType,
    ReportDeliveryStatus,
    ReportJobStatus,
    ReportJobTriggerSource,
    ReportRecipientChannel,
    ReportRecipientType,
    ReportScheduleStatus,
)
from .models import (
    ReportArtifact,
    ReportDelivery,
    ReportDefinition,
    ReportJob,
    ReportSchedule,
    ReportScheduleRecipient,
    ReportTemplate,
)
from .scheduling.calculator import calculate_next_runs
from .services.jobs import ReportJobService
from .services.permissions import get_schedule_action_policy

logger = logging.getLogger(__name__)


def _safe_write_audit(*args, **kwargs):
    try:
        return write_audit(*args, **kwargs)
    except Exception:
        logger.warning("Failed to write reports audit log.", exc_info=True)
        return None


def _safe_name(value, default="report"):
    candidate = str(value or default).strip().replace("/", "_").replace("\\", "_")
    return candidate or default


def _job_detail_url(job: ReportJob, request=None) -> str:
    url = reverse("report-job-api-detail", kwargs={"pk": job.pk})
    return request.build_absolute_uri(url) if request else url


def _artifact_download_url(artifact: ReportArtifact, request=None) -> str:
    url = reverse("report-artifact-api-download", kwargs={"pk": artifact.pk})
    return request.build_absolute_uri(url) if request else url


def _schedule_recipients(schedule: ReportSchedule):
    recipients = []
    if hasattr(schedule, "recipient_entries"):
        recipients = list(schedule.recipient_entries.filter(is_active=True))
    return recipients


class ReportDefinitionViewSet(viewsets.ReadOnlyModelViewSet):
    lookup_field = "code"
    lookup_value_regex = "[^/]+"
    permission_module = "report"
    filterset_class = ReportDefinitionFilter
    search_fields = ["code", "name", "description", "category"]
    ordering_fields = ["code", "name", "category", "version"]
    ordering = ["code"]

    def get_queryset(self):
        queryset = ReportDefinition.objects.all().order_by("code")
        if not getattr(self.request.user, "is_superuser", False):
            queryset = queryset.filter(is_active=True)
        return queryset

    def get_serializer_class(self):
        if self.action == "retrieve":
            return ReportDefinitionDetailSerializer
        return ReportDefinitionListSerializer

    @action(detail=True, methods=["get"], url_path="parameter-schema")
    def parameter_schema(self, request, code=None):
        definition = self.get_object()
        serializer = ReportDefinitionParameterSchemaSerializer(definition, context=self.get_serializer_context())
        return Response(serializer.data)


class ReportTemplateViewSet(ScopedModelViewSet):
    access_scope = "organization"
    organization_field = "organization"
    queryset = ReportTemplate.objects.select_related("organization", "definition", "created_by").all().order_by("-created_at")
    serializer_class = ReportTemplateListSerializer
    permission_module = "report"
    audit_resource_type = "ReportTemplate"
    filterset_class = ReportTemplateFilter
    search_fields = ["name", "code", "description", "definition__name", "definition__code"]
    ordering_fields = ["created_at", "updated_at", "name", "code", "version"]
    ordering = ["-created_at"]

    def get_queryset(self):
        queryset = ReportTemplate.objects.select_related("organization", "definition", "created_by").all()
        user = getattr(self.request, "user", None)
        if not user or not user.is_authenticated:
            return queryset.none()
        if getattr(user, "is_superuser", False):
            return queryset.order_by("-created_at")
        scope = get_access_scope(user)
        organization_ids = list(scope["organization_ids"])
        queryset = queryset.filter(Q(organization__isnull=True) | Q(organization_id__in=organization_ids))
        return queryset.order_by("-created_at")

    def get_serializer_class(self):
        if self.action in {"create", "update", "partial_update"}:
            return ReportTemplateWriteSerializer
        if self.action == "retrieve":
            return ReportTemplateDetailSerializer
        return ReportTemplateListSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        detail = ReportTemplateDetailSerializer(instance, context=self.get_serializer_context())
        _safe_write_audit(
            "CREATE",
            "ReportTemplate",
            instance.pk,
            organization=instance.organization,
            actor=request.user,
            new_value=detail.data,
        )
        headers = self.get_success_headers(detail.data)
        return Response(detail.data, status=status.HTTP_201_CREATED, headers=headers)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        policy = get_schedule_action_policy(
            schedule=instance,
            user=request.user,
            organization=instance.organization,
            data_center=instance.data_center,
            request=request,
        )
        if not policy.can("edit"):
            status_code, code, message = policy.denial("edit")
            return Response({"code": code, "message": message}, status=status_code)
        serializer = self.get_serializer(instance, data=request.data, partial=partial, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        detail = ReportTemplateDetailSerializer(instance, context=self.get_serializer_context())
        _safe_write_audit(
            "UPDATE",
            "ReportTemplate",
            instance.pk,
            organization=instance.organization,
            actor=request.user,
            new_value=detail.data,
        )
        return Response(detail.data)

    def destroy(self, request, *args, **kwargs):
        template = self.get_object()
        if ReportJob.objects.filter(template=template).exists() or ReportSchedule.objects.filter(template=template).exists():
            return Response(
                {"code": "TEMPLATE_IN_USE", "message": "Template is referenced by existing jobs or schedules. Disable it instead of deleting."},
                status=status.HTTP_409_CONFLICT,
            )
        return super().destroy(request, *args, **kwargs)


class ReportJobViewSet(ScopedModelViewSet):
    access_scope = "mixed"
    organization_field = "organization"
    data_center_field = "data_center"
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
        .order_by("-queued_at")
    )
    serializer_class = ReportJobDetailSerializer
    permission_module = "report"
    audit_resource_type = "ReportJob"
    filterset_class = ReportJobFilter
    search_fields = [
        "definition__name",
        "definition__code",
        "template__name",
        "template__code",
        "requested_by__username",
        "requested_by__email",
        "error_message",
    ]
    ordering_fields = ["queued_at", "started_at", "completed_at", "status", "retry_count"]
    ordering = ["-queued_at"]
    http_method_names = ["get", "post", "head", "options"]

    def get_serializer_class(self):
        if self.action == "list":
            return ReportJobListSerializer
        if self.action == "retrieve":
            return ReportJobDetailSerializer
        if self.action == "create":
            return ReportJobCreateSerializer
        return ReportJobDetailSerializer

    def _refresh(self, job: ReportJob):
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

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            job = ReportJobService.create_and_enqueue(
                organization=data["organization"],
                data_center=data.get("data_center"),
                definition=data["definition"],
                template=data["template"],
                parameters=data.get("parameters") or {},
                primary_format=data.get("primary_format"),
                attachment_formats=data.get("attachment_formats") or [],
                trigger_source=ReportJobTriggerSource.API,
                requested_by=request.user,
                recipients=data.get("recipients") or [],
            )
        except (IntegrityError, ValueError) as exc:
            return Response({"code": "INVALID_REPORT_JOB", "message": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        payload = {
            "id": str(job.pk),
            "reference": job.reference,
            "status": job.status,
            "trigger_source": job.trigger_source,
            "queued_at": job.queued_at,
            "detail_url": _job_detail_url(job, request=request),
            "message": "Report generation has been queued.",
        }
        return Response(ReportJobCreateResponseSerializer(payload).data, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        job = self.get_object()
        serializer = ReportJobActionSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        try:
            ReportJobService.cancel_job(job, requested_by=request.user)
        except ValueError as exc:
            return Response({"code": "INVALID_JOB_STATE", "message": str(exc)}, status=status.HTTP_409_CONFLICT)
        refreshed = self._refresh(job)
        return Response(ReportJobDetailSerializer(refreshed, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        job = self.get_object()
        serializer = ReportJobActionSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        if job.status != ReportJobStatus.FAILED:
            return Response(
                {"code": "INVALID_JOB_STATE", "message": "Only failed jobs can be retried."},
                status=status.HTTP_409_CONFLICT,
            )
        retry_job = ReportJobService.create_retry_job(failed_job=job, requested_by=request.user)
        refreshed = self._refresh(retry_job)
        return Response(ReportJobDetailSerializer(refreshed, context=self.get_serializer_context()).data, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=["get"])
    def artifacts(self, request, pk=None):
        job = self.get_object()
        queryset = job.artifacts.select_related("job", "job__definition", "job__template").order_by("-created_at")
        page = self.paginate_queryset(queryset)
        serializer = ReportArtifactSerializer(page if page is not None else queryset, many=True, context=self.get_serializer_context())
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    @action(detail=True, methods=["get"])
    def deliveries(self, request, pk=None):
        job = self.get_object()
        queryset = job.deliveries.select_related("job", "schedule", "artifact", "recipient", "job__definition", "job__template").order_by("-queued_at", "-created_at")
        page = self.paginate_queryset(queryset)
        serializer = ReportDeliverySerializer(page if page is not None else queryset, many=True, context=self.get_serializer_context())
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)


class ReportScheduleViewSet(ScopedModelViewSet):
    access_scope = "mixed"
    organization_field = "organization"
    data_center_field = "data_center"
    queryset = (
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
        )
        .prefetch_related("recipient_entries", "executions__deliveries")
        .all()
        .order_by("-created_at")
    )
    serializer_class = ReportScheduleDetailSerializer
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
        if self.action in {"create", "update", "partial_update"}:
            return ReportScheduleWriteSerializer
        if self.action == "retrieve":
            return ReportScheduleDetailSerializer
        return ReportScheduleListSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        detail = ReportScheduleDetailSerializer(instance, context=self.get_serializer_context())
        _safe_write_audit(
            "CREATE",
            "ReportSchedule",
            instance.pk,
            organization=instance.organization,
            actor=request.user,
            new_value=detail.data,
        )
        headers = self.get_success_headers(detail.data)
        return Response(detail.data, status=status.HTTP_201_CREATED, headers=headers)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        detail = ReportScheduleDetailSerializer(instance, context=self.get_serializer_context())
        _safe_write_audit(
            "UPDATE",
            "ReportSchedule",
            instance.pk,
            organization=instance.organization,
            actor=request.user,
            new_value=detail.data,
        )
        return Response(detail.data)

    def _refresh(self, schedule: ReportSchedule):
        return (
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
            )
            .prefetch_related("recipient_entries", "executions__deliveries")
            .filter(pk=schedule.pk)
            .first()
        )

    def destroy(self, request, *args, **kwargs):
        schedule = self.get_object()
        policy = get_schedule_action_policy(
            schedule=schedule,
            user=request.user,
            organization=schedule.organization,
            data_center=schedule.data_center,
            request=request,
        )
        if not policy.can("delete"):
            status_code, code, message = policy.denial("delete")
            return Response({"code": code, "message": message}, status=status_code)
        if ReportJob.objects.filter(schedule=schedule).exists():
            return Response(
                {"code": "SCHEDULE_IN_USE", "message": "Schedule has existing executions. Disable it instead of deleting."},
                status=status.HTTP_409_CONFLICT,
            )
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=["post"], url_path="run-now")
    def run_now(self, request, pk=None):
        schedule = self.get_object()
        policy = get_schedule_action_policy(
            schedule=schedule,
            user=request.user,
            organization=schedule.organization,
            data_center=schedule.data_center,
            request=request,
        )
        if not policy.can("run_now"):
            status_code, code, message = policy.denial("run_now")
            return Response({"code": code, "message": message}, status=status_code)
        serializer = ReportJobActionSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        recipients = [
            {
                "channel": recipient.channel,
                "recipient_type": recipient.recipient_type,
                "destination": recipient.destination,
                "display_name": recipient.display_name,
            }
            for recipient in _schedule_recipients(schedule)
        ]
        try:
            job = ReportJobService.create_run_now_job(
                organization=schedule.organization,
                data_center=schedule.data_center,
                definition=schedule.definition,
                template=schedule.template,
                parameters=schedule.parameters,
                primary_format=schedule.primary_format,
                attachment_formats=schedule.attachment_formats,
                requested_by=request.user,
                schedule=schedule,
                recipients=recipients,
            )
        except (IntegrityError, ValueError) as exc:
            return Response({"code": "INVALID_SCHEDULE", "message": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        _safe_write_audit(
            "REPORT_SCHEDULE_RUN_NOW",
            "ReportSchedule",
            schedule.pk,
            organization=schedule.organization,
            actor=request.user,
            message="Report schedule run now requested",
        )

        refreshed = self._refresh(schedule)
        return Response(
            {
                "job": ReportJobCreateResponseSerializer(
                    {
                        "id": str(job.pk),
                        "reference": job.reference,
                        "status": job.status,
                        "trigger_source": job.trigger_source,
                        "queued_at": job.queued_at,
                        "detail_url": _job_detail_url(job, request=request),
                        "message": "Report generation has been queued.",
                    }
                ).data,
                "schedule": ReportScheduleDetailSerializer(refreshed, context=self.get_serializer_context()).data,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=["post"])
    def pause(self, request, pk=None):
        schedule = self.get_object()
        policy = get_schedule_action_policy(
            schedule=schedule,
            user=request.user,
            organization=schedule.organization,
            data_center=schedule.data_center,
            request=request,
        )
        if not policy.can("pause"):
            status_code, code, message = policy.denial("pause")
            return Response({"code": code, "message": message}, status=status_code)
        serializer = ReportJobActionSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        schedule.status = ReportScheduleStatus.PAUSED
        schedule.is_active = False
        schedule.save(update_fields=["status", "is_active", "updated_at"])
        _safe_write_audit(
            "REPORT_SCHEDULE_PAUSED",
            "ReportSchedule",
            schedule.pk,
            organization=schedule.organization,
            actor=request.user,
            message="Report schedule paused",
        )
        refreshed = self._refresh(schedule)
        return Response(ReportScheduleDetailSerializer(refreshed, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["post"])
    def resume(self, request, pk=None):
        schedule = self.get_object()
        policy = get_schedule_action_policy(
            schedule=schedule,
            user=request.user,
            organization=schedule.organization,
            data_center=schedule.data_center,
            request=request,
        )
        if not policy.can("resume"):
            status_code, code, message = policy.denial("resume")
            return Response({"code": code, "message": message}, status=status_code)
        serializer = ReportJobActionSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        schedule.status = ReportScheduleStatus.ACTIVE
        schedule.is_active = True
        schedule.next_run_at = schedule.calculate_next_run_at(reference_time=timezone.now())
        if schedule.end_at and schedule.next_run_at and schedule.next_run_at > schedule.end_at:
            schedule.status = ReportScheduleStatus.EXPIRED
            schedule.is_active = False
        schedule.save(update_fields=["status", "is_active", "next_run_at", "updated_at"])
        _safe_write_audit(
            "REPORT_SCHEDULE_RESUMED",
            "ReportSchedule",
            schedule.pk,
            organization=schedule.organization,
            actor=request.user,
            message="Report schedule resumed",
        )
        refreshed = self._refresh(schedule)
        return Response(ReportScheduleDetailSerializer(refreshed, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["get"])
    def executions(self, request, pk=None):
        schedule = self.get_object()
        queryset = schedule.executions.select_related("organization", "data_center", "definition", "template", "requested_by").prefetch_related("artifacts", "deliveries").order_by("-queued_at")
        page = self.paginate_queryset(queryset)
        serializer = ReportJobListSerializer(page if page is not None else queryset, many=True, context=self.get_serializer_context())
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    @action(detail=True, methods=["get"])
    def deliveries(self, request, pk=None):
        schedule = self.get_object()
        queryset = ReportDelivery.objects.filter(schedule=schedule).select_related("job", "schedule", "artifact", "recipient", "job__definition", "job__template").order_by("-queued_at", "-created_at")
        page = self.paginate_queryset(queryset)
        serializer = ReportDeliverySerializer(page if page is not None else queryset, many=True, context=self.get_serializer_context())
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    @action(detail=True, methods=["get"], url_path="next-runs")
    def next_runs(self, request, pk=None):
        schedule = self.get_object()
        try:
            count = min(int(request.query_params.get("count", 5) or 5), 20)
        except (TypeError, ValueError):
            count = 5
        runs = calculate_next_runs(
            recurrence_rule=schedule.recurrence_rule or {
                "frequency": schedule.frequency,
                "delivery_time": schedule.delivery_time.strftime("%H:%M:%S") if schedule.delivery_time else "06:00:00",
            },
            after=schedule.next_run_at or timezone.now(),
            start_at=schedule.start_at,
            end_at=schedule.end_at,
            count=count,
        )
        return Response({"count": len(runs), "results": [run.isoformat() for run in runs]})

    @action(detail=False, methods=["post"], url_path="preview-next-runs")
    def preview_next_runs(self, request):
        recurrence_rule = request.data.get("recurrence_rule") or {}
        try:
            count = min(int(request.data.get("count", 5) or 5), 20)
        except (TypeError, ValueError):
            count = 5
        start_at = request.data.get("start_at")
        end_at = request.data.get("end_at")
        if start_at:
            start_at = parse_datetime(start_at)
        if end_at:
            end_at = parse_datetime(end_at)
        runs = calculate_next_runs(
            recurrence_rule=recurrence_rule,
            after=timezone.now(),
            start_at=start_at,
            end_at=end_at,
            count=count,
        )
        return Response({"count": len(runs), "results": [run.isoformat() for run in runs]})


class ReportArtifactViewSet(ScopedModelViewSet):
    access_scope = "mixed"
    organization_field = "job__organization"
    data_center_field = "job__data_center"
    queryset = (
        ReportArtifact.objects.select_related(
            "job",
            "job__organization",
            "job__data_center",
            "job__definition",
            "job__template",
            "job__requested_by",
        )
        .all()
        .order_by("-created_at")
    )
    serializer_class = ReportArtifactSerializer
    permission_module = "report"
    audit_resource_type = "ReportArtifact"
    filterset_class = ReportArtifactFilter
    search_fields = ["file_name", "job__reference", "job__definition__code", "job__template__code"]
    ordering_fields = ["created_at", "expires_at", "status", "format"]
    ordering = ["-created_at"]
    http_method_names = ["get", "post", "head", "options"]

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        artifact = self.get_object()
        if artifact.status != ReportArtifactStatus.AVAILABLE:
            return Response({"code": "ARTIFACT_NOT_AVAILABLE", "message": "Artifact is not available for download."}, status=status.HTTP_410_GONE)
        if artifact.expires_at and artifact.expires_at <= timezone.now():
            return Response({"code": "ARTIFACT_EXPIRED", "message": "Artifact has expired."}, status=status.HTTP_410_GONE)
        if not artifact.file:
            return Response({"code": "ARTIFACT_MISSING", "message": "Artifact file is missing."}, status=status.HTTP_410_GONE)
        _safe_write_audit(
            "REPORT_ARTIFACT_DOWNLOADED",
            "ReportArtifact",
            artifact.pk,
            organization=artifact.job.organization,
            actor=request.user,
            message="Report artifact downloaded",
        )
        file_handle = artifact.file.open("rb")
        filename = _safe_name(artifact.file_name or os.path.basename(artifact.file.name))
        response = FileResponse(file_handle, as_attachment=True, filename=filename)
        response["Content-Type"] = artifact.content_type or "application/octet-stream"
        if artifact.size_bytes is not None:
            response["Content-Length"] = str(artifact.size_bytes)
        response["X-Content-Type-Options"] = "nosniff"
        response["Cache-Control"] = "private, no-store"
        response["Pragma"] = "no-cache"
        return response


class ReportDeliveryViewSet(ScopedModelViewSet):
    access_scope = "mixed"
    organization_field = "job__organization"
    data_center_field = "job__data_center"
    queryset = (
        ReportDelivery.objects.select_related(
            "job",
            "schedule",
            "artifact",
            "recipient",
            "job__organization",
            "job__data_center",
            "job__definition",
            "job__template",
        )
        .all()
        .order_by("-queued_at", "-created_at")
    )
    serializer_class = ReportDeliverySerializer
    permission_module = "report"
    audit_resource_type = "ReportDelivery"
    filterset_class = ReportDeliveryFilter
    search_fields = ["job__reference", "schedule__name", "destination_snapshot", "error_message"]
    ordering_fields = ["queued_at", "attempted_at", "delivered_at", "status", "attempt_number"]
    ordering = ["-queued_at", "-created_at"]
    http_method_names = ["get", "post", "head", "options"]

    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        delivery = self.get_object()
        serializer = ReportJobActionSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        if delivery.status != ReportDeliveryStatus.FAILED:
            return Response(
                {"code": "INVALID_DELIVERY_STATE", "message": "Only failed deliveries can be retried."},
                status=status.HTTP_409_CONFLICT,
            )
        if delivery.job.status not in {ReportJobStatus.SUCCEEDED, ReportJobStatus.PARTIALLY_SUCCEEDED}:
            return Response(
                {"code": "INVALID_JOB_STATE", "message": "The related job must have succeeded before retrying delivery."},
                status=status.HTTP_409_CONFLICT,
            )
        artifact = delivery.artifact or delivery.job.primary_artifact
        if delivery.channel != ReportRecipientChannel.SMS and not artifact:
            return Response(
                {"code": "ARTIFACT_NOT_AVAILABLE", "message": "A downloadable artifact is required for delivery retry."},
                status=status.HTTP_409_CONFLICT,
            )
        with transaction.atomic():
            retry_delivery = ReportDelivery.objects.create(
                job=delivery.job,
                schedule=delivery.schedule,
                artifact=artifact,
                channel=delivery.channel,
                recipient=delivery.recipient,
                recipient_type=delivery.recipient_type,
                destination_snapshot=delivery.destination_snapshot,
                attempt_number=delivery.attempt_number + 1,
                status=ReportDeliveryStatus.PENDING,
            )
        from apps.reports.tasks import deliver_report_task

        transaction.on_commit(lambda: deliver_report_task.delay(str(retry_delivery.pk)))
        _safe_write_audit(
            "REPORT_DELIVERY_RETRY_REQUESTED",
            "ReportDelivery",
            delivery.pk,
            organization=delivery.job.organization,
            actor=request.user,
            message="Report delivery retry queued",
        )
        return Response(ReportDeliverySerializer(retry_delivery, context=self.get_serializer_context()).data, status=status.HTTP_202_ACCEPTED)


class ReportDashboardAPIView(APIView):
    permission_module = "report"

    def get(self, request):
        user = request.user
        scope = get_access_scope(user)
        organization_id = request.query_params.get("organization")
        data_center_id = request.query_params.get("data_center")
        date_from = request.query_params.get("date_from")
        date_to = request.query_params.get("date_to")
        organization_ids = list(scope["organization_ids"])
        if organization_id:
            if not scope["global_access"] and organization_id not in {str(value) for value in scope["organization_ids"]}:
                return Response({"code": "FORBIDDEN_SCOPE", "message": "You do not have access to the requested organization."}, status=status.HTTP_403_FORBIDDEN)
            organization_ids = [organization_id]
        if data_center_id and not scope["global_access"] and data_center_id not in {str(value) for value in scope["data_center_ids"]}:
            return Response({"code": "FORBIDDEN_SCOPE", "message": "You do not have access to the requested data center."}, status=status.HTTP_403_FORBIDDEN)
        job_qs = ReportJob.objects.select_related("definition", "template", "organization", "data_center", "requested_by").prefetch_related("artifacts", "deliveries")
        schedule_qs = ReportSchedule.objects.select_related("definition", "template", "organization", "data_center", "created_by", "last_job", "last_job__requested_by").prefetch_related("recipient_entries", "executions__deliveries")
        artifact_qs = ReportArtifact.objects.select_related("job", "job__definition", "job__template", "job__organization", "job__data_center")
        delivery_qs = ReportDelivery.objects.select_related("job", "schedule", "artifact", "recipient", "job__definition", "job__template", "job__organization", "job__data_center")

        if not scope["global_access"]:
            if organization_ids:
                job_qs = job_qs.filter(organization_id__in=organization_ids)
                schedule_qs = schedule_qs.filter(organization_id__in=organization_ids)
                artifact_qs = artifact_qs.filter(job__organization_id__in=organization_ids)
                delivery_qs = delivery_qs.filter(job__organization_id__in=organization_ids)
            else:
                job_qs = job_qs.none()
                schedule_qs = schedule_qs.none()
                artifact_qs = artifact_qs.none()
                delivery_qs = delivery_qs.none()

        if data_center_id:
            job_qs = job_qs.filter(data_center_id=data_center_id)
            schedule_qs = schedule_qs.filter(data_center_id=data_center_id)
            artifact_qs = artifact_qs.filter(job__data_center_id=data_center_id)
            delivery_qs = delivery_qs.filter(job__data_center_id=data_center_id)

        if date_from:
            parsed = parse_datetime(date_from)
            if parsed:
                job_qs = job_qs.filter(created_at__gte=parsed)
                delivery_qs = delivery_qs.filter(created_at__gte=parsed)
        if date_to:
            parsed = parse_datetime(date_to)
            if parsed:
                job_qs = job_qs.filter(created_at__lte=parsed)
                delivery_qs = delivery_qs.filter(created_at__lte=parsed)

        summary = {
            "active_schedules": schedule_qs.filter(status=ReportScheduleStatus.ACTIVE).count(),
            "paused_schedules": schedule_qs.filter(status=ReportScheduleStatus.PAUSED).count(),
            "queued_jobs": job_qs.filter(status=ReportJobStatus.QUEUED).count(),
            "running_jobs": job_qs.filter(status=ReportJobStatus.RUNNING).count(),
            "successful_jobs_today": job_qs.filter(status__in=[ReportJobStatus.SUCCEEDED, ReportJobStatus.PARTIALLY_SUCCEEDED], completed_at__date=timezone.localdate()).count(),
            "failed_jobs_today": job_qs.filter(status=ReportJobStatus.FAILED, completed_at__date=timezone.localdate()).count(),
            "available_artifacts": artifact_qs.filter(status=ReportArtifactStatus.AVAILABLE).count(),
            "failed_deliveries_today": delivery_qs.filter(status=ReportDeliveryStatus.FAILED, attempted_at__date=timezone.localdate()).count(),
        }

        definitions = ReportDefinition.objects.all().order_by("code")
        if not getattr(user, "is_superuser", False):
            definitions = definitions.filter(is_active=True)

        latest_job_id_sq = job_qs.filter(definition_id=OuterRef("pk")).order_by("-queued_at").values("pk")[:1]
        latest_artifact_id_sq = artifact_qs.filter(job__definition_id=OuterRef("pk"), status=ReportArtifactStatus.AVAILABLE).order_by("-created_at").values("pk")[:1]
        schedule_scope = Q()
        if organization_ids:
            schedule_scope &= Q(schedules__organization_id__in=organization_ids)
        if data_center_id:
            schedule_scope &= Q(schedules__data_center_id=data_center_id)
        catalogue_qs = definitions.annotate(
            latest_job_id=Subquery(latest_job_id_sq),
            latest_artifact_id=Subquery(latest_artifact_id_sq),
            active_schedule_count=Count("schedules", filter=schedule_scope & Q(schedules__status=ReportScheduleStatus.ACTIVE), distinct=True),
        )
        catalogue = []
        latest_job_ids = [row.latest_job_id for row in catalogue_qs if row.latest_job_id]
        latest_artifact_ids = [row.latest_artifact_id for row in catalogue_qs if row.latest_artifact_id]
        jobs_map = ReportJob.objects.select_related("definition", "template", "organization", "data_center", "requested_by").prefetch_related("artifacts", "deliveries").filter(pk__in=latest_job_ids).in_bulk()
        artifacts_map = ReportArtifact.objects.select_related("job", "job__definition", "job__template", "job__organization", "job__data_center").filter(pk__in=latest_artifact_ids).in_bulk()
        for definition in catalogue_qs:
            latest_job = jobs_map.get(definition.latest_job_id)
            latest_artifact = artifacts_map.get(definition.latest_artifact_id)
            catalogue.append(
                {
                    "definition": ReportDefinitionListSerializer(definition, context={"request": request}).data,
                    "latest_job": ReportJobListSerializer(latest_job, context={"request": request}).data if latest_job else None,
                    "latest_artifact": ReportArtifactSerializer(latest_artifact, context={"request": request}).data if latest_artifact else None,
                    "active_schedule_count": definition.active_schedule_count,
                    "allowed_actions": ["generate", "create-schedule"] if definition.is_active else [],
                }
            )

        recent_jobs = ReportJobListSerializer(job_qs.order_by("-queued_at")[:10], many=True, context={"request": request}).data
        upcoming_schedules = ReportScheduleListSerializer(schedule_qs.filter(status=ReportScheduleStatus.ACTIVE).order_by("next_run_at")[:10], many=True, context={"request": request}).data
        recent_delivery_failures = ReportDeliverySerializer(delivery_qs.filter(status=ReportDeliveryStatus.FAILED).order_by("-attempted_at")[:10], many=True, context={"request": request}).data

        return Response(
            {
                "summary": summary,
                "report_catalogue": catalogue,
                "recent_jobs": recent_jobs,
                "upcoming_schedules": upcoming_schedules,
                "recent_delivery_failures": recent_delivery_failures,
            }
        )


class ReportOptionsAPIView(APIView):
    permission_module = "report"

    def get(self, request):
        return Response(
            {
                "schedule_statuses": [{"value": value, "label": label} for value, label in ReportScheduleStatus.choices],
                "job_statuses": [{"value": value, "label": label} for value, label in ReportJobStatus.choices],
                "trigger_sources": [{"value": value, "label": label} for value, label in ReportJobTriggerSource.choices],
                "artifact_formats": ["PDF", "CSV", "XLSX"],
                "artifact_types": [{"value": value, "label": label} for value, label in ReportArtifactType.choices],
                "artifact_statuses": [{"value": value, "label": label} for value, label in ReportArtifactStatus.choices],
                "delivery_statuses": [{"value": value, "label": label} for value, label in ReportDeliveryStatus.choices],
                "delivery_channels": [{"value": value, "label": label} for value, label in ReportRecipientChannel.choices],
                "recipient_types": [{"value": value, "label": label} for value, label in ReportRecipientType.choices],
                "recurrence_frequencies": ["DAILY", "WEEKLY", "MONTHLY", "QUARTERLY"],
                "invalid_monthly_day_policies": ["LAST_DAY", "ERROR"],
            }
        )
