from apps.common.middleware import get_current_request

SENSITIVE_KEYS = {'password', 'token', 'secret', 'credential', 'community', 'auth_key', 'priv_key'}

def redact(value):
    if isinstance(value, dict):
        return {k: ('***REDACTED***' if any(s in k.lower() for s in SENSITIVE_KEYS) else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value

def write_audit(action, resource_type=None, resource_id=None, old_value=None, new_value=None, message=None, organization=None, actor=None):
    from apps.audit.models import AuditLog
    request = get_current_request()
    user = actor or getattr(request, 'user', None)
    if user is not None and not getattr(user, 'is_authenticated', False):
        user = None
    ip = None
    ua = None
    if request is not None:
        ip = request.META.get('HTTP_X_FORWARDED_FOR', request.META.get('REMOTE_ADDR', '')).split(',')[0]
        ua = request.META.get('HTTP_USER_AGENT', '')
    return AuditLog.objects.create(
        organization=organization,
        actor=user,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id else None,
        old_value=redact(old_value or {}),
        new_value=redact(new_value or {}),
        ip_address=ip or None,
        user_agent=ua,
        message=message,
    )
