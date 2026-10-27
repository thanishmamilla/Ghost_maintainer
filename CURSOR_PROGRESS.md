# Ghost Maintainer — build progress

## Current status

**All 12 steps complete, plus a hardening pass.** Backend, frontend, demo repository, both product
surfaces, documentation and verification are done.

- Backend: **298 tests pass**
- Frontend: **59 tests pass**, typecheck clean, builds in 4.3s
- Demo repository: **21 Jest tests pass**
- Live scan: 2 rotten, 3 aging, 2 healthy, 15 alerts, 11 with observed usage
- Both migrations verified against the real demo suite

Step 12 is pending only on credentials you said you would provide (`GITHUB_TOKEN`, `LLM_API_KEY`),
which unlock real PR creation and model-assisted reviews. Neither is needed for the demo.

## Step plan

| # | Step | State |
|---|---|---|
| 1 | Foundation: FastAPI skeleton, models, config, dashboard shell | done |
| 2 | Demo rotten repository with passing Jest tests | done |
| 3 | Scanner: clone/local, manifest + lockfile resolution | done |
| 4 | Health engine + Rot Score | done |
| 5 | Reachability analysis | done |
| 6 | Fixer: safe bumps, curated migrations, optional LLM | done |
| 7 | Sandbox: baseline → patch → retest → diff | done |
| 8 | PR bot: dry-run body with evidence, gated real mode | done |
| 9 | Jobs, API routes, SSE | done |
| 10 | Dashboard: graph, package panel, fix console, PR preview | done |
| 11 | Feature 2: PR review agents + learner | done |
| 12 | Hardening, docs, demo script, offline fixtures | done |

## Decisions recorded during the build

- **Location**: built at the workspace root (`backend/`, `frontend/`, `demo-rotten-app/`).
- **Python 3.11** via a `uv` virtualenv. `py` resolves to 3.9 on this machine and must not be used.
- **Tailwind v4** through `@tailwindcss/vite`; theme tokens live in `frontend/src/index.css`.
- **Rot Score interpretation**: staleness measures the *installed* release's publish date, because a
  dependency pinned to a 2019 build is stale even when the project still ships releases. "A newer
  major exists" is the separate version-lag component. Maintainer inactivity uses the repository's
  last push, never the latest release timestamp.
- **Unknown data never counts as neglect.** Each component reports `known=False` when its input could
  not be established, and an unreachable advisory service is reported as unknown rather than as
  "no known vulnerabilities".
- **`moment` is not npm-deprecated.** Only `request` (and `jsonwebtoken@3.2.2`) carry deprecation
  notices. The moment narrative rests on staleness, advisories and version lag.
- **Fixed versions differ per advisory.** `lodash@4.17.15` needs 4.17.23/4.18.0 depending on the
  advisory; the fixer intersects all advisories and takes the lowest same-major version that resolves
  every one of them. It picked 4.18.0, not 4.17.21.
- **Cross-major upgrades are refused, not attempted.** `jsonwebtoken@3.2.2` only has fixes in v4+
  and v9+, so it is offered with `requires_human_review`.
- **Codemods rename identifiers in code only.** Comments and string literals are masked before
  renaming, so prose mentioning `moment` is not rewritten.
- **The demo's HTTP helper keeps its transport injectable.** That is what lets `request → axios` be
  migrated without touching the test suite, and what proves the migration.
- **`npm ci` for the baseline, `npm install` after the patch**, because the manifest change makes the
  lockfile stale and a frozen install would refuse.
- **Windows**: `npm` resolves to `npm.cmd`; the runner resolves executables through `PATH` rather
  than relying on a shell. Vite binds the IPv6 loopback, so `localhost:5173` works and
  `127.0.0.1:5173` does not.
- **Sandbox**: Docker when a daemon is reachable, otherwise the local-restricted runner behind
  `ALLOW_UNTRUSTED_LOCAL_EXECUTION`, labelled "not a security boundary" in the UI and README.
- **Offline fixtures** cover npm, OSV and GitHub, so `OFFLINE_FIXTURES=true` produces identical scan
  findings without any network access.
- **Feature 2 has deterministic rules** for every agent, so the reviewer works with no LLM key. Model
  findings are added only after validation against changed files and lines.

## Verification log

### Step 1 — foundation

| Command | Result |
|---|---|
| `uv venv .venv --python 3.11` | CPython 3.11.7, 31 packages |
| `npm install` (frontend) | 85 packages |
| `python -m pytest` | 3 passed |
| `uvicorn` + `GET /health` | `200 {"status":"ok","version":"0.1.0","dry_run":true}` |
| `npm run build` | tsc clean, 1579 modules, 4.6s |
| `npm run dev` + `GET localhost:5173` | 200, root div present |

### Step 2 — demo repository

| Command | Result |
|---|---|
| `npm install` | 349 packages; npm's own audit flagged the intended deprecations |
| `npm test` | 5 suites, 21 tests passing, 4.4s, no network |
| lockfile | v3, all six versions pinned as intended |
| `python -m app.fixtures_capture` | npm + OSV + GitHub captured, 0.3 MB after trimming |
| `python -m pytest` | 28 passed |

### Steps 3–6 — analysis and repair

| Area | Verification |
|---|---|
| Scanner | 55 tests; manifest parsing, lockfile v1/v2/v3, unresolved versions, error paths |
| Health + Rot Score | 118 tests; every boundary (1095/730/365 days, 30/59/60 labels), unknown-data paths |
| Reachability | 152 tests; import/require/dynamic/side-effect forms, exclusions, `not_observed` |
| Fixer + codemods | 183 tests; bump intersection, refusal paths, both codemods |
| Live demo scan | 7 deps → 2 rotten, 3 aging, 2 healthy, 15 alerts, 11 with observed usage |

### Step 7–9 — execution, PR and API

| Command | Result |
|---|---|
| `python tests/verify_migrations.py` | `moment migration: PASS`, `request migration: PASS` — both against the real demo suite |
| `python tests/e2e_demo.py` | scan 8.1s; lodash bump 4.17.15→4.18.0 sandbox passed; `moment→dayjs` sandbox passed, 88-line diff |
| `python -m pytest` | 294 passed |
| Sandbox timeout test | process tree killed, no orphan process, no marker file written |
| API validation | path traversal, shell metacharacters, unknown jobs, unfinished scans all rejected |

### Step 10–11 — dashboard and reviewer

| Check | Result |
|---|---|
| `npm run build` | tsc clean, 1586 modules, ~190 kB initial + 373 kB lazy graph chunk |
| Live scan over HTTP | `POST /api/scan {"repo_url":"demo"}` → 202 with a job id |
| Review of a real PR | `expressjs/express#7561` → 4 agents, succeeded, dry run, no findings |
| Review of a synthetic bad diff | finds eval, hardcoded secret, debug logging, empty catch |
| Convention handling | a `var`-heavy file no longer produces `var` findings |

### Step 12 — hardening pass

Added a frontend test suite (Vitest + Testing Library + jsdom, 59 tests) that renders the dashboard
against a mocked API using the real backend response shape. It found four live bugs:

| Bug | Effect before the fix |
|---|---|
| Graph click emitted `pkg:lodash` | Detail panel looked up the wrong key; highlight broke (`pkg:pkg:lodash`) |
| `new EventSource(...)` unguarded | Fix console crashed where EventSource is unavailable, instead of polling |
| `scrollIntoView` unguarded | Threw where the API is missing |
| `PRPreview` returned early without a PR | A failed sandbox showed no test evidence at all |

Other work in this pass:

| Change | Result |
|---|---|
| `OFFLINE_FIXTURES` verified end to end | Identical scores to the live run, **zero** HTTP requests; now covered by two tests |
| Cytoscape lazy-loaded | Initial bundle 578 kB → **190 kB** |
| Workspace pruning on startup | Prevents 348 MB of stale temp copies accumulating; covered by two tests |
| `UsageMatch.specifier` typed in the client | The backend already returned it; the TypeScript interface was missing it |
| `FixRequest.fix_type` | The UI can now choose a bump or a replacement explicitly |

## Known issues

- Docker Desktop is not running on this machine, so the sandbox uses the local-restricted runner.
  Start Docker Desktop to exercise the container path.
- The learner store and the job store are process-local. Restarting the backend clears jobs; the
  learner persists in `backend/.workspaces/learner.json`.
- Webhook dedupe keys live in the job store and expire with it, so a re-delivery after a restart
  would be reviewed twice.
- `StarletteDeprecationWarning: install httpx2` comes from FastAPI's TestClient. Harmless.
- Dashboard behaviour is verified by rendering against a mocked API and by the backend's end-to-end
  script, not by a real browser session — no browser driver is available in this environment.

## Next concrete action

1. Add `GITHUB_TOKEN` + `DRY_RUN=false` to `backend/.env` and push a copy of `demo-rotten-app/` to a
   repository you own, then confirm a real pull request opens.
2. Add `LLM_API_KEY` + `LLM_MODEL` to enable model-assisted review findings.
3. Rehearse the three-minute script in `README.md` and record a backup video.
### Follow-up � free LLM providers

Checked every candidate endpoint live rather than trusting documentation:

| Endpoint | Result |
|---|---|
| npm registry, OSV, deps.dev, npm downloads, OpenSSF Scorecard | 200, no auth, ~0.2�0.7s |
| GitHub REST (unauthenticated) | 60 requests/hour |
| Groq, Cerebras, Mistral, Gemini compat | reachable, 400/401 without a key |
| LLM7.io | reachable, needs a real key from token.llm7.io |
| Pollinations (claimed keyless) | answered once, then 402/500 � not dependable |
| GitHub Models | **retired 30 July 2026**, endpoint gone |

Two client fixes came out of this:

- A provider that lacks `response_format` is retried without it, so JSON-mode review findings work on
  every free tier instead of only the ones that implement the hint.
- Error bodies are included in the message. "returned 400" was useless; "returned 400: model `x` does
  not exist" tells you what to change.

| Change | Result |
|---|---|
| `python -m app.llm_check` | Prints config, masks the key, probes the provider, lists free presets |
| Backend tests | 321 passed (20 new LLM tests) |

### Final pass � demo readiness

The fix pipeline was too slow to record: a cold `npm ci` inside the sandbox accounted for ~70 s of a
~90 s run. Measured breakdown and fix:

| Step | Before | After |
|---|---|---|
| `npm ci` in a fresh copy | 70.7 s | skipped |
| copying `node_modules` (8,000 files, hard links) | � | 5.2 s |
| `npm test` | 4.8 s | 4.8 s |
| `npm install` after the patch | 44 s | 4.1 s |
| **Fix end to end** | **96.6 s** | **16.1 s** |

The sandbox reuses the working copy's installed dependencies when a lockfile is present, records that
decision in `SandboxResult.notes`, and prints it in the pull-request body. Cloned repositories have no
`node_modules`, so they still get a full install.

Also fixed in this pass:

- `FixResult.logs` was always empty while the real log lived on the job; both now carry the same entries.
- A replacement plan's explanation repeated "Replace � Replace �".
- Added `scripts/demo.ps1` (installs, starts, waits, opens the dashboard, `-Offline` switch) and
  `DEMO.md` (shot-by-shot script with timings and recovery table).

Verified with both servers running over real HTTP:

| Check | Result |
|---|---|
| `GET /health` | ok, dry_run, sandbox auto |
| `GET /localhost:5173` | 200 |
| `POST /api/scan` + poll | succeeded, 7 deps, 2 rotten / 3 aging / 2 healthy, 15 alerts |
| `POST /api/fix` lodash | succeeded, 4.17.15 ? 4.18.0, baseline+post-fix passed, 16.1 s |
| `POST /api/fix` moment (replacement) | succeeded, 2 files, 15.6 s, 9 log entries |
| Backend tests | 325 passed |
