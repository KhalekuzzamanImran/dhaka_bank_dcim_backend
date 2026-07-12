from .base import *  # noqa
DEBUG = True

# Ensure Celery task.apply()/get() in local/test mode propagates retry exceptions
# so the notification task tests can assert the retry contract directly.
CELERY_TASK_EAGER_PROPAGATES = True
