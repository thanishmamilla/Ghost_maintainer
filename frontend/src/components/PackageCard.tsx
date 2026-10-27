import { AlertTriangle, ArrowRight, Info, ExternalLink } from "lucide-react";
import type { DependencyReport, RiskLabel, Severity } from "../api";

const RISK_TEXT: Record<RiskLabel, string> = {
  rotten: "text-critical",
  aging: "text-medium",
  healthy: "text-ok",
};

const RISK_BG: Record<RiskLabel, string> = {
  rotten: "bg-critical",
  aging: "bg-medium",
  healthy: "bg-ok",
};

const SEVERITY_TEXT: Record<Severity, string> = {
  critical: "text-critical",
  high: "text-high",
  medium: "text-medium",
  low: "text-low",
  unknown: "text-muted",
};

function RotGauge({ value, label }: { value: number; label: RiskLabel }) {
  const radius = 34;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference * (1 - value / 100);
  const stroke = label === "rotten" ? "#ef5350" : label === "aging" ? "#f0b93c" : "#35d39a";

  return (
    <div className="relative h-24 w-24 shrink-0">
      <svg viewBox="0 0 80 80" className="h-24 w-24 -rotate-90">
        <circle cx="40" cy="40" r={radius} fill="none" stroke="#232b33" strokeWidth="7" />
        <circle
          cx="40"
          cy="40"
          r={radius}
          fill="none"
          stroke={stroke}
          strokeWidth="7"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className={`text-xl font-semibold tabular-nums ${RISK_TEXT[label]}`}>{value}</span>
        <span className="text-[9px] uppercase tracking-wide text-muted">{label}</span>
      </div>
    </div>
  );
}

export function PackageCard({
  report,
  onFix,
  fixing,
}: {
  report: DependencyReport;
  onFix: (type: "version_bump" | "package_replacement") => void;
  fixing: boolean;
}) {
  return (
    <div className="panel p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="truncate font-mono text-base font-medium">{report.name}</h3>
            {report.is_dev ? (
              <span className="rounded border border-line px-1 py-0.5 text-[10px] text-muted">dev</span>
            ) : null}
          </div>
          <p className="mt-1 font-mono text-xs text-muted">
            installed {report.version ?? `${report.declared_range} (unresolved)`}
            <span className="mx-1.5 text-line">|</span>
            latest {report.latest_version ?? "unknown"}
          </p>
          {report.health.deprecated ? (
            <p className="mt-2 flex items-start gap-1.5 text-[11px] text-high">
              <AlertTriangle aria-hidden="true" className="mt-0.5 h-3 w-3 shrink-0" />
              <span>{report.health.deprecated_message ?? "Deprecated on npm."}</span>
            </p>
          ) : null}
        </div>
        <RotGauge value={report.rot.value} label={report.rot.label} />
      </div>

      <div className="mt-4 space-y-2">
        <p className="text-[11px] uppercase tracking-wide text-muted">Score breakdown</p>
        {report.rot.components.map((component) => (
          <div key={component.key}>
            <div className="flex items-baseline justify-between text-[11px]">
              <span className={component.known ? "text-ink/80" : "text-muted"}>
                {component.label}
                {component.known ? "" : " (unknown)"}
              </span>
              <span className="font-mono tabular-nums text-muted">
                {component.points}/{component.max_points}
              </span>
            </div>
            <div className="mt-1 h-1 overflow-hidden rounded-full bg-line-soft">
              <div
                className={`h-full ${component.points > 0 ? RISK_BG[report.rot.label] : "bg-line"}`}
                style={{ width: `${(component.points / component.max_points) * 100}%` }}
              />
            </div>
            <p className="mt-1 text-[10px] leading-relaxed text-muted">{component.reason}</p>
          </div>
        ))}
      </div>

      {report.vulnerabilities.length > 0 ? (
        <div className="mt-4">
          <p className="text-[11px] uppercase tracking-wide text-muted">
            Advisories ({report.vulnerabilities.length})
          </p>
          <ul className="mt-2 space-y-1.5">
            {report.vulnerabilities.map((vulnerability) => (
              <li key={vulnerability.id} className="text-[11px]">
                <div className="flex items-start justify-between gap-2">
                  <a
                    href={vulnerability.references[0] ?? `https://osv.dev/vulnerability/${vulnerability.id}`}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 font-mono text-ink/90 hover:text-accent"
                  >
                    {vulnerability.id}
                    <ExternalLink aria-hidden="true" className="h-2.5 w-2.5" />
                  </a>
                  <span className={`text-[10px] uppercase ${SEVERITY_TEXT[vulnerability.severity]}`}>
                    {vulnerability.severity}
                  </span>
                </div>
                <p className="mt-0.5 leading-relaxed text-muted">{vulnerability.summary}</p>
                {vulnerability.fixed_versions.length > 0 ? (
                  <p className="mt-0.5 font-mono text-[10px] text-muted">
                    fixed in {vulnerability.fixed_versions.join(", ")}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {report.health.data_gaps.length > 0 ? (
        <div className="mt-4 rounded-lg border border-line-soft bg-panel-2/50 p-2.5">
          <p className="flex items-center gap-1.5 text-[11px] text-medium">
            <Info aria-hidden="true" className="h-3 w-3" />
            Missing or stale metadata
          </p>
          <ul className="mt-1 space-y-0.5 text-[10px] leading-relaxed text-muted">
            {report.health.data_gaps.map((gap) => (
              <li key={gap}>{gap}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="mt-4 border-t border-line-soft pt-3">
        <p className="text-[11px] uppercase tracking-wide text-muted">Available fixes</p>
        {report.fix_options.length === 0 ? (
          <p className="mt-1.5 text-[11px] text-muted">No automatic fix is available for this package.</p>
        ) : (
          <div className="mt-2 space-y-2">
            {report.fix_options.map((option) => (
              <div key={`${option.type}-${option.to_version ?? option.replacement}`} className="rounded-lg border border-line-soft p-2.5">
                <div className="flex items-start justify-between gap-2">
                  <p className="text-[11px] leading-relaxed text-ink/85">{option.summary}</p>
                  {option.requires_human_review ? (
                    <span className="shrink-0 rounded border border-medium/40 bg-medium/10 px-1 py-0.5 text-[9px] uppercase text-medium">
                      review
                    </span>
                  ) : null}
                </div>
                <button
                  type="button"
                  disabled={fixing}
                  onClick={() => onFix(option.type as "version_bump" | "package_replacement")}
                  className="mt-2 inline-flex h-8 items-center gap-1.5 rounded-md bg-accent px-3 text-xs font-medium text-[#0a0c0e] transition-colors hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  Adopt fix
                  <ArrowRight aria-hidden="true" className="h-3 w-3" />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}