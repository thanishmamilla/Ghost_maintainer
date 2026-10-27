import { ExternalLink, FileCode2, ShieldQuestion } from "lucide-react";
import type { FixResult } from "../api";

function StatusPill({ ok, label }: { ok: boolean | null; label: string }) {
  const tone =
    ok === true
      ? "border-ok/40 bg-ok/10 text-ok"
      : ok === false
        ? "border-critical/40 bg-critical/10 text-critical"
        : "border-line bg-panel-2 text-muted";
  return (
    <span className={`rounded border px-1.5 py-0.5 text-[10px] ${tone}`}>
      {label}: {ok === true ? "passed" : ok === false ? "failed" : "not run"}
    </span>
  );
}

export function PRPreview({ result }: { result: FixResult }) {
  const { pr, sandbox, plan } = result;

  if (!pr) {
    return (
      <div className="panel p-4">
        <p className="text-sm font-medium">Pull-request preview</p>
        <p className="mt-1.5 text-[11px] text-muted">
          {result.error ??
            "No preview was produced. The fix must pass the sandbox tests before a preview is prepared."}
        </p>

        {sandbox ? (
          <div className="mt-3 space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <StatusPill ok={sandbox.baseline_passed} label="baseline" />
              <StatusPill ok={sandbox.after_passed} label="post-fix" />
              <span className="rounded border border-line bg-panel-2 px-1.5 py-0.5 text-[10px] text-muted">
                sandbox: {sandbox.mode}
              </span>
              <span className="text-[10px] text-muted">{sandbox.duration_seconds.toFixed(1)}s</span>
            </div>

            {sandbox.status === "needs_human_review" ? (
              <p className="flex items-center gap-1.5 text-[11px] text-medium">
                <ShieldQuestion aria-hidden="true" className="h-3 w-3" />
                Needs human review — the fix is not proposed as a pull request.
              </p>
            ) : null}

            {sandbox.test_output_tail ? (
              <div>
                <p className="text-[10px] uppercase tracking-wide text-muted">Test output</p>
                <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded-lg bg-[#070a0c] px-3 py-2 font-mono text-[10px] text-muted">
                  {sandbox.test_output_tail.slice(-1200)}
                </pre>
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    );
  }

  return (
    <div className="panel overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line-soft px-4 py-3">
        <p className="text-sm font-medium">Pull-request preview</p>
        <div className="flex items-center gap-2">
          <span
            className={`rounded border px-1.5 py-0.5 text-[10px] uppercase tracking-wide ${
              pr.created
                ? "border-ok/40 bg-ok/10 text-ok"
                : "border-accent/40 bg-accent/10 text-accent"
            }`}
          >
            {pr.created ? "opened" : "dry run"}
          </span>
          {pr.url ? (
            <a
              href={pr.url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 rounded border border-line px-1.5 py-0.5 text-[10px] text-ink hover:border-accent/40"
            >
              Open PR
              <ExternalLink aria-hidden="true" className="h-2.5 w-2.5" />
            </a>
          ) : null}
        </div>
      </div>

      <div className="border-b border-line-soft px-4 py-3">
        <p className="font-mono text-xs text-ink">{pr.title}</p>
        <p className="mt-1 font-mono text-[10px] text-muted">
          {pr.branch} → {pr.base_branch}
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2 border-b border-line-soft px-4 py-2.5">
        <StatusPill ok={sandbox?.baseline_passed ?? null} label="baseline" />
        <StatusPill ok={sandbox?.after_passed ?? null} label="post-fix" />
        <span className="rounded border border-line bg-panel-2 px-1.5 py-0.5 text-[10px] text-muted">
          sandbox: {sandbox?.mode ?? "—"}
        </span>
        {sandbox ? (
          <span className="text-[10px] text-muted">{sandbox.duration_seconds.toFixed(1)}s</span>
        ) : null}
        {plan ? (
          <span className="text-[10px] text-muted">
            {plan.files_to_change.length} file(s) changed
          </span>
        ) : null}
      </div>

      {pr.notes.length > 0 ? (
        <ul className="space-y-1 border-b border-line-soft px-4 py-2.5 text-[10px] text-muted">
          {pr.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      ) : null}

      {sandbox?.diff ? (
        <div>
          <div className="flex items-center gap-1.5 border-b border-line-soft px-4 py-2 text-[10px] uppercase tracking-wide text-muted">
            <FileCode2 aria-hidden="true" className="h-3 w-3" />
            Diff
          </div>
          <pre className="max-h-[320px] overflow-auto bg-[#070a0c] px-4 py-3 font-mono text-[10px] leading-relaxed">
            {sandbox.diff.split("\n").map((line, index) => {
              const tone = line.startsWith("+")
                ? "text-ok"
                : line.startsWith("-")
                  ? "text-critical"
                  : line.startsWith("@@")
                    ? "text-accent"
                    : "text-muted";
              return (
                <span key={index} className={`block ${tone}`}>
                  {line || " "}
                </span>
              );
            })}
          </pre>
        </div>
      ) : null}

      <details className="border-t border-line-soft">
        <summary className="cursor-pointer px-4 py-2.5 text-[11px] text-muted hover:text-ink">
          Pull-request body (Markdown)
        </summary>
        <pre className="max-h-72 overflow-auto whitespace-pre-wrap px-4 pb-4 font-mono text-[10px] leading-relaxed text-muted">
          {pr.body}
        </pre>
      </details>
    </div>
  );
}