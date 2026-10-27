import { useEffect, useRef } from "react";
import { CheckCircle2, CircleDashed, Loader2, XCircle } from "lucide-react";
import type { JobState, LogEntry } from "../api";

const LEVEL_CLASS: Record<string, string> = {
  info: "text-muted",
  warn: "text-medium",
  error: "text-critical",
  success: "text-ok",
};

/** The pipeline stages, in the order the backend emits them. */
export const PIPELINE_STEPS = [
  { step: "plan", label: "Planning dependency fix" },
  { step: "sandbox", label: "Preparing isolated workspace" },
  { step: "baseline", label: "Collecting baseline test results" },
  { step: "diff", label: "Generating unified diff" },
  { step: "pr", label: "Preparing pull-request preview" },
] as const;

function stepStatus(step: string, logs: LogEntry[], state: JobState, failed: boolean) {
  const entries = logs.filter((entry) => entry.step === step);
  if (entries.some((entry) => entry.level === "error")) return "failed" as const;
  if (entries.length > 0) return "done" as const;
  if (failed && state === "failed") return "failed" as const;
  if (state === "running") return "pending" as const;
  return "pending" as const;
}

export function FixConsole({
  logs,
  state,
  error,
  packageName,
}: {
  logs: LogEntry[];
  state: JobState | null;
  error: string | null;
  packageName: string | null;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Not every environment provides scrollIntoView (older engines, jsdom), and
    // auto-scrolling is a nicety rather than a requirement.
    bottomRef.current?.scrollIntoView?.({ block: "nearest" });
  }, [logs.length]);

  return (
    <div className="space-y-3">
      <div className="panel overflow-hidden">
        <div className="flex items-center justify-between border-b border-line-soft px-4 py-3">
          <p className="text-sm font-medium">
            Fix pipeline {packageName ? <span className="font-mono text-muted">· {packageName}</span> : null}
          </p>
          <span className="text-[10px] uppercase tracking-wide text-muted">
            {state ?? "idle"}
            {state === "running" ? <Loader2 aria-hidden="true" className="ml-1 inline h-3 w-3 animate-spin" /> : null}
          </span>
        </div>

        <ol className="divide-y divide-line-soft/60">
          {PIPELINE_STEPS.map(({ step, label }) => {
            const status = stepStatus(step, logs, state ?? "queued", Boolean(error));
            return (
              <li key={step} className="flex items-center gap-2.5 px-4 py-2 text-[11px]">
                {status === "done" ? (
                  <CheckCircle2 aria-hidden="true" className="h-3.5 w-3.5 text-ok" />
                ) : status === "failed" ? (
                  <XCircle aria-hidden="true" className="h-3.5 w-3.5 text-critical" />
                ) : (
                  <CircleDashed aria-hidden="true" className="h-3.5 w-3.5 text-line" />
                )}
                <span className={status === "pending" ? "text-muted" : "text-ink/85"}>{label}</span>
              </li>
            );
          })}
        </ol>

        <div className="max-h-64 min-h-[120px] overflow-y-auto border-t border-line-soft bg-[#070a0c] px-4 py-3 font-mono text-[11px] leading-relaxed">
          {logs.length === 0 ? (
            <p className="text-muted">Waiting for the backend to report progress…</p>
          ) : (
            logs.map((entry, index) => (
              <p key={`${entry.ts}-${index}`} className={LEVEL_CLASS[entry.level] ?? "text-muted"}>
                <span className="text-line">[{entry.step || "log"}]</span> {entry.message}
              </p>
            ))
          )}
          {error ? <p className="mt-2 text-critical">{error}</p> : null}
          <div ref={bottomRef} />
        </div>
      </div>
    </div>
  );
}