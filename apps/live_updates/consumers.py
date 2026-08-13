from __future__ import annotations

from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

from apps.access_control.models import RoleScope
from apps.common.access import get_access_scope, get_user_resource_access_rows
from apps.devices.models import Device

from .services import get_live_update_snapshot, live_update_group_name


class LiveUpdatesConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = await self._authenticate()
        if not getattr(user, "is_authenticated", False):
            await self.close(code=4401)
            return

        self.scope["user"] = user
        try:
            self.live_groups = await database_sync_to_async(self._get_groups_for_user)(user)
            for group in self.live_groups:
                await self.channel_layer.group_add(group, self.channel_name)
            await self.accept()
            snapshot = await database_sync_to_async(get_live_update_snapshot)()
            # Revisions are safe metadata; the cached event can contain tenant
            # data and is only exposed to global subscribers.
            if live_update_group_name("global") not in self.live_groups:
                snapshot["last_event"] = None
            await self.send_json({"type": "snapshot", **snapshot})
        except Exception:
            await self.close(code=1011)

    async def disconnect(self, close_code):
        if self.channel_layer is not None:
            try:
                for group in getattr(self, "live_groups", []):
                    await self.channel_layer.group_discard(group, self.channel_name)
            except Exception:
                pass

    async def receive_json(self, content, **kwargs):
        if content.get("type") == "ping":
            await self.send_json({"type": "pong"})

    async def live_update(self, event):
        await self.send_json({"type": "update", **event["event"]})

    async def _authenticate(self):
        raw_query = self.scope.get("query_string", b"").decode("utf-8")
        params = parse_qs(raw_query)
        token = (params.get("token") or [None])[0]
        if not token:
            return None

        try:
            access_token = AccessToken(token)
        except TokenError:
            return None

        user_id = access_token.payload.get("user_id")
        if not user_id:
            return None

        User = get_user_model()
        try:
            return await database_sync_to_async(User.objects.get)(pk=user_id)
        except User.DoesNotExist:
            return None

    @staticmethod
    def _get_groups_for_user(user):
        access = get_access_scope(user)
        if access["global_access"]:
            return [live_update_group_name("global")]

        organization_scopes = set()
        data_center_scopes = set()
        device_scopes = set()
        # Use the narrowest direct assignment for each subscription. Publisher
        # fan-out can include parent scopes, but a socket must not join
        # overlapping parent/child groups and receive duplicates.
        for row in get_user_resource_access_rows(user):
            role_scope = getattr(row.role, "scope", None)
            if role_scope == RoleScope.ORGANIZATION and row.organization_id:
                organization_scopes.add(str(row.organization_id))
            elif role_scope == RoleScope.DATA_CENTER and row.data_center_id:
                data_center_scopes.add(str(row.data_center_id))
            elif role_scope == RoleScope.DEVICE and row.device_id:
                device_scopes.add(str(row.device_id))
            elif role_scope in {RoleScope.ROOM, RoleScope.RACK}:
                devices = Device.objects.filter(
                    organization_id=row.organization_id,
                    data_center_id=row.data_center_id,
                )
                devices = devices.filter(
                    room_id=row.room_id if role_scope == RoleScope.ROOM else row.room_id,
                    **({"rack_id": row.rack_id} if role_scope == RoleScope.RACK else {}),
                )
                device_scopes.update(str(device_id) for device_id in devices.values_list("pk", flat=True))

        # Remove descendants covered by a parent assignment. This keeps the
        # publisher's multi-scope fan-out compatible with one delivery/socket.
        if organization_scopes:
            data_center_scopes -= set(
                str(data_center_id)
                for data_center_id in Device.objects.filter(
                    organization_id__in=organization_scopes,
                ).values_list("data_center_id", flat=True)
                if data_center_id
            )
        if data_center_scopes or organization_scopes:
            covered_devices = Device.objects.filter(
                data_center_id__in=data_center_scopes,
            )
            if organization_scopes:
                covered_devices = covered_devices | Device.objects.filter(organization_id__in=organization_scopes)
            device_scopes -= set(str(device_id) for device_id in covered_devices.values_list("pk", flat=True))

        scopes = {
            *(f"organization:{value}" for value in organization_scopes),
            *(f"data_center:{value}" for value in data_center_scopes),
            *(f"device:{value}" for value in device_scopes),
        }
        return [live_update_group_name(scope) for scope in scopes]
