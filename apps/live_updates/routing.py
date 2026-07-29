from django.urls import path

from .consumers import LiveUpdatesConsumer

websocket_urlpatterns = [
    path("ws/live-updates/", LiveUpdatesConsumer.as_asgi()),
]

