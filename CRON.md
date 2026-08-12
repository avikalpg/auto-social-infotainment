# Cron invocations

Do not install these automatically. Each invocation is isolated and guarded by the repo lock file.

Example every 15 minutes:

```cron
*/15 * * * * cd /opt/auto-social-infotainment && /usr/bin/flock -n state/cron.lock .venv/bin/workflow-automation resume >> logs/cron.out 2>> logs/cron.err
```

Recommended split by stage if adapters run on different hosts:

```cron
5 * * * * cd /opt/auto-social-infotainment && /usr/bin/flock -n state/extract.lock .venv/bin/workflow-automation extract-story >> logs/extract.out 2>> logs/extract.err
20 * * * * cd /opt/auto-social-infotainment && /usr/bin/flock -n state/video.lock .venv/bin/workflow-automation produce-video >> logs/video.out 2>> logs/video.err
35 * * * * cd /opt/auto-social-infotainment && /usr/bin/flock -n state/publish.lock .venv/bin/workflow-automation publish-instagram >> logs/publish.out 2>> logs/publish.err
```

The CLI also creates `state/workflow.lock`; `flock` is an outer guard for cron overlap protection.
