# One Bank Liebert PAC trap mappings

Seed the model-scoped Liebert condition notification mappings with:

```sh
python manage.py seed_onebank_liebert_pac_traps --dry-run
python manage.py seed_onebank_liebert_pac_traps
```

The command targets the active PAC at `10.156.0.156` and its database device
model. It registers the Liebert GP Notifications MIB notifications:

| Event code | Trap OID | Meaning |
| --- | --- | --- |
| `PAC_ALARM_FIRED` | `1.3.6.1.4.1.476.1.42.3.3.0.1` | A condition was added to the Liebert conditions table |
| `PAC_ALARM_RESTORED` | `1.3.6.1.4.1.476.1.42.3.3.0.2` | A condition was removed from the Liebert conditions table |

These notifications do not create generic alerts directly. The PAC trap handler
uses them to poll the device's PAC alarm OID mappings and confirm the state,
which avoids treating a possibly duplicated or stale UDP notification as the
alarm state itself.

The OIDs and notification behavior are documented in the [Liebert GP
Notifications MIB](https://mibs.observium.org/mib/LIEBERT-GP-NOTIFICATIONS-MIB/).
The product-specific OID/trap tables for PDX and PCW are in Vertiv's [PDX/PCW
application monitoring guide](https://www.vertiv.com/48ef1e/globalassets/products/thermal-management/room-cooling/vertiv-liebert-pcw-ph-models-chilled-water-floor-mount-cooling-unit/liebert-pdx-and-liebert-pcw-v1a-application-monitoring-user-guide-installer-user-guide-for-liebert-icom-3.pdf).
