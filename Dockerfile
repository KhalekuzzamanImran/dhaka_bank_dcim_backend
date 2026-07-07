FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libpq-dev netcat-openbsd && rm -rf /var/lib/apt/lists/*
ARG APP_UID=10001
ARG APP_GID=10001
RUN groupadd --gid ${APP_GID} appgroup \
    && useradd --uid ${APP_UID} --gid ${APP_GID} --create-home --shell /bin/bash appuser \
    && mkdir -p /app/staticfiles /app/media /var/log/dcim \
    && chown -R appuser:appgroup /app /var/log/dcim
COPY requirements /app/requirements
RUN pip install --no-cache-dir -r requirements/production.txt
COPY . /app
RUN chown -R appuser:appgroup /app /var/log/dcim
RUN chmod +x /app/scripts/entrypoint.sh
ENTRYPOINT ["/app/scripts/entrypoint.sh"]
