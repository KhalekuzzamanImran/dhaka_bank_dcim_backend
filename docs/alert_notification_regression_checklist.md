# Alert and Notification Regression Checklist

Use this checklist after changing alert rules, notification delivery, or Celery workers.

## 1. Schema and health

- `python manage.py check`
- `python manage.py migrate`

## 2. Seed data

- `python manage.py seed_alert_rules`
- Verify PAC rules are created for existing metric codes.

## 3. Alert open

- Force a telemetry value into an alarm state.
- Call `evaluate_latest(latest)`.
- Confirm one `AlertEvent` is `OPEN`.

## 4. Duplicate prevention

- Call `evaluate_latest(latest)` again with the same active condition.
- Confirm the active alert count does not increase.

## 5. Acknowledge

- POST `/api/v1/alerts/alert-events/{id}/acknowledge/` with a comment.
- Confirm the alert becomes `ACKNOWLEDGED`.
- Confirm `AlertComment` and `AlertEventLog` rows were created.

## 6. Auto-resolve

- Return the telemetry value to normal.
- Call `evaluate_latest(latest)` again.
- Confirm the alert becomes `RESOLVED`.

## 7. Notification creation

- Confirm alert open creates the expected notification channels:
  - INFO -> WEB
  - WARNING -> WEB + EMAIL
  - CRITICAL/EMERGENCY -> WEB + EMAIL + SMS

## 8. Console email

- Run `docker compose logs -f notification-worker`.
- Confirm the email backend prints the message body and `Email sent ...`.

## 9. Console SMS

- Run `docker compose logs -f notification-worker`.
- Confirm the SMS backend prints `[TEST SMS] To: ...`.

## 10. Pending retry

- Find an old `PENDING` notification.
- Run `python manage.py retry_pending_notifications --dry-run`.
- Run `python manage.py retry_pending_notifications`.
- Confirm it is requeued to Celery.

## 11. Dedupe

- Trigger the same alert open or resolve multiple times.
- Confirm notification rows are not duplicated.

## 12. Escalation

- Create an unacknowledged alert older than the escalation threshold.
- Run the escalation task.
- Confirm escalation logs or notifications are created.

## 13. Suppression

- Create an active suppression window.
- Trigger the matching alert.
- Confirm the alert is suppressed or not opened, according to the configured policy.

## 14. User access

- Log in as a restricted user.
- Confirm alert summary and notification views only show allowed resources.

## 15. Production email switch

- Set SMTP env vars in `.env`.
- Restart workers.
- Send a test email notification.

## 16. Production SMS switch

- Set `SMS_BACKEND=http` and gateway env vars.
- Restart workers.
- Send a test SMS notification.
