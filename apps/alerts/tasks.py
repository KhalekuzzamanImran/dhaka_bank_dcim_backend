from celery import shared_task

from .services.escalation import run_alert_escalation_check


@shared_task(queue="alerts", autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=3)
def check_alert_escalations_task():
    return {"escalated_count": len(run_alert_escalation_check())}
