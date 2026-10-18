# Outbound-connected Modbus RTU gateways

The reverse gateway collector supports a gateway that initiates a persistent TCP connection to the backend, while the backend remains the Modbus client and polls RTU devices through that connection. It accepts transparent Modbus TCP ADUs only; gateway-specific login banners, framing, or proprietary protocols are not supported.

## Network and security

Run the collector as its own process with `docker compose up -d --build modbus-gateway-listener`. The listener defaults to port `4010`. Set `MODBUS_GATEWAY_PUBLISH_BIND` to the backend host's private/VPN interface address in `.env`, and configure the gateway to connect to that address and port. The compose default binds only to loopback. Do not publish this port on a public interface.

Use WireGuard, IPsec, or an equivalent authenticated VPN between each gateway and the backend. Give each gateway a stable, unique VPN source IP and register that exact address in Admin. The collector rejects connections from unregistered/inactive source IPs and rejects a second concurrent session for the same registered gateway. Since the transparent Modbus bridge protocol has no application authentication, the VPN peer identity and source-IP allowlist are the trust boundary. Do not rely on source-IP allowlisting over the public internet.

## Django Admin setup

1. Create the gateway under **Devices → Modbus gateways**. Set its organization, data center, unique code, name, and stable VPN source IP. Connection status and last connection/error fields are maintained by the listener.
2. Create each physical battery as a normal **Devices → Devices** record. The battery remains the telemetry owner; the gateway is its transport path.
3. Add a **Devices → Device protocol configs** row for each battery. Select **Modbus RTU Gateway**, choose the registered gateway, and enable it. Host and port are intentionally unused for this reverse connection.
4. Create an active **Devices → Polling profiles** row with protocol **Modbus RTU Gateway** and the desired polling interval, timeout, retries, and stale threshold. Assign it to the battery under **Devices → Device polling configs** and enable polling.
5. Create the battery's metric definitions, then add **Devices → Modbus register mappings** for its device type/model. Set the battery's RTU slave ID, function code, register address/count, data type, byte/word order, scale, offset, and unit. Mappings may be model-specific or shared by device type.

The listener polls all enabled batteries associated with the connected gateway according to each battery's polling profile. It validates transaction ID, unit ID, function code, byte count, and register values. Good/partial readings are written through the normal telemetry writer, so both telemetry history and Latest Telemetry update and alert rules are evaluated. Each attempt creates a `TelemetryIngestLog` row; the device polling config tracks consecutive failures and the latest error.

## Battery script compatibility

`onebank/battery/battery_modbus_tcp_server.py` uses a transparent byte stream, function code 3, and fixed unit ID 1, so the gateway must tunnel Modbus TCP requests/responses without adding a protocol preamble. The production collector takes the unit ID and register map from Admin. It does not run that diagnostic script or use its hardcoded SOH value. Confirm RS-485 serial settings and the gateway's register-address convention against the battery manual before enabling polling.

## Operations

- `docker compose logs -f modbus-gateway-listener` shows connection acceptance/rejection and transport failures.
- In **Devices → Modbus gateways**, `connection_status` and `last_error` show the session state.
- In **Devices → Device polling configs**, `last_polled_at`, `consecutive_failures`, and `last_error_message` show per-battery health.
- In **Telemetry → Telemetry ingest logs**, filter protocol `MODBUS_RTU_GATEWAY` to inspect poll outcomes.
- The standard stale-device reconciliation marks batteries degraded/offline when readings stop arriving.

Configure database migrations before starting the new listener: `docker compose exec api python manage.py migrate`.
