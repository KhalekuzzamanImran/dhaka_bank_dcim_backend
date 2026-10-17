import signal
import socket
import struct
import sys
import time
from dataclasses import dataclass
from typing import Optional


# ============================================================
# TCP server configuration
# ============================================================

HOST = "0.0.0.0"
PORT = 4010

UNIT_ID = 1
FUNCTION_CODE = 0x03  # Read Holding Registers

RESPONSE_TIMEOUT_SECONDS = 10
POLL_INTERVAL_SECONDS = 5
DELAY_BETWEEN_PARAMETERS_SECONDS = 0.2

# Fixed SOH value because the device does not provide an SOH register.
FIXED_SOH_PERCENT = 77.5


# ============================================================
# Parameter configuration
# ============================================================

@dataclass(frozen=True)
class ModbusParameter:
    name: str
    register_address: int
    scale: float
    unit: str
    signed: bool = False
    decimals: int = 1
    register_count: int = 1


PARAMETERS = [
    ModbusParameter(
        name="Voltage",
        register_address=0x2711,
        scale=0.001,
        unit="V",
        signed=False,
        decimals=3,
    ),
    ModbusParameter(
        name="Internal Resistance",
        register_address=0x2793,
        scale=0.001,
        unit="mΩ",
        signed=False,
        decimals=3,
    ),
    ModbusParameter(
        name="Temperature",
        register_address=0x2815,
        scale=0.1,
        unit="°C",
        signed=True,
        decimals=1,
    ),
    ModbusParameter(
        name="Charging/Discharging Current",
        register_address=0x2A01,
        scale=0.1,
        unit="A",
        signed=True,
        decimals=1,
    ),
    ModbusParameter(
        name="SOC",
        register_address=0x2AF9,
        scale=0.1,
        unit="%",
        signed=False,
        decimals=1,
    ),
]


# ============================================================
# Runtime state
# ============================================================

shutdown_requested = False
server_socket: Optional[socket.socket] = None
active_connection: Optional[socket.socket] = None


class ModbusError(Exception):
    """Raised when an invalid Modbus response is received."""


# ============================================================
# Shutdown handling
# ============================================================

def request_shutdown(signum=None, frame=None) -> None:
    global shutdown_requested

    if shutdown_requested:
        return

    shutdown_requested = True
    print("\nShutdown requested. Closing server...")

    if active_connection is not None:
        try:
            active_connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

        try:
            active_connection.close()
        except OSError:
            pass

    if server_socket is not None:
        try:
            server_socket.close()
        except OSError:
            pass


def interruptible_sleep(seconds: float) -> None:
    """Sleep while allowing Ctrl+C or SIGTERM to stop immediately."""
    end_time = time.monotonic() + seconds

    while not shutdown_requested:
        remaining = end_time - time.monotonic()

        if remaining <= 0:
            return

        time.sleep(min(remaining, 0.1))


# ============================================================
# Socket helpers
# ============================================================

def receive_exact(
    conn: socket.socket,
    size: int,
) -> bytes:
    """Receive exactly the requested number of bytes."""
    data = bytearray()

    while len(data) < size:
        if shutdown_requested:
            raise InterruptedError(
                "Server shutdown requested."
            )

        chunk = conn.recv(size - len(data))

        if not chunk:
            raise ConnectionError(
                "Gateway disconnected."
            )

        data.extend(chunk)

    return bytes(data)


# ============================================================
# Modbus TCP functions
# ============================================================

def build_modbus_tcp_request(
    transaction_id: int,
    parameter: ModbusParameter,
) -> bytes:
    """
    Build a Modbus TCP Read Holding Registers request.

    MBAP header:
        Transaction ID: 2 bytes
        Protocol ID:    2 bytes
        Length:         2 bytes
        Unit ID:        1 byte

    PDU:
        Function code:    1 byte
        Register address: 2 bytes
        Register count:   2 bytes
    """
    protocol_id = 0

    pdu = struct.pack(
        ">BHH",
        FUNCTION_CODE,
        parameter.register_address,
        parameter.register_count,
    )

    # Length includes the Unit ID plus the PDU.
    length = 1 + len(pdu)

    mbap_header = struct.pack(
        ">HHHB",
        transaction_id,
        protocol_id,
        length,
        UNIT_ID,
    )

    return mbap_header + pdu


def receive_modbus_tcp_response(
    conn: socket.socket,
) -> bytes:
    """Receive one complete Modbus TCP response."""
    header = receive_exact(conn, 7)

    _, protocol_id, length, _ = struct.unpack(
        ">HHHB",
        header,
    )

    if protocol_id != 0:
        raise ModbusError(
            f"Invalid Modbus protocol ID: {protocol_id}"
        )

    if length < 2:
        raise ModbusError(
            f"Invalid Modbus TCP length: {length}"
        )

    # The Unit ID has already been read in the 7-byte header.
    pdu_length = length - 1
    pdu = receive_exact(conn, pdu_length)

    return header + pdu


def parse_modbus_response(
    response: bytes,
    expected_transaction_id: int,
    parameter: ModbusParameter,
) -> float:
    """Validate and convert a Modbus response to an actual value."""
    if len(response) < 9:
        raise ModbusError(
            f"{parameter.name}: response is too short."
        )

    (
        transaction_id,
        protocol_id,
        length,
        unit_id,
    ) = struct.unpack(
        ">HHHB",
        response[:7],
    )

    pdu = response[7:]

    if transaction_id != expected_transaction_id:
        raise ModbusError(
            f"{parameter.name}: transaction ID mismatch. "
            f"Expected {expected_transaction_id}, "
            f"received {transaction_id}."
        )

    if protocol_id != 0:
        raise ModbusError(
            f"{parameter.name}: invalid protocol ID "
            f"{protocol_id}."
        )

    if unit_id != UNIT_ID:
        raise ModbusError(
            f"{parameter.name}: expected Unit ID "
            f"{UNIT_ID}, received {unit_id}."
        )

    if length != len(pdu) + 1:
        raise ModbusError(
            f"{parameter.name}: invalid MBAP length."
        )

    if len(pdu) < 2:
        raise ModbusError(
            f"{parameter.name}: incomplete response PDU."
        )

    function_code = pdu[0]

    if function_code & 0x80:
        exception_code = pdu[1]

        exception_names = {
            1: "Illegal Function",
            2: "Illegal Data Address",
            3: "Illegal Data Value",
            4: "Slave Device Failure",
            5: "Acknowledge",
            6: "Slave Device Busy",
            10: "Gateway Path Unavailable",
            11: "Gateway Target Device Failed to Respond",
        }

        exception_name = exception_names.get(
            exception_code,
            "Unknown Modbus exception",
        )

        raise ModbusError(
            f"{parameter.name}: Modbus exception "
            f"{exception_code} - {exception_name}."
        )

    if function_code != FUNCTION_CODE:
        raise ModbusError(
            f"{parameter.name}: unexpected function code "
            f"0x{function_code:02X}."
        )

    byte_count = pdu[1]
    expected_byte_count = parameter.register_count * 2

    if byte_count != expected_byte_count:
        raise ModbusError(
            f"{parameter.name}: expected "
            f"{expected_byte_count} data bytes, "
            f"received {byte_count}."
        )

    data = pdu[2:2 + byte_count]

    if len(data) != byte_count:
        raise ModbusError(
            f"{parameter.name}: incomplete register data."
        )

    raw_value = int.from_bytes(
        data,
        byteorder="big",
        signed=parameter.signed,
    )

    return raw_value * parameter.scale


def poll_parameter(
    conn: socket.socket,
    transaction_id: int,
    parameter: ModbusParameter,
) -> float:
    """Send one request and return the converted value."""
    request = build_modbus_tcp_request(
        transaction_id=transaction_id,
        parameter=parameter,
    )

    conn.sendall(request)

    response = receive_modbus_tcp_response(conn)

    return parse_modbus_response(
        response=response,
        expected_transaction_id=transaction_id,
        parameter=parameter,
    )


# ============================================================
# Output
# ============================================================

def display_values(
    values: dict[str, float],
) -> None:
    """Display only actual engineering values."""
    print()
    print("========== Battery Cell Data ==========")

    for parameter in PARAMETERS:
        value = values.get(parameter.name)

        if value is None:
            continue

        formatted_value = (
            f"{value:.{parameter.decimals}f}"
        )

        print(
            f"{parameter.name:<30}: "
            f"{formatted_value} {parameter.unit}"
        )

    print(
        f"{'SOH':<30}: "
        f"{FIXED_SOH_PERCENT:.1f} %"
    )

    print("=======================================")


# ============================================================
# Gateway connection handling
# ============================================================

def handle_gateway(
    conn: socket.socket,
    address: tuple[str, int],
) -> None:
    global active_connection

    active_connection = conn
    conn.settimeout(RESPONSE_TIMEOUT_SECONDS)

    print(
        f"Gateway connected: "
        f"{address[0]}:{address[1]}"
    )

    transaction_id = 1

    try:
        while not shutdown_requested:
            values: dict[str, float] = {}

            for parameter in PARAMETERS:
                if shutdown_requested:
                    break

                actual_value = poll_parameter(
                    conn=conn,
                    transaction_id=transaction_id,
                    parameter=parameter,
                )

                values[parameter.name] = actual_value

                transaction_id += 1

                if transaction_id > 65535:
                    transaction_id = 1

                interruptible_sleep(
                    DELAY_BETWEEN_PARAMETERS_SECONDS
                )

            if shutdown_requested:
                break

            display_values(values)

            interruptible_sleep(
                POLL_INTERVAL_SECONDS
            )

    except socket.timeout:
        if not shutdown_requested:
            print(
                "Timeout: gateway did not return "
                "a Modbus response."
            )

    except InterruptedError:
        pass

    except ConnectionError as error:
        if not shutdown_requested:
            print(f"Connection error: {error}")

    except ModbusError as error:
        if not shutdown_requested:
            print(f"Modbus error: {error}")

    except OSError as error:
        if not shutdown_requested:
            print(f"Socket error: {error}")

    finally:
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

        try:
            conn.close()
        except OSError:
            pass

        active_connection = None
        print("Gateway connection closed.")


# ============================================================
# TCP server
# ============================================================

def main() -> None:
    global server_socket

    signal.signal(
        signal.SIGINT,
        request_shutdown,
    )

    signal.signal(
        signal.SIGTERM,
        request_shutdown,
    )

    server_socket = socket.socket(
        socket.AF_INET,
        socket.SOCK_STREAM,
    )

    server_socket.setsockopt(
        socket.SOL_SOCKET,
        socket.SO_REUSEADDR,
        1,
    )

    try:
        server_socket.bind((HOST, PORT))
        server_socket.listen(10)
        server_socket.settimeout(1.0)

        print(
            f"Waiting for gateway on "
            f"{HOST}:{PORT}..."
        )
        print("Press Ctrl+C to stop the server.")

        while not shutdown_requested:
            try:
                conn, address = server_socket.accept()

            except socket.timeout:
                continue

            except OSError:
                if shutdown_requested:
                    break

                raise

            handle_gateway(
                conn=conn,
                address=address,
            )

    except KeyboardInterrupt:
        request_shutdown()

    except OSError as error:
        if not shutdown_requested:
            print(f"Server error: {error}")

    finally:
        request_shutdown()

        if server_socket is not None:
            try:
                server_socket.close()
            except OSError:
                pass

        server_socket = None
        print("Server stopped cleanly.")


if __name__ == "__main__":
    try:
        main()
    finally:
        sys.exit(0)
