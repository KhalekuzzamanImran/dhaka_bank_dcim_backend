from django.urls import path

from .views import LiveUpdateStateAPIView

urlpatterns = [
    path("state/", LiveUpdateStateAPIView.as_view(), name="live-update-state"),
]

