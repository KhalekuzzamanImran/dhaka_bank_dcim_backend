import threading

_local = threading.local()

def get_current_request():
    return getattr(_local, 'request', None)

class RequestContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
    def __call__(self, request):
        _local.request = request
        try:
            return self.get_response(request)
        finally:
            _local.request = None
