from rest_framework.routers import DefaultRouter
from .views import DataCenterViewSet, RoomViewSet, RowViewSet, RackViewSet

router = DefaultRouter()
router.register(r"data-centers", DataCenterViewSet, basename="data-center")
router.register(r"rooms", RoomViewSet, basename="room")
router.register(r"rows", RowViewSet, basename="row")
router.register(r"racks", RackViewSet, basename="rack")
urlpatterns = router.urls
