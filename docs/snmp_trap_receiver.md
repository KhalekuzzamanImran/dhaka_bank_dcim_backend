# SNMP Trap Receiver

SNMP trap receiver logic lives in `collectors/snmp_trap_receiver/`.

- `receiver.py`: UDP listener for SNMP traps.
- `services.py`: maps traps into SNMPTrapEvent, DeviceEvent and AlertEvent.
- `tasks.py`: asynchronous trap processing.

The receiver listens inside the container on UDP 1162 and maps host UDP 162 to it.
