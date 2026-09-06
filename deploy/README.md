# Deploy

## What triggers a post

A cron on the VPS (31.97.223.43) at **19:00 WIB**, running
`jvto-post-trip.sh`, which calls the workflow's `workflow_dispatch`.

    0 19 * * * /usr/local/bin/jvto-post-trip.sh

The box is on `Asia/Jakarta`, so the cron line reads as WIB with no
conversion. The script passes no inputs, so `force` stays false and the
interval gate applies — the same behaviour the schedule trigger had.

### Why not GitHub's own schedule

Because it is not a schedule, it is a request. GitHub queues `schedule:` runs
behind every other repo's, worst at the top of the hour. Measured:

| asked  | ran   | late    |
|--------|-------|---------|
| 19:00  | 22:49 | 3h 49m  |
| 19:00  | 22:42 | 3h 42m  |
| 19:00  | 22:32 | 3h 32m  |
| 19:00  | 22:30 | 3h 30m  |
| 19:00  | 21:18 | 2h 18m  |

Not one run was on time. Every `workflow_dispatch` in the same history started
in the second it was requested, so the delay is specific to the schedule
trigger. The workflow has no `schedule:` trigger at all now — this cron is the only
thing that starts a post.

**That makes this box a single point of failure.** If it is down, or the cron
is removed, or the token in `.env` expires, nothing posts and nothing says so:
the sheet just stops advancing. There is no second trigger to cover it. Two
things to check when posts stop appearing:

    ssh root@31.97.223.43 'tail /var/log/jvto-post-trip.log'
    ssh root@31.97.223.43 'crontab -l'

A working day writes one `dispatched ...` line. A silent log means the cron
never ran; a `FAILED:` line names the reason.

## Installing it

    scp deploy/jvto-post-trip.sh root@31.97.223.43:/usr/local/bin/
    ssh root@31.97.223.43 'chmod 700 /usr/local/bin/jvto-post-trip.sh'
    ssh root@31.97.223.43 '/usr/local/bin/jvto-post-trip.sh --dry-run'

`--dry-run` checks the token, repo and workflow without dispatching. Use it
whenever the token is rotated: a dispatch cannot be tested by running it,
because running it publishes to the real accounts.

The GitHub token is read from `/var/www/sosmed-studio/.env`, which the studio
panel already needs for its own dispatch button — one copy on the box to
rotate rather than two. Log: `/var/log/jvto-post-trip.log`.
