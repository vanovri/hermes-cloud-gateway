'use strict';

// Next installs log-only exception listeners. Never continue with corrupted
// router state; the process supervisor owns recovery. Avoid secret-bearing errors.
process.on('uncaughtExceptionMonitor', (_error, origin) => {
  try {
    require('node:fs').writeSync(2, JSON.stringify({
      event: 'router_fatal',
      origin: origin === 'unhandledRejection' ? 'unhandledRejection' : 'uncaughtException',
      time: new Date().toISOString(),
    }) + '\n');
  } finally {
    process.exit(1);
  }
});
