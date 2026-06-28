class SNMPWorkerError(Exception):
    """Base exception for SNMP worker errors."""


class SNMPCredentialError(SNMPWorkerError):
    """Raised when a device does not have a valid SNMP credential."""


class SNMPConfigurationError(SNMPWorkerError):
    """Raised when a device is missing required SNMP configuration."""


class SNMPTimeoutError(SNMPWorkerError):
    """Raised when an SNMP request times out or cannot reach the device."""


class SNMPResponseError(SNMPWorkerError):
    """Raised when the device returns an SNMP error response."""
