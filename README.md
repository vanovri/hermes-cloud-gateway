Hermes Cloud Gateway

Runs Hermes commit 2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19 and 9router 0.5.112.
The separate laptop relay profile remains independent.

Actions restores encrypted state, applies checked core patches, then runs
ops/supervise.py for at most 17,400 seconds. The supervisor owns the foreground
router and gateway process trees. It checks actual event-loop, cron, housekeeping,
Telegram-adapter and router progress. Probe subprocesses have ten-second deadlines.
Five consecutive unhealthy probes after startup cause exit 1 and bounded cleanup.

Gateway shutdown keeps the router available during drain. Detached descendants
are killed and reaped. Captured output and diagnostic dumps rotate at 20 MiB
with five backups. The application logging queue holds at most 2,048 records.

After successful restore, persistence runs even when the gateway fails. It uses
SQLite online backups, content hashes, encrypted recovery artifacts, and atomic
rotation of state/live plus two backup refs. A separate workflow recovers failed
cycles and checks every five minutes. Failed snapshots cannot replace valid state.

Actions schedules can be delayed or dropped. Runner handoffs have a service gap.
Hard runner loss can lose changes since the previous completed state publication.
This design does not establish uninterrupted availability or zero data loss.

The persistent-host alternative is deploy/hermes.service. It provides restart
supervision, a systemd watchdog, memory limits and reboot startup via linger.
See deploy/OPERATIONS.txt for exact deployment, reload and monitoring commands.
Never run cloud and server Telegram consumers simultaneously.

Linux tests:
python3 -m unittest discover -s tests -v

Pinned-source compatibility and native watchdog drills:
python3 ops/verify_core.py --core /path/to/pinned/hermes-agent

HERMES_BUNDLE_KEY is the existing repository secret for state encryption.
Never commit credentials, plaintext state, or the key. Deployment does not
change personal memories or the pinned runtime revision.
