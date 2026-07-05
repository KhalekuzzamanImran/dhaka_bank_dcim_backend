from rest_framework.routers import DefaultRouter

from .views import PermissionViewSet, RolePermissionViewSet, RoleViewSet, UserDataCenterRoleViewSet, UserResourceAccessViewSet

router = DefaultRouter()
router.register(r"roles", RoleViewSet, basename="role")
router.register(r"permissions", PermissionViewSet, basename="permission")
router.register(r"role-permissions", RolePermissionViewSet, basename="role-permission")
router.register(r"user-resource-accesses", UserResourceAccessViewSet, basename="user-resource-access")
router.register(r"user-data-center-roles", UserDataCenterRoleViewSet, basename="user-data-center-role")

urlpatterns = router.urls
