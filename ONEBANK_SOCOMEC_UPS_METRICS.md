# OneBank Socomec UPS metrics

This note records which metrics used by the frontend UPS views can be read from
the onboarded Socomec UPS, and which cannot be mapped from the available SNMP
walk.

## Device and source

- Device: UPS 01 (`10.156.0.157`), Socomec MASTERY 3/3 UPS 60 kVA.
- SNMP card: Net Vision v6.30; the walk responds to standard RFC 1628 UPS-MIB
  objects.
- Walk: `snmpwalks/onebank/socomec_ups/snmp_walk.txt`.
- Seeder: `python manage.py seed_onebank_socomec_ups_metrics`.
- The bundled `NetVision-8.11.mib` enterprise branch used for additional
  NetVision objects is not exposed by this card's walk. The mappings below use
  OIDs observed in the walk.

## Frontend metrics available from the walk

The database contains 27 direct, model-specific OID mappings. The phase table
uses RFC 1628 input/output table indexes 1, 2, and 3 for L1, L2, and L3.

| Frontend metric code(s) | RFC 1628 OID(s) | Notes |
| --- | --- | --- |
| `ups_battery_capacity_percent`, `ups_battery_charge` | `1.3.6.1.2.1.33.1.2.4.0` | Estimated battery charge; both frontend codes map to this same value. |
| `ups_battery_voltage` | `1.3.6.1.2.1.33.1.2.5.0` | Battery voltage; scaled by `0.1` from the RFC's decivolt value. |
| `ups_battery_temperature_celsius` | `1.3.6.1.2.1.33.1.2.7.0` | Battery temperature in Celsius. |
| `ups_abnormal_conditions` | `1.3.6.1.2.1.33.1.6.1.0` | Active alarm count (`upsAlarmsPresent`). |
| `ups_input_frequency` | `1.3.6.1.2.1.33.1.3.3.1.2.1` | Input frequency from phase/index 1. |
| `ups_input_voltage` | `1.3.6.1.2.1.33.1.3.3.1.3.1` | Compatibility metric reads input line 1. |
| `ups_input_l{1,2,3}_voltage` | `1.3.6.1.2.1.33.1.3.3.1.3.{1,2,3}` | Per-phase input voltage. |
| `ups_input_l{1,2,3}_current` | `1.3.6.1.2.1.33.1.3.3.1.4.{1,2,3}` | Per-phase input current, scaled by `0.1` from RFC deciamps. |
| `ups_output_frequency` | `1.3.6.1.2.1.33.1.4.2.0` | Output frequency. |
| `ups_output_status` | `1.3.6.1.2.1.33.1.4.1.0` | RFC 1628 source enum: `3` normal, `4` bypass, `5` battery. |
| `ups_output_voltage` | `1.3.6.1.2.1.33.1.4.4.1.2.1` | Compatibility metric reads output line 1. |
| `ups_output_l{1,2,3}_voltage` | `1.3.6.1.2.1.33.1.4.4.1.2.{1,2,3}` | Per-phase output voltage. |
| `ups_output_current` | `1.3.6.1.2.1.33.1.4.4.1.3.1` | Compatibility metric reads output line 1. |
| `ups_output_l{1,2,3}_current` | `1.3.6.1.2.1.33.1.4.4.1.3.{1,2,3}` | Per-phase output current, scaled by `0.1` from RFC deciamps. |
| `ups_output_l{1,2,3}_percent_load` | `1.3.6.1.2.1.33.1.4.4.1.4.{1,2,3}` | Per-phase output load percentage. |
| `ups_output_kva_capacity` | `1.3.6.1.2.1.33.1.9.5.0` | Configured nominal VA, scaled by `0.001` to kVA. |

The frontend overview also uses `ups_load_percent`. RFC 1628 provides load per
phase rather than a scalar total, so the SNMP poller stores this as a derived
metric: the arithmetic mean of the three phase load percentages. It is not a
direct OID mapping.

## Frontend metrics not mapped

| Frontend metric | Reason |
| --- | --- |
| `ups_battery_runtime_remaining` | The device reports `65535` (unavailable) for remaining runtime in the observed walk. |
| `ups_bad_battery_pack_count` | No corresponding object was found in the available RFC 1628 walk. |
| `ups_output_redundancy` | No corresponding object was found in the available RFC 1628 walk. |

## Verification

The seeder is safe to rerun; it updates existing metric definitions and
model-specific mappings. The current seed contains 28 metrics (27 direct
mappings and one derived metric). Battery voltage uses the walk's value `544`,
scaled to `54.4 V`. Output status follows RFC 1628 enum values. Phase currents
use RFC deciamp scaling. The verified poll after adding the battery-voltage and
output-status mappings returned `SUCCESS` with 29 successful readings and zero
failures. Latest stored output-source status is `3` (`normal`); battery
voltage is `54.4 V`, and the derived load is `5.333333%` from phase loads `4%`,
`5%`, and `7%`, all with `GOOD` quality.
