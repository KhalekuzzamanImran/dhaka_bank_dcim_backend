class ModbusWorkerError(Exception):
    pass


class ModbusConfigurationError(ModbusWorkerError):
    pass


class ModbusResponseError(ModbusWorkerError):
    pass
