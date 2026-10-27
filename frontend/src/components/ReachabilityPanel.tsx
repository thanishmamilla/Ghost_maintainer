import { Eye, EyeOff, HelpCircle, Info } from "lucide-react";
import type { Reachability } from "../api";

const BADGE = {
  observed: {
    icon: Eye,
    label: "Observed in source",
    className: "border-critical/40 bg-critical/10 text-critical",
  },
  not_observed: {
    icon: EyeOff,
    label: "Not observed",
    className: "border-medium/40 bg-medium/10 text-medium",
  },
  unknown: {
    icon: HelpCircle,
    label: "Unknown",
    className: "border-line bg-panel-2 text-muted",
  },
} as const;

export function ReachabilityPanel({ reachability }: { reachability: Reachability }) {
  const badge = BADGE[reachability.classification];
  const Icon = badge.icon;

  return (
    <div className="panel p-4">
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium">Reachability</p>
        <span className={`inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-[10px] ${badge.className}`}>
          <Icon aria-hidden="true" className="h-3 w-3" />
          {badge.label}
        </span>
      </div>

      <p className="mt-2 text-[11px] leading-relaxed text-muted">{reachability.explanation}</p>

      {reachability.used_members.length > 0 ? (
        <p className="mt-2 text-[11px] text-muted">
          Members used:{" "}
          <span className="font-mono text-ink/80">{reachability.used_members.join(", ")}</span>
        </p>
      ) : null}

      {reachability.matches.length > 0 ? (
        <div className="mt-3 overflow-hidden rounded-lg border border-line-soft">
          <table className="w-full text-left text-[10px]">
            <thead className="bg-panel-2/60 uppercase tracking-wide text-muted">
              <tr>
                <th scope="col" className="px-2 py-1.5 font-medium">File</th>
                <th scope="col" className="px-2 py-1.5 font-medium">Line</th>
                <th scope="col" className="px-2 py-1.5 font-medium">Reference</th>
              </tr>
            </thead>
            <tbody>
              {reachability.matches.slice(0, 8).map((match) => (
                <tr key={`${match.file}:${match.line}`} className="border-t border-line-soft/60">
                  <td className="px-2 py-1.5 font-mono text-ink/80">{match.file}</td>
                  <td className="px-2 py-1.5 font-mono tabular-nums text-muted">{match.line}</td>
                  <td className="max-w-[220px] truncate px-2 py-1.5 font-mono text-muted" title={match.snippet}>
                    {match.snippet}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      <p className="mt-3 flex items-start gap-1.5 text-[10px] leading-relaxed text-muted">
        <Info aria-hidden="true" className="mt-0.5 h-3 w-3 shrink-0" />
        {reachability.limitation}
      </p>

      <p className="mt-1.5 text-[10px] text-muted">{reachability.scanned_files} source file(s) scanned.</p>
    </div>
  );
}