from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from apps.common.views import HealthCheckView, ReadinessCheckView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('health/', HealthCheckView.as_view(), name='health'),
    path('ready/', ReadinessCheckView.as_view(), name='ready'),
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/v1/auth/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/v1/auth/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('api/v1/accounts/', include('apps.accounts.urls')),
    path('api/v1/organizations/', include('apps.organizations.urls')),
    path('api/v1/access/', include('apps.access_control.urls')),
    path('api/v1/datacenters/', include('apps.datacenters.urls')),
    path('api/v1/devices/', include('apps.devices.urls')),
    path('api/v1/telemetry/', include('apps.telemetry.urls')),
    path('api/v1/alerts/', include('apps.alerts.urls')),
    path('api/v1/maintenance/', include('apps.maintenance.urls')),
    path('api/v1/dashboards/', include('apps.dashboards.urls')),
    path('api/v1/reports/', include('apps.reports.urls')),
    path('api/v1/notifications/', include('apps.notifications.urls')),
    path('api/v1/audit/', include('apps.audit.urls')),
    path('api/v1/traps/', include('apps.traps.urls')),
]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
