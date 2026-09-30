import os


bind = '0.0.0.0:8000'
workers = int(os.getenv('API_WORKERS', '3'))
worker_class = 'uvicorn.workers.UvicornWorker'
timeout = 60
keepalive = 5
max_requests = 1000
max_requests_jitter = 100
accesslog = '-'
errorlog = '-'
