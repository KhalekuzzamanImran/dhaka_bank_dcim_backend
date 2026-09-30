from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.common.access import invalidate_access_scope_cache

from .models import Role, UserResourceAccess


@receiver(post_save, sender=UserResourceAccess)
@receiver(post_delete, sender=UserResourceAccess)
def invalidate_user_access_scope(sender, instance, **kwargs):
    invalidate_access_scope_cache(instance.user_id)


def _invalidate_role_users(instance):
    for user_id in UserResourceAccess.objects.filter(role_id=instance.pk).values_list("user_id", flat=True):
        invalidate_access_scope_cache(user_id)


@receiver(post_save, sender=Role)
@receiver(post_delete, sender=Role)
def invalidate_role_access_scopes(sender, instance, **kwargs):
    _invalidate_role_users(instance)
