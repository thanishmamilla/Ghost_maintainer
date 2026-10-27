import { useState } from "react";
import { Github, Loader2, ScanLine, TestTube2 } from "lucide-react";
import { isGithubRepoUrl } from "../api";

interface RepoInputProps {
  value: string;
  busy: boolean;
  disabled?: boolean;
  onChange: (value: string) => void;
  onScan: () => void;
  onDemo: () => void;
}

export function RepoInput({ value, busy, disabled, onChange, onScan, onDemo }: RepoInputProps) {
  const [touched, setTouched] = useState(false);
  const trimmed = value.trim();
  const invalid = touched && trimmed.length > 0 && !isGithubRepoUrl(trimmed);

  return (
    <div className="panel p-3 sm:p-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="relative flex-1">
          <Github
            aria-hidden="true"
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted"
          />
          <label htmlFor="repo-url" className="sr-only">
            GitHub repository URL
          </label>
          <input
            id="repo-url"
            type="url"
            inputMode="url"
            spellCheck={false}
            autoComplete="off"
            value={value}
            disabled={disabled || busy}
            onChange={(event) => onChange(event.target.value)}
            onBlur={() => setTouched(true)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !busy && trimmed.length > 0) {
                event.preventDefault();
                onScan();
              }
            }}
            placeholder="https://github.com/owner/repo"
            aria-invalid={invalid}
            aria-describedby={invalid ? "repo-url-error" : "repo-url-hint"}
            className="w-full rounded-lg border border-line bg-surface py-2.5 pl-9 pr-3 font-mono text-sm text-ink placeholder:text-muted/60 focus:border-accent/60 focus:outline-none disabled:opacity-60"
          />
        </div>

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onScan}
            disabled={busy || disabled || trimmed.length === 0}
            className="inline-flex h-10 items-center gap-2 rounded-lg bg-accent px-4 text-sm font-medium text-[#0a0c0e] transition-colors hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? (
              <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
            ) : (
              <ScanLine aria-hidden="true" className="h-4 w-4" />
            )}
            {busy ? "Scanning" : "Scan repo"}
          </button>

          <button
            type="button"
            onClick={onDemo}
            disabled={busy || disabled}
            className="inline-flex h-10 items-center gap-2 rounded-lg border border-line bg-panel-2 px-3.5 text-sm text-ink transition-colors hover:border-accent/40 hover:bg-panel disabled:cursor-not-allowed disabled:opacity-50"
          >
            <TestTube2 aria-hidden="true" className="h-4 w-4 text-muted" />
            Demo repo
          </button>
        </div>
      </div>

      <p
        id={invalid ? "repo-url-error" : "repo-url-hint"}
        className={`mt-2 pl-1 text-xs ${invalid ? "text-critical" : "text-muted"}`}
      >
        {invalid
          ? "Enter a public repository URL in the form https://github.com/owner/repo, or use the demo repo."
          : "Public npm repositories only. The demo repo runs offline-safe with pinned test fixtures."}
      </p>
    </div>
  );
}