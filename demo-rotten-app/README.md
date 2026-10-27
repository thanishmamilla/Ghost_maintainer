# demo-rotten-app

A deliberately rotten Node.js application used to demonstrate Ghost Maintainer end to end.

It is small, deterministic and self-contained: `npm test` never touches the network, and every
finding it produces is backed by a real advisory in the npm registry or OSV.dev.

## What is wrong with it

| Package | Locked | Problem | Intended Ghost Maintainer fix |
|---|---|---|---|
| `moment` | 2.29.1 | Two HIGH advisories (path traversal, ReDoS); project is in maintenance mode | Package replacement to `dayjs` |
| `request` | 2.88.2 | Deprecated on npm; last published 2020; one MODERATE advisory | Package replacement to `axios` |
| `lodash` | 4.17.15 | Six advisories, highest HIGH; prototype pollution and ReDoS | Safe same-major bump to `4.18.1` |
| `minimist` | 1.2.0 | CRITICAL prototype-pollution advisory (GHSA-xvch-5gv4-984h) | Safe same-major bump to `1.2.8` |
| `jsonwebtoken` | 3.2.2 | CRITICAL advisory (GHSA-c7hr-j4mj-j2w6) | Safe bump — **never imported anywhere in `src/`** |
| `semver` | 7.8.5 | None. Recent publish, active repo, no advisories | Healthy baseline, no action |

`jsonwebtoken` is the deliberate "installed but not observed" case. It is a real dependency with a
CRITICAL advisory, yet no source file imports it. Ghost Maintainer must report it as
`not_observed` and explain that a source scan cannot prove it is unreachable — it may still be
reached transitively, dynamically, or through files the scanner does not parse.

## Source layout

```text
src/
  dates.js      moment        — formatting, add/subtract, diff
  objects.js    lodash        — deep merge helpers
  args.js       minimist      — CLI argument parsing
  http.js       request       — promise-wrapped HTTP GET helper
  versions.js   semver        — version comparison helpers
  index.js      —             — public surface
tests/
  dates.test.js     7 assertions
  objects.test.js   3 assertions
  args.test.js      4 assertions
  http.test.js      4 assertions (network fully mocked)
  versions.test.js  3 assertions
```

## Running the tests

```bash
npm install
npm test
```

Expected: **5 suites, 21 tests, all passing.** The suite is deterministic and requires no
network access — the HTTP helper is exercised through a mocked `request` module.

## Publishing a copy to GitHub for real pull-request demos

Ghost Maintainer can open a real pull request only against a repository you own, with a token
that has `repo` scope. To demo that path:

1. Create a new empty GitHub repository (for example `ghost-maintainer-demo`).
2. Copy this directory's contents into it and push:

   ```bash
   git init
   git add .
   git commit -m "Import rotten demo application"
   git branch -M main
   git remote add origin https://github.com/<your-user>/<your-repo>.git
   git push -u origin main
   ```

3. Do **not** commit `node_modules/` — commit `package.json` and `package-lock.json` so the
   scanner can resolve exact installed versions.
4. Point Ghost Maintainer at `https://github.com/<your-user>/<your-repo>` and set
   `GITHUB_TOKEN` plus `DRY_RUN=false` in `backend/.env`.

Until then, `DRY_RUN=true` (the default) produces a complete pull-request preview with the diff
and test evidence, and performs no write requests against GitHub.
