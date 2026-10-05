# One Bank PAC onboarding

Run `python manage.py onboard_onebank_pac --dry-run` to inspect the records,
then `python manage.py onboard_onebank_pac` to create or update the device and
related SNMP records. The command reads `snmpwalks/onebank/pac/snmp_walk.txt`
and records the PAC under `ONE_BANK/PDC`, using the same room and rack as the
onboarded UPS when available.

The saved walk reports `sysObjectID` `1.3.6.1.4.1.476.1.42`, the Vertiv/Liebert
Global Products branch. The [IANA enterprise registry](https://www.iana.org/assignments/enterprise-numbers/)
identifies enterprise 476 as Vertiv (formerly Emerson Computer Power), and
[Vertiv's Liebert MIB page](https://www.vertiv.com/en-ca/support/software-download/monitoring/management-information-bases-mibs-for-liebert-products/)
provides the vendor's MIB package. The walk's `sysDescr` is `Uninitialized`,
so it does not identify a specific PAC model. The database therefore records
Vertiv/Liebert as vendor and labels the model as unspecified.

The local `mibs/onebank/pac` directory contains an SNMP MIB Browser video but
no text MIB. The PAC's live `snmpwalk` and the read-only `/root/pac_snmp_report.sh`
provide the source OIDs. Set `PAC_SNMP_COMMUNITY` in the environment, then
seed its frontend telemetry and SNMP polling with:

```sh
python manage.py seed_onebank_liebert_pac_metrics
```

The command stores the v2c community encrypted, enables the SNMP endpoint and
60-second polling profile, and seeds only model-specific metrics verified on
this device. Compressor status uses the script's status-table leaves
`1.3.6.1.4.1.476.1.42.3.9.20.1.20.1.2.1.5264.1` and
`1.3.6.1.4.1.476.1.42.3.9.20.1.20.1.2.1.5264.2`; the on/off strings are
normalized to booleans for `pac_compressor_1_status` and
`pac_compressor_2_status`, which the PAC detail page reads directly.

The separate `seed_onebank_liebert_pac_traps` command registers the Liebert GP
condition-added and condition-removed notifications. See
[`onebank_liebert_pac_traps.md`](onebank_liebert_pac_traps.md) for those trap
OIDs and the confirmation-poll behavior.
