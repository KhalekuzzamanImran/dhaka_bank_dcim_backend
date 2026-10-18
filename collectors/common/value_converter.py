from decimal import Decimal, InvalidOperation


def normalize_value(raw_value, data_type="float", scale_factor=1, offset_value=0):
    data_type = (data_type or "float").lower()
    if raw_value is None:
        return None
    if data_type in {"float", "float32", "float64", "integer", "int", "uint16", "int16", "uint32", "int32", "counter", "gauge"}:
        try:
            value = Decimal(str(raw_value)) * Decimal(str(scale_factor)) + Decimal(str(offset_value))
            if data_type in {"integer", "int", "uint16", "int16", "uint32", "int32", "counter", "gauge"}:
                # Modbus mapping types describe the raw register encoding. A
                # scale factor can turn that integer register into a
                # fractional engineering value (e.g. 13556 * 0.001 = 13.556 V).
                # Keep integral results as ints, but preserve fractional values.
                if value == value.to_integral_value():
                    return int(value)
                return float(value)
            return float(value)
        except (InvalidOperation, TypeError, ValueError):
            return None
    if data_type in {"bool", "boolean"}:
        if isinstance(raw_value, str):
            return raw_value.strip().lower() in {"1", "true", "yes", "on", "online", "ok", "active"}
        return bool(raw_value)
    return str(raw_value)
