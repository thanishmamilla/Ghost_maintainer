# Recording the demo

Everything below assumes a single command was run first:

```powershell
.\scripts\demo.ps1
```

It installs anything missing, starts both servers, waits for them, prints the mode line, and opens
the dashboard. For a recording you can also use `.\scripts\demo.ps1 -Offline`, which switches the scan
to the captured fixtures so it cannot be affected by a registry outage.

Measured timings on this machine, with the demo repository's `node_modules` already installed:

| Action | Time |
|---|---|
| Scan the demo repository | ~7 s |
| Fix `lodash` (version bump) | ~16 s |
| Fix `moment` (package replacement) | ~20 s |

That leaves plenty of room inside three minutes, and none of the waits are dead air — the fix console
streams real backend events as they happen.

---

## Before you press record

1. Run `.\scripts\demo.ps1` and wait for `Ready.`
2. Confirm the header chips read **dry-run**, **sandbox auto**, **API online**.
3. Close anything that shows notifications.
4. Set the browser to 1440×900 or larger; the layout is designed for a laptop width.
5. Have the repository list pre-warmed: press **Demo repo** once, let it finish, then reload the page.
   The second scan is the one you record, so the first one is not visible.

---

## The three-minute script

### 0:00–0:25 — The hook

> "Every app is built on code nobody maintains. A single abandoned package, `event-stream`, was used
> to steal cryptocurrency from thousands of apps. This is Ghost Maintainer: it adopts the orphans."

Press **Demo repo**. While it runs (~7 s), say:

> "It resolves direct dependencies from the lockfile, then asks npm, OSV, GitHub and deps.dev about
> each one."

### 0:25–0:55 — Show the rot

Six summary cards appear: 7 packages, 2 rotten, 3 aging, 2 healthy, 15 advisories, 11 attached to code
that is actually imported.

> "Notice the last number. Fifteen alerts, but only eleven sit on packages this app actually imports."

Click **jsonwebtoken** — the red node, or the top row of the table.

> "Rot 90. Its release is eleven years old, npm marks it deprecated, it carries four advisories
> including a critical one, and it's three major versions behind."

Point at the reachability panel.

> "And no source file imports it. But we do not say 'safe'. We say *not observed*, and explain that it
> may still be reached transitively or dynamically."

### 0:55–1:20 — The insight

Click **lodash**.

> "This one is different. Rot 48, six advisories — and three real call sites, with file and line
> numbers. This is the alert you actually have to fix."

Point at the breakdown.

> "The score is transparent: staleness, abandonment, advisories, maintainer activity, version lag.
> Every component shows its own points and its reason."

### 1:20–2:05 — The fix

Press **Adopt fix** on `lodash`.

> "It plans the smallest same-major upgrade that resolves *every* advisory — not the first fix it
> finds. Then it copies the repo, runs the baseline tests, applies the patch, and runs them again."

Watch the console fill in. When it finishes (~16 s):

> "Baseline: 21 tests passing. After the fix: 21 tests passing. 4.17.15 to 4.18.0."

Point at the diff, then note the PR preview:

> "And here is the pull request it prepared, with the score, the advisory evidence, the call sites,
> and both test runs attached."

### 2:05–2:45 — The migration

Select **moment**, press **Adopt fix** on the *package replacement* option (the second button).

> "Version bumps are the easy case. This one is abandoned, so it rewrites the code instead:
> `package.json` and `src/dates.js` change, `dayjs` is installed, and the same tests prove the
> rewrite behaves identically."

The diff shows the import and call sites changing.

### 2:45–3:00 — Close

> "Ghost Maintainer never claims more than it knows. Unobserved is not unreachable, a score of zero is
> not safe, and no pull request is opened unless the tests pass. Scale that to every open-source
> project and the supply chain gets healthier on its own."

If a real PR is configured, open it here instead and show the green checks.

---

## Optional: show Feature 2

Switch to the **PR review** tab and enter `expressjs/express#7561`, then press **Review**.

> "The same product reviews new code. Four agents — static, security, architecture, style — run over
> the added lines and post inline comments. With no model key they run on rules; with one, model
> findings are validated against the diff before they are posted."

The result is honest: that pull request is clean, and the reviewer says so rather than inventing
findings.

---

## If something goes wrong

| Situation | Recovery |
|---|---|
| Scan fails or is slow | Restart with `.\scripts\demo.ps1 -Offline` |
| A fix returns `baseline_failed` | Do not narrate it as user error; say the repo's own tests were already failing, which is exactly why the tool refuses to blame its patch |
| Fix hangs | The sandbox caps each command at 180 s and kills the process tree |
| Dashboard says "API offline" | The backend window has an error; restart it |
| Nothing loads at `127.0.0.1:5173` | Use `http://localhost:5173` — Vite binds the IPv6 loopback |

Always keep one clean recorded run as a backup file. Every external dependency in the demo is either
cached on disk or replaceable by fixtures, so a recorded run cannot be invalidated later.
