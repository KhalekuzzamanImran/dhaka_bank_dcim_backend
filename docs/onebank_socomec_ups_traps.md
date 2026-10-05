# OneBank Socomec UPS SNMP traps

`python manage.py seed_onebank_socomec_ups_traps` seeds the 42 SMIv1 UPS trap
definitions in `mibs/onebank/ups/NetVision-8.11.mib` as model-specific
`SNMPTrapOIDMapping` rows for Socomec UPS models. It is safe to rerun; use
`--dry-run` to check scope first. OIDs use the SMIv1 canonical form
`1.3.6.1.4.1.4555.1.1.7.2.0.<specific-trap-number>`; the `7.2` branch is
the MIB's `upsTraps` enterprise identifier.

The MIB description supplies each event's severity and message. Informational
traps are stored as device events without opening alerts. Warning and critical
traps open alerts. MIB-defined restoration notifications for battery power,
agent communication, and EMD sensor conditions resolve their corresponding
active alert event codes. The generic alarm-table removal trap is recorded as
an informational event; it is not used to clear all alarm alerts because it
does not identify a specific mapped alarm event code.

The NET VISION 8.3 manual, section 15.8, says the receiver must be configured
with the NMS address and the NET VISION MIB trap type. Its severity filter
sends all traps at `Information`, warning and critical traps at `Warning`, or
only critical traps at `Critical`; event filtering can further limit which
notifications are sent. Select `Information` if restoration and informational
events should also reach DCIM. The manual also notes that enabling the NET
VISION Control SNMP TRAP/email filter suppresses trap 3 and trap 4 when
severity filtering is used.

These database mappings describe Net Vision 8 traps. The onboarded card was
identified as Net Vision v6.30, and the available poll walk showed RFC 1628
objects rather than the private `1.3.6.1.4.1.4555.1.1.7` branch. Therefore the
seed adds correctly defined mappings, but the card/version's actual trap OIDs
and receiver settings still need to be verified with a received trap before
claiming end-to-end trap delivery.
