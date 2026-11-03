---
name: run-ina
description: Run, start, launch, drive, or screenshot the ina site in a real browser — serve it against the throwaway test database, seed links, run their media jobs, then click expandos, check players and the CSP with the headless Chromium driver. Use to confirm a template, JS, CSS, CSP or embed change works in the running app, not just in tests.
---

# Run ina

ina is a Litestar app (see `CLAUDE.md`). The agent path: serve it on **:8765 against the test
database** (`ina_test`, never the dev data), seed with `seed.py`, and drive it with `driver.mjs`,
a headless-Chromium script runner that reads commands on stdin. Paths below are relative to the
repo root. Verified on macOS (arm64) with the compose services under Docker.

## Prerequisites

`mise`, Docker, and Node (mise installs Node). Once per machine, install the driver's one
dependency outside the repo, plus the browser build that matches it:

```sh
D="${TMPDIR:-/tmp}/ina-run"; mkdir -p "$D" && npm i --silent --prefix "$D" playwright-core@1.64.1 && (cd "$D" && npx playwright-core install chromium)
```

## Start

```sh
mise run services                                   # postgres, redis, rabbitmq, opensearch, seaweedfs...
uv run pytest tests/repos/test_links.py -q          # migrates ina_test and wipes it: a clean slate
source .claude/skills/run-ina/env.sh
lsof -ti:8765 -sTCP:LISTEN | xargs kill 2>/dev/null
nohup uv run litestar run --port 8765 > "${TMPDIR:-/tmp}/ina-run/server.log" 2>&1 &
for i in $(seq 30); do curl -sf -o /dev/null "$INA_RUN_URL/" && break; sleep 1; done
```

## Seed

Registers `tester` (password `hunter2hunter2`), creates `in/pics`, submits `Title=URL` links,
runs each link's `find_embed` job in-process, and prints what `media` each got:

```sh
PYTHONPATH=. uv run python .claude/skills/run-ina/seed.py \
  'YouTube=https://www.youtube.com/watch?v=dQw4w9WgXcQ' \
  'X=https://x.com/jack/status/20' \
  'Bluesky=https://bsky.app/profile/bsky.app/post/3l6oveex3ii2l' \
  'Flower=https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4'
```

## Drive (agent path)

```sh
source .claude/skills/run-ina/env.sh
node .claude/skills/run-ina/driver.mjs <<'EOF'
goto /in/pics/
open YouTube
open X
open Bluesky
open Flower
frames
shot pics
shot x-post .thing:has(a.title:text-is("X"))
login tester hunter2hunter2
goto /in/pics/
text .user
report
EOF
```

Commands: `goto`, `login <user> <pw>`, `click <css>`, `open <exact link title>` (clicks its
expando and waits 6s for the player), `wait <ms>`, `text <css>`, `eval <js>`,
`shot <name> [css]`, `frames` (each open player's height, whether it resized itself, and the text
inside it), `report` (CSP violations on ina's page and uncaught errors). Screenshots land in
`$TMPDIR/ina-run/shots/`. **Look at them.** The exit code is 1 if any command failed.

A good run prints rows like
`{"tag":"iframe","height":225,"resized":"225px",...} | jack @jack · Follow just setting up my twttr`,
then `CSP violations on ina: none`.

## Stop

```sh
lsof -ti:8765 -sTCP:LISTEN | xargs kill
```

## Human path

`mise run dev` serves the dev database at http://localhost (via Traefik). `mise run worker`
runs jobs from RabbitMQ. Use it for browsing by hand, not for agent runs.

## Gotchas

- **Don't let the server queue jobs.** `env.sh` sets `INA_EMBEDS=false` and
  `INA_THUMBNAILS=false`. Otherwise submits publish to the shared RabbitMQ, and a dev worker
  picks them up and runs them against the *dev* database, where the link IDs don't exist.
  `seed.py` calls the job functions directly instead.
- **Use playwright-core 1.64.x and `channel: 'chromium'`.** The default headless shell, and
  1.56's Chromium build (`chrome-mac`, open-source Chromium), have no H.264, so linked `.mp4`
  files fail with `readyState=0 error=4`. 1.64's build is Chrome for Testing, which plays them.
- **Open expandos one at a time and wait.** Clicking several expando buttons back to back misses
  some: players above load and grow, and the next button moves. `open` waits 6s after each.
- **Only ina's own CSP is reported.** Each embedded site's frame enforces its own policy and logs
  its own console noise, which says nothing about ina.
- **The test database is wiped by every DB test.** Seed again after running pytest.
- **Embeds need the internet.** Short links and Bluesky handles are looked up live by
  `find_embed`, and the players load from the real sites.

## Troubleshooting

- `{"tag":"video",...} | readyState=0 error=4`: wrong browser build. Reinstall with the
  Prerequisites line (playwright-core 1.64.1 + `install chromium`).
- `ModuleNotFoundError: No module named 'tests'` from `seed.py`: run it with `PYTHONPATH=.`
  from the repo root.
- `seed.py` prints `not submitted: [...ALREADY_SUB...]`: that URL is already in `in/pics` from
  an earlier run. Wipe with the pytest line under Start, then seed again.
