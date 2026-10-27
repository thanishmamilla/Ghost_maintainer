import { useState } from "react";
import { AlertTriangle, Bot, Loader2, MessageSquareCode, Search, ShieldCheck, Sparkles } from "lucide-react";
import { getReview, parsePullReference, startReview } from "../api";
import type { ReviewFinding, ReviewReport } from "../api";

const SEVERITY_CLASS: Record<ReviewFinding["severity"], string> = {
  critical: "border-critical/40 bg-critical/10 text-critical",
  high: "border-high/40 bg-high/10 text-high",
  medium: "border-medium/40 bg-medium/10 text-medium",
  low: "border-low/40 bg-low/10 text-low",
  info: "border-line bg-panel-2 text-muted",
};

const AGENT_LABEL: Record<string, string> = {
  static: "Static analysis",
  security: "Security",
  architecture: "Architecture",
  style: "Style",
};

export function ReviewPanel({ llmConfigured }: { llmConfigured: boolean }) {
  const [reference, setReference] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<ReviewReport | null>(null);

  const submit = async () => {
    const parsed = parsePullReference(reference);
    if (!parsed) {
      setError('Use the form "owner/repo#123" or paste the pull-request URL.');
      return;
    }

    setBusy(true);
    setError(null);
    setReport(null);

    try {
      const { job_id } = await startReview(parsed.repo, parsed.pull, llmConfigured);
      const startedAt = Date.now();
      for (;;) {
        const job = await getReview(job_id);
        if (job.state === "succeeded" && job.report) {
          setReport(job.report);
          return;
        }
        if (job.state === "failed") {
          setError(job.error ?? "The review failed.");
          return;
        }
        if (Date.now() - startedAt > 5 * 60_000) {
          setError("The review exceeded five minutes and polling was stopped.");
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not run the review.");
    } finally {
      setBusy(false);
    }
  };

  const grouped = report
    ? report.findings.reduce<Record<string, ReviewFinding[]>>((accumulator, finding) => {
        (accumulator[finding.agent] ??= []).push(finding);
        return accumulator;
      }, {})
    : {};

  return (
    <div className="space-y-4">
      <div className="panel p-4">
        <p className="flex items-center gap-2 text-sm font-medium">
          <MessageSquareCode aria-hidden="true" className="h-4 w-4 text-accent" />
          Review a pull request
        </p>
        <p className="mt-1 text-[11px] leading-relaxed text-muted">
          Four agents review the added lines of a pull request. Rule-based findings always run;
          {llmConfigured ? " model-assisted findings are validated against the diff." : " configure an LLM key for model-assisted findings."}
        </p>

        <div className="mt-3 flex flex-col gap-2 sm:flex-row">
          <input
            value={reference}
            onChange={(event) => setReference(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !busy) void submit();
            }}
            placeholder="owner/repo#123"
            aria-label="Pull request reference"
            className="flex-1 rounded-lg border border-line bg-surface px-3 py-2 font-mono text-xs text-ink placeholder:text-muted/60 focus:border-accent/60 focus:outline-none"
          />
          <button
            type="button"
            onClick={() => void submit()}
            disabled={busy || reference.trim().length === 0}
            className="inline-flex h-9 items-center justify-center gap-2 rounded-lg bg-accent px-4 text-xs font-medium text-[#0a0c0e] transition-colors hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? <Loader2 aria-hidden="true" className="h-3.5 w-3.5 animate-spin" /> : <Search aria-hidden="true" className="h-3.5 w-3.5" />}
            {busy ? "Reviewing" : "Review"}
          </button>
        </div>

        {error ? (
          <p className="mt-3 flex items-start gap-2 rounded-lg border border-critical/30 bg-critical/5 px-3 py-2 text-[11px] text-critical">
            <AlertTriangle aria-hidden="true" className="mt-0.5 h-3 w-3 shrink-0" />
            <span className="break-words">{error}</span>
          </p>
        ) : null}
      </div>

      {report ? (
        <div className="space-y-3">
          <div className="panel p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="font-mono text-xs text-ink">
                {report.repo}#{report.pull_number}
              </p>
              <div className="flex items-center gap-2">
                <span
                  className={`rounded border px-1.5 py-0.5 text-[10px] uppercase tracking-wide ${
                    report.posted ? "border-ok/40 bg-ok/10 text-ok" : "border-accent/40 bg-accent/10 text-accent"
                  }`}
                >
                  {report.posted ? "posted" : "dry run"}
                </span>
                <span className="rounded border border-line bg-panel-2 px-1.5 py-0.5 text-[10px] text-muted">
                  {report.llm_used ? "rules + model" : "rules only"}
                </span>
              </div>
            </div>
            <p className="mt-2 text-[11px] leading-relaxed text-ink/85">{report.summary}</p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {report.agents_run.map((agent) => (
                <span
                  key={agent}
                  className="inline-flex items-center gap-1 rounded border border-line-soft px-1.5 py-0.5 text-[10px] text-muted"
                >
                  {agent === "security" ? (
                    <ShieldCheck aria-hidden="true" className="h-2.5 w-2.5" />
                  ) : (
                    <Bot aria-hidden="true" className="h-2.5 w-2.5" />
                  )}
                  {AGENT_LABEL[agent] ?? agent}
                </span>
              ))}
            </div>
            {report.notes.length > 0 ? (
              <ul className="mt-2 space-y-0.5 text-[10px] text-muted">
                {report.notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            ) : null}
          </div>

          {report.findings.length === 0 ? (
            <div className="panel flex items-center gap-2 p-4 text-[11px] text-muted">
              <Sparkles aria-hidden="true" className="h-3.5 w-3.5 text-ok" />
              No issues found in the added lines.
            </div>
          ) : (
            Object.entries(grouped).map(([agent, findings]) => (
              <div key={agent} className="panel overflow-hidden">
                <div className="flex items-center justify-between border-b border-line-soft px-4 py-2.5">
                  <p className="text-xs font-medium">{AGENT_LABEL[agent] ?? agent}</p>
                  <span className="text-[10px] text-muted">{findings.length}</span>
                </div>
                <ul className="divide-y divide-line-soft/60">
                  {findings.map((finding) => (
                    <li key={`${finding.path}:${finding.line}:${finding.message}`} className="px-4 py-2.5">
                      <div className="flex flex-wrap items-center gap-2">
                        <span
                          className={`rounded border px-1.5 py-0.5 text-[9px] uppercase tracking-wide ${SEVERITY_CLASS[finding.severity]}`}
                        >
                          {finding.severity}
                        </span>
                        <span className="font-mono text-[10px] text-muted">
                          {finding.path}:{finding.line}
                        </span>
                        {finding.source === "llm" ? (
                          <span className="rounded border border-accent/30 px-1 py-0.5 text-[9px] text-accent">
                            model
                          </span>
                        ) : null}
                      </div>
                      <p className="mt-1 text-[11px] text-ink/85">{finding.message}</p>
                      {finding.suggestion ? (
                        <p className="mt-0.5 text-[10px] text-muted">{finding.suggestion}</p>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </div>
            ))
          )}
        </div>
      ) : null}
    </div>
  );
}