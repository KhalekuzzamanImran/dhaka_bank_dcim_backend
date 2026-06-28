from django.core.cache import cache


def acquire_device_poll_lock(device_id, timeout=120):
    return cache.add(f"poll-lock:device:{device_id}", "1", timeout=timeout)


def release_device_poll_lock(device_id):
    cache.delete(f"poll-lock:device:{device_id}")


def acquire_modbus_gateway_lock(host, port, timeout=30):
    return cache.add(f"poll-lock:modbus-gateway:{host}:{port}", "1", timeout=timeout)


def release_modbus_gateway_lock(host, port):
    cache.delete(f"poll-lock:modbus-gateway:{host}:{port}")
