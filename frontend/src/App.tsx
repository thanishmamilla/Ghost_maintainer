import { Suspense, lazy, useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Ghost,
  Loader2,
  Radar,
  RefreshCw,
  ShieldCheck,
  Wrench,
} from "lucide-react";
import { RepoInput } from "./components/RepoInput";
import { PackageCard } from "./components/PackageCard";
import { ReachabilityPanel } from "./components/ReachabilityPanel";
import { FixConsole } from "./components/FixConsole";
import { PRPreview } from "./components/PRPreview";
import { ReviewPanel } from "./components/ReviewPanel";

// Cytoscape is only needed once a scan has results, so it is loaded on demand
// rather than in the initial bundle.
const DependencyGraph = lazy(() =>
  import("./components/DependencyGraph").then((module) => ({ default: module.DependencyGraph })),
);

function GraphSkeleton() {
  return (
    <div className="panel flex h-[380px] items-center justify-center">
      <p className="text-xs text-muted">Loading the dependency graph�</p>
    </div>
  );
}
import {
  ApiError,
  DEMO_REPO,
  fixStreamUrl,
  getFix,
  getHealth,
  getScan,
  parseRepoName,
  startFix,
  startScan,
} from "./api";
import type {
  DependencyReport,
  FixResult,
  FixType,
  Health,
  JobState,
  LogEntry,
  RiskLabel,
  ScanResult,
} from "./api";

type Phase = "idle" | "loading" | "error" | "done";
type Connection = "checking" | "online" | "offline";

const RISK_TEXT: Record<RiskLabel, string> = {
  rotten: "text-critical",
  aging: "text-medium",
  healthy: "text-ok",
};

const RISK_BORDER: Record<RiskLabel, string> = {
  rotten: "border-critical/40 bg-critical/10",
  aging: "border-medium/40 bg-medium/10",
  healthy: "border-ok/40 bg-ok/10",
};

function Chip({ label, value }: { label: string; value: string }) {
  return (
    <span className="hidden items-center gap-1.5 rounded-md border border-line-soft bg-panel-2/70 px-2 py-1 text-[11px] sm:inline-flex">
      <span className="text-muted">{label}</span>
      <span className="font-mono text-ink">{value}</span>
    </span>
  );
}

function SummaryCard({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: number | string;
  hint?: string;
  tone?: string;
}) {
  return (
    <div className="panel px-4 py-3">
      <p className="text-[11px] uppercase tracking-wide text-muted">{label}</p>
      <p className={`mt-1 text-2xl font-semibold tabular-nums ${tone ?? "text-ink"}`}>{value}</p>
      {hint ? <p className="mt-0.5 text-[11px] text-muted">{hint}</p> : null}
    </div>
  );
}

export default function App() {
  const [repoUrl, setRepoUrl] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<string | null>(null);
  const [scan, setScan] = useState<ScanResult | null>(null);
  const [target, setTarget] = useState<string | null>(null);
  const [lastScanAt, setLastScanAt] = useState<string | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [connection, setConnection] = useState<Connection>("checking");
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useState<"dependencies" | "review">("dependencies");

  const [fixPackage, setFixPackage] = useState<string | null>(null);
  const [fixJobId, setFixJobId] = useState<string | null>(null);
  const [fixState, setFixState] = useState<JobState | null>(null);
  const [fixLogs, setFixLogs] = useState<LogEntry[]>([]);
  const [fixResult, setFixResult] = useState<FixResult | null>(null);
  const [fixError, setFixError] = useState<string | null>(null);

  const runRef = useRef(0);

  useEffect(() => {
    let cancelled = false;
    getHealth()
      .then((result) => {
        if (cancelled) return;
        setHealth(result);
        setConnection("online");
      })
      .catch(() => {
        if (!cancelled) setConnection("offline");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const resetFix = useCallback(() => {
    setFixPackage(null);
    setFixJobId(null);
    setFixState(null);
    setFixLogs([]);
    setFixResult(null);
    setFixError(null);
  }, []);

  const runScan = useCallback(
    async (repo: string) => {
      const runId = ++runRef.current;
      setPhase("loading");
      setError(null);
      setScan(null);
      setSelected(null);
      setTarget(parseRepoName(repo));
      resetFix();

      try {
        const { job_id } = await startScan(repo);
        const startedAt = Date.now();

        for (;;) {
          if (runRef.current !== runId) return;
          const job = await getScan(job_id);
          if (runRef.current !== runId) return;

          if (job.state === "succeeded" && job.result) {
            setScan(job.result);
            setPhase("done");
            setLastScanAt(new Date().toLocaleTimeString());
            const first = [...job.result.dependencies].sort((a, b) => b.rot.value - a.rot.value)[0];
            setSelected(first ? first.name : null);
            return;
          }
          if (job.state === "failed") {
            throw new ApiError(job.error ?? "The scan failed before producing a result.", 0);
          }
          if (Date.now() - startedAt > 10 * 60_000) {
            throw new ApiError("The scan exceeded ten minutes and polling was stopped.", 0);
          }
          await new Promise((resolve) => setTimeout(resolve, 1200));
        }
      } catch (err) {
        if (runRef.current !== runId) return;
        setError(err instanceof Error ? err.message : "Unexpected error while scanning.");
        setPhase("error");
      }
    },
    [resetFix],
  );

  // Fix job: poll as the floor, with SSE triggering an immediate refresh.
  useEffect(() => {
    if (!fixJobId) return;
    let cancelled = false;
    let terminal = false;

    const apply = (job: Awaited<ReturnType<typeof getFix>>) => {
      if (cancelled) return;
      setFixLogs(job.logs);
      setFixState(job.state);
      setFixError(job.error);
      setFixResult(job.result);
      if (job.state === "succeeded" || job.state === "failed") {
        terminal = true;
      }
    };

    const refresh = async () => {
      try {
        apply(await getFix(fixJobId));
      } catch (err) {
        if (!cancelled) setFixError(err instanceof Error ? err.message : "Could not read the fix job.");
      }
    };

    // Server-sent events are an optimisation, not a requirement. When the API is
    // unavailable or the environment has no EventSource, polling below is enough
    // to keep the console correct.
    let source: EventSource | null = null;
    if (typeof EventSource !== "undefined") {
      try {
        source = new EventSource(fixStreamUrl(fixJobId));
        source.addEventListener("log", () => void refresh());
        source.addEventListener("state", () => void refresh());
        source.addEventListener("done", () => {
          void refresh();
          source?.close();
        });
        source.onerror = () => {
          source?.close();
          source = null;
        };
      } catch {
        source = null;
      }
    }

    const timer = window.setInterval(() => {
      if (terminal) return;
      void refresh();
    }, 1200);

    void refresh();

    return () => {
      cancelled = true;
      source?.close();
      window.clearInterval(timer);
    };
  }, [fixJobId]);

  const handleFix = useCallback(
    async (report: DependencyReport, type: FixType) => {
      if (!scan) return;
      setFixPackage(report.name);
      setFixState("queued");
      setFixLogs([]);
      setFixResult(null);
      setFixError(null);
      try {
        const { job_id } = await startFix(scan.scan_id, report.name, type);
        setFixJobId(job_id);
      } catch (err) {
        setFixError(err instanceof Error ? err.message : "Could not start the fix.");
        setFixState("failed");
      }
    },
    [scan],
  );

  const sorted = scan ? [...scan.dependencies].sort((a, b) => b.rot.value - a.rot.value) : [];
  const summary = scan?.summary;
  const selectedReport = scan?.dependencies.find((dependency) => dependency.name === selected) ?? null;
  const fixing = fixState === "queued" || fixState === "running";

  return (
    <div className="min-h-full">
      <header className="sticky top-0 z-20 border-b border-line-soft bg-surface/85 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-7xl items-center justify-between gap-4 px-5">
          <div className="flex items-center gap-2.5">
            <span className="grid h-7 w-7 place-items-center rounded-md border border-accent/30 bg-accent/10">
              <Ghost aria-hidden="true" className="h-4 w-4 text-accent" />
            </span>
            <span className="text-sm font-semibold tracking-tight">Ghost Maintainer</span>
            <span className="rounded border border-line px-1.5 py-0.5 font-mono text-[10px] text-muted">
              v{health?.version ?? "0.1.0"}
            </span>
          </div>

          <div className="flex items-center gap-2">
            <Chip label="mode" value={health ? (health.dry_run ? "dry-run" : "live") : "—"} />
            <Chip label="llm" value={health?.llm_configured ? "on" : "off"} />
            <Chip label="sandbox" value={health?.sandbox_mode ?? "auto"} />
            <span className="inline-flex items-center gap-1.5 rounded-md border border-line-soft bg-panel-2/70 px-2 py-1 text-[11px]">
              <span
                aria-hidden="true"
                className={`h-1.5 w-1.5 rounded-full ${
                  connection === "online"
                    ? "bg-ok"
                    : connection === "offline"
                      ? "bg-critical"
                      : "animate-pulse bg-medium"
                }`}
              />
              <span className="text-muted">
                {connection === "online" ? "API online" : connection === "offline" ? "API offline" : "Checking"}
              </span>
            </span>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-5 pb-16">
        <section className="relative overflow-hidden pb-6 pt-10">
          <div aria-hidden="true" className="grid-bg pointer-events-none absolute inset-x-0 top-0 h-56 opacity-60" />
          <div className="relative">
            <p className="mb-2 inline-flex items-center gap-2 rounded-full border border-line-soft bg-panel-2/60 px-2.5 py-1 text-[11px] text-muted">
              <Radar aria-hidden="true" className="h-3 w-3 text-accent" />
              Dependency rot, proven fixes, evidence-backed pull requests
            </p>
            <h1 className="max-w-2xl text-3xl font-semibold tracking-tight sm:text-4xl">Adopt the orphans.</h1>
            <p className="mt-2 max-w-2xl text-sm leading-relaxed text-muted">
              Scan an npm repository for abandoned and vulnerable dependencies, see whether your code actually uses
              them, fix them with deterministic codemods, and prove the fix with tests before any pull request is
              prepared.
            </p>
          </div>
        </section>

        <RepoInput
          value={repoUrl}
          busy={phase === "loading"}
          onChange={setRepoUrl}
          onScan={() => void runScan(repoUrl.trim())}
          onDemo={() => {
            setRepoUrl("");
            void runScan(DEMO_REPO);
          }}
        />

        <div className="mt-4 flex items-center justify-between text-[11px] text-muted">
          <span>
            {target ? (
              <>
                Target <span className="font-mono text-ink/80">{target}</span>
              </>
            ) : (
              "No scan yet"
            )}
          </span>
          <span className="flex items-center gap-3">
            {lastScanAt ? <span>Last scan {lastScanAt}</span> : null}
            {phase === "done" ? (
              <button
                type="button"
                onClick={() => void runScan(repoUrl.trim() || DEMO_REPO)}
                className="inline-flex items-center gap-1 rounded border border-line px-2 py-0.5 hover:border-accent/40 hover:text-ink"
              >
                <RefreshCw aria-hidden="true" className="h-3 w-3" />
                Re-scan
              </button>
            ) : null}
          </span>
        </div>

<div className="mt-6 flex gap-1 border-b border-line-soft" role="tablist" aria-label="Workspace view">
          {(
            [
              ["dependencies", "Dependency fixer"],
              ["review", "PR review"],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={tab === key}
              onClick={() => setTab(key)}
              className={`-mb-px border-b-2 px-3 py-2 text-xs transition-colors ${
                tab === key
                  ? "border-accent text-ink"
                  : "border-transparent text-muted hover:text-ink"
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        <section className="mt-6">
          {tab === "review" ? (
            <ReviewPanel llmConfigured={health?.llm_configured ?? false} />
          ) : (
            <>
          {phase === "idle" ? (
            <div className="grid gap-3 sm:grid-cols-3">
              {[
                {
                  icon: Radar,
                  title: "Scan",
                  body: "Direct dependencies are resolved from the lockfile, then scored on abandonment, not just CVEs.",
                },
                {
                  icon: ShieldCheck,
                  title: "Prove",
                  body: "Source scanning shows which packages are actually imported, with file and line evidence.",
                },
                {
                  icon: Wrench,
                  title: "Fix",
                  body: "Safe bumps and curated migrations run against the test suite in an isolated workspace.",
                },
              ].map(({ icon: Icon, title, body }) => (
                <div key={title} className="panel p-4">
                  <Icon aria-hidden="true" className="h-4 w-4 text-accent" />
                  <p className="mt-2.5 text-sm font-medium">{title}</p>
                  <p className="mt-1 text-xs leading-relaxed text-muted">{body}</p>
                </div>
              ))}
            </div>
          ) : null}

          {phase === "loading" ? (
            <div className="panel flex items-start gap-3 p-5">
              <Loader2 aria-hidden="true" className="mt-0.5 h-4 w-4 animate-spin text-accent" />
              <div>
                <p className="text-sm font-medium">Scanning {target ?? "repository"}…</p>
                <p className="mt-1 text-xs text-muted">
                  Cloning, resolving manifests, then querying npm, OSV, GitHub and deps.dev once per package.
                </p>
              </div>
            </div>
          ) : null}

          {phase === "error" ? (
            <div className="panel border-critical/30 bg-critical/5 p-5">
              <div className="flex items-start gap-3">
                <AlertTriangle aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-critical" />
                <div className="min-w-0">
                  <p className="text-sm font-medium text-critical">Scan failed</p>
                  <p className="mt-1 break-words font-mono text-xs leading-relaxed text-ink/80">{error}</p>
                  <div className="mt-3 flex gap-2">
                    <button
                      type="button"
                      onClick={() => void runScan(repoUrl.trim() || DEMO_REPO)}
                      className="rounded-md border border-line bg-panel-2 px-3 py-1.5 text-xs hover:border-accent/40"
                    >
                      Retry
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setRepoUrl("");
                        void runScan(DEMO_REPO);
                      }}
                      className="rounded-md border border-line px-3 py-1.5 text-xs text-muted hover:text-ink"
                    >
                      Try the demo repo
                    </button>
                  </div>
                </div>
              </div>
            </div>
          ) : null}

          {phase === "done" && scan && summary ? (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-3 lg:grid-cols-6">
                <SummaryCard label="Packages" value={summary.total_dependencies} />
                <SummaryCard label="Rotten" value={summary.rotten} tone="text-critical" />
                <SummaryCard label="Aging" value={summary.aging} tone="text-medium" />
                <SummaryCard label="Healthy" value={summary.healthy} tone="text-ok" />
                <SummaryCard label="Alerts" value={summary.total_alerts} hint="OSV advisories" />
                <SummaryCard
                  label="Observed usage"
                  value={summary.alerts_with_observed_usage}
                  hint="of those alerts"
                />
              </div>

              {scan.errors.length > 0 ? (
                <div className="panel-quiet p-3">
                  <p className="flex items-center gap-2 text-[11px] font-medium text-medium">
                    <Activity aria-hidden="true" className="h-3.5 w-3.5" />
                    Scan notes
                  </p>
                  <ul className="mt-1.5 space-y-0.5 text-[10px] text-muted">
                    {scan.errors.map((message) => (
                      <li key={message} className="break-words font-mono">
                        {message}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_400px]">
                <div className="space-y-4">
                  <Suspense fallback={<GraphSkeleton />}>
                    <DependencyGraph
                      graph={scan.graph}
                      selected={selected ? `pkg:${selected}` : null}
                      onSelect={(nodeId) => setSelected(nodeId || null)}
                    />
                  </Suspense>

                  <div className="panel overflow-hidden">
                    <div className="flex items-center justify-between border-b border-line-soft px-4 py-3">
                      <p className="text-sm font-medium">Dependencies</p>
                      <p className="text-[11px] text-muted">
                        {summary.unresolved_versions > 0
                          ? `${summary.unresolved_versions} unresolved — no lockfile entry`
                          : "All versions resolved from the lockfile"}
                      </p>
                    </div>
                    <div className="overflow-x-auto">
                      <table className="w-full text-left text-xs">
                        <thead className="bg-panel-2/60 text-[10px] uppercase tracking-wide text-muted">
                          <tr>
                            <th scope="col" className="px-4 py-2 font-medium">Package</th>
                            <th scope="col" className="px-4 py-2 font-medium">Installed</th>
                            <th scope="col" className="px-4 py-2 font-medium">Rot</th>
                            <th scope="col" className="px-4 py-2 font-medium">Alerts</th>
                            <th scope="col" className="px-4 py-2 font-medium">Usage</th>
                          </tr>
                        </thead>
                        <tbody>
                          {sorted.map((dep) => (
                            <tr
                              key={dep.name}
                              onClick={() => setSelected(dep.name)}
                              className={`cursor-pointer border-t border-line-soft/70 transition-colors hover:bg-panel-2/40 ${
                                selected === dep.name ? "bg-panel-2/70" : ""
                              }`}
                            >
                              <td className="px-4 py-2.5">
                                <span className="font-mono text-ink">{dep.name}</span>
                                {dep.is_dev ? (
                                  <span className="ml-2 rounded border border-line px-1 py-0.5 text-[9px] text-muted">
                                    dev
                                  </span>
                                ) : null}
                              </td>
                              <td className="px-4 py-2.5 font-mono text-muted">
                                {dep.version ?? <span className="text-medium">{dep.declared_range}</span>}
                              </td>
                              <td className="px-4 py-2.5">
                                <span
                                  className={`inline-flex items-center gap-1.5 rounded border px-1.5 py-0.5 ${RISK_BORDER[dep.rot.label]}`}
                                >
                                  <span className={`font-mono tabular-nums ${RISK_TEXT[dep.rot.label]}`}>
                                    {dep.rot.value}
                                  </span>
                                  <span className="text-[9px] uppercase tracking-wide text-muted">
                                    {dep.rot.label}
                                  </span>
                                </span>
                              </td>
                              <td className="px-4 py-2.5 font-mono text-muted">{dep.vulnerabilities.length}</td>
                              <td className="px-4 py-2.5 text-muted">
                                {dep.reachability.classification === "observed"
                                  ? "observed"
                                  : dep.reachability.classification === "not_observed"
                                    ? "not observed"
                                    : "unknown"}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </div>

                <div className="space-y-4">
                  {selectedReport ? (
                    <>
                      <PackageCard
                        report={selectedReport}
                        fixing={fixing}
                        onFix={(type) => void handleFix(selectedReport, type)}
                      />
                      <ReachabilityPanel reachability={selectedReport.reachability} />
                    </>
                  ) : (
                    <div className="panel p-6 text-center">
                      <p className="text-sm text-muted">Select a dependency to inspect its evidence.</p>
                    </div>
                  )}
                </div>
              </div>

              {fixPackage ? (
                <div className="grid gap-4 xl:grid-cols-2">
                  <FixConsole logs={fixLogs} state={fixState} error={fixError} packageName={fixPackage} />
                  {fixResult ? <PRPreview result={fixResult} /> : (
                    <div className="panel p-4">
                      <p className="text-sm font-medium">Pull-request preview</p>
                      <p className="mt-1.5 text-[11px] text-muted">
                        A preview is produced only after the baseline and post-fix tests both pass.
                      </p>
                    </div>
                  )}
                </div>
              ) : null}
</div>
          ) : null}
            </>
          )}
        </section>

        <footer className="mt-12 border-t border-line-soft pt-4 text-[11px] leading-relaxed text-muted">
          Rot scores and reachability are evidence, not proof. A package with no observed import may still be reached
          transitively, dynamically or through unsupported files.
        </footer>
      </main>
    </div>
  );
}