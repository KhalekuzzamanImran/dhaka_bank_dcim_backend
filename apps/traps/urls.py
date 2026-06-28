from rest_framework.routers import DefaultRouter
from .views import SNMPTrapSourceViewSet, SNMPTrapOIDMappingViewSet, SNMPTrapEventViewSet

router = DefaultRouter()
router.register(r"sources", SNMPTrapSourceViewSet, basename="snmp-trap-source")
router.register(r"mappings", SNMPTrapOIDMappingViewSet, basename="snmp-trap-mapping")
router.register(r"events", SNMPTrapEventViewSet, basename="snmp-trap-event")
urlpatterns = router.urls
