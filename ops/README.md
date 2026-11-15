Hermes operational fixes

The cloud workflow owns Telegram and the scheduled research jobs. The laptop runs the authenticated localrelay profile. Local default cron copies remain paused for rollback.

The upstream Hermes commit stays pinned. apply_core_patches.py checks its replacement contexts before applying narrow changes. The original gateway delivery ledger and cron delivery queue APIs remain intact. Scheduler ticks hold due occurrences until the gateway reports ready. Optional output_validator scripts run before delivery, with their owning profile environment. The startup digest renderer fetches official pages independently and accepts matching short quotes. The delivered dedup state is committed after a confirmed delivery.

The source host list is intentionally reviewed. A hostname on this list establishes source authority only. It does not establish eligibility, an open application window, funding availability, or successful selection. Add a domain only after checking the organizer's official site. Unknown ENVIID business metrics remain unknown. No personal memory files are rewritten by these helpers.

State backup includes cron ledgers, scripts, pending messages, profiles, plugin source and plugin data, alongside the existing core state. Each SQLite database uses its online backup API and quick_check. The archive stores and verifies content hashes. Regenerable dependency directories, caches, logs, locks, sockets and sidecar WAL files are excluded. No guarantee of an atomic transaction across separate databases is implied. A hard runner loss before checkpoint still has a recovery-point gap.

Critical snapshot, encryption, validation, and Git publication failures stop the persistence step. Only encrypted recovery output may be uploaded as an artifact. Dispatch of a replacement cycle follows successful persistence. A live process or HTTP router response is explicitly not labeled as successful model inference or successful message delivery.

Run isolated operational tests with:

python3 -m unittest discover -s tests -v

Core patch compatibility now runs in CI through verify_core.py against the exact production revision. It checks idempotence, compiles the patched modules, and exercises actual native watchdog code in isolated subprocesses. tests/test_core_resilience.py covers loop sockets, stale worker and cron progress, logging backpressure, corruption escalation and retry behavior. This does not execute the entire upstream plugin and platform suite.

supervise.py replaces the shell heartbeat loop. It runs 9router's custom server in the foreground, applies a fatal Node error preload, bounds probes independently, drains the gateway before stopping its router, and reaps detached descendants. Five failed probes exit 1. The native Hermes watchdog remains the internal backstop. A fresh loop socket response, same-PID heartbeat, current housekeeping, and successful cron ticks are required for readiness. The GitHub receipt includes this progress evidence.

deploy/OPERATIONS.txt contains exact Actions and persistent-host commands. The prepared systemd service uses Restart=always, RestartSec=5, StartLimitIntervalSec=0, a 60-second watchdog, and a 4 GiB memory limit. No service or deployed revision is changed merely by editing these files. The installed Windows runtime is not reset or upgraded.
