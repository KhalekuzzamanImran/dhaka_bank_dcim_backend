from __future__ import annotations

from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

from .services import LIVE_UPDATES_GROUP_NAME, get_live_update_snapshot


class LiveUpdatesConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = await self._authenticate()
        if not getattr(user, "is_authenticated", False):
            await self.close(code=4401)
            return

        self.scope["user"] = user
        try:
            await self.channel_layer.group_add(LIVE_UPDATES_GROUP_NAME, self.channel_name)
            await self.accept()
            await self.send_json({"type": "snapshot", **await database_sync_to_async(get_live_update_snapshot)()})
        except Exception:
            await self.close(code=1011)

    async def disconnect(self, close_code):
        if self.channel_layer is not None:
            try:
                await self.channel_layer.group_discard(LIVE_UPDATES_GROUP_NAME, self.channel_name)
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
