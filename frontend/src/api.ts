/**
 * Typed client for the Ghost Maintainer API.
 * The base URL is configurable through VITE_API_BASE_URL at build/dev time.
 */

const RAW_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
export const API_BASE = RAW_BASE.replace(/\/+$/, "");

/** Sentinel accepted by POST /api/scan to scan the bundled demo repository. */
export const DEMO_REPO = "demo";

export type RiskLabel = "healthy" | "aging" | "rotten";
export type MetadataStatus = "ok" | "partial" | "unavailable";
export type JobState = "queued" | "running" | "succeeded" | "failed";
export type Severity = "critical" | "high" | "medium" | "low" | "unknown";
export type ReachabilityClass = "observed" | "not_observed" | "unknown";
export type FixType = "version_bump" | "package_replacement" | "none";
export type SandboxStatus =
  | "passed"
  | "failed"
  | "needs_human_review"
  | "baseline_failed"
  | "timeout"
  | "error";

export interface Health {
  status: "ok" | "degraded";
  version: string;
  dry_run: boolean;
  github_configured: boolean;
  llm_configured: boolean;
  sandbox_mode: string;
  time: string;
}

export interface Vulnerability {
  id: string;
  aliases: string[];
  summary: string;
  severity: Severity;
  severity_source: "database_specific" | "cvss_vector" | "unknown";
  cvss_score: number | null;
  affected_ranges: string[];
  fixed_versions: string[];
  references: string[];
  affected_functions: string[];
}

export interface ScoreComponent {
  key: "staleness" | "abandonment" | "vulnerabilities" | "inactivity" | "version_lag";
  label: string;
  points: number;
  max_points: number;
  reason: string;
  known: boolean;
}

export interface RotScore {
  value: number;
  label: RiskLabel;
  components: ScoreComponent[];
  reasons: string[];
  computed_at: string;
  metadata_status: MetadataStatus;
}

export interface UsageMatch {
  file: string;
  line: number;
  column: number;
  snippet: string;
  kind: "import" | "require" | "dynamic_import" | "side_effect";
  /** The raw import specifier, e.g. `lodash/fp`. */
  specifier: string;
  members: string[];
}

export interface Reachability {
  classification: ReachabilityClass;
  explanation: string;
  matches: UsageMatch[];
  used_members: string[];
  scanned_files: number;
  limitation: string;
}

export interface PackageHealth {
  latest_version: string | null;
  published_at: string | null;
  latest_published_at: string | null;
  deprecated: boolean | null;
  deprecated_message: string | null;
  repository_url: string | null;
  repo_archived: boolean | null;
  repo_pushed_at: string | null;
  downloads_last_week: number | null;
  scorecard_score: number | null;
  metadata_status: MetadataStatus;
  data_gaps: string[];
}

export interface FixOption {
  type: FixType;
  summary: string;
  from_version: string | null;
  to_version: string | null;
  replacement: string | null;
  requires_human_review: boolean;
  migration_notes: string[];
}

export interface DependencyReport {
  name: string;
  version: string | null;
  version_source: "lockfile" | "unresolved";
  declared_range: string;
  is_dev: boolean;
  latest_version: string | null;
  repository_url: string | null;
  license: string | null;
  health: PackageHealth;
  rot: RotScore;
  vulnerabilities: Vulnerability[];
  reachability: Reachability;
  fix_options: FixOption[];
}

export interface ScanSummary {
  total_dependencies: number;
  rotten: number;
  aging: number;
  healthy: number;
  total_alerts: number;
  alerts_with_observed_usage: number;
  resolved_versions: number;
  unresolved_versions: number;
}

export interface GraphNode {
  id: string;
  label: string;
  kind: "project" | "dependency";
  rot: number;
  label_kind: RiskLabel;
  is_dev: boolean;
  alerts: number;
  observed: boolean;
}

export interface DepGraph {
  nodes: GraphNode[];
  edges: { source: string; target: string }[];
}

export interface ScanResult {
  scan_id: string;
  repo_url: string;
  repo_name: string;
  repo_slug: string | null;
  ref: string | null;
  base_commit: string | null;
  scanned_at: string;
  dependencies: DependencyReport[];
  summary: ScanSummary;
  graph: DepGraph;
  errors: string[];
}

export interface ScanJobResponse {
  job_id: string;
  state: JobState;
  progress: number;
  error: string | null;
  result: ScanResult | null;
  logs: LogEntry[];
}

export interface FileChange {
  path: string;
  diff: string;
  added_lines: number;
  removed_lines: number;
}

export interface FixPlan {
  type: FixType;
  package: string;
  from_version: string | null;
  to_version: string | null;
  replacement: string | null;
  files_to_change: string[];
  explanation: string;
  migration_notes: string[];
  evidence: string[];
  requires_human_review: boolean;
  changes: FileChange[];
}

export interface SandboxResult {
  status: SandboxStatus;
  mode: "docker" | "local_restricted";
  baseline_passed: boolean | null;
  after_passed: boolean | null;
  baseline_exit_code: number | null;
  after_exit_code: number | null;
  baseline_output_tail: string;
  test_output_tail: string;
  duration_seconds: number;
  diff: string;
  files_changed: string[];
  error: string | null;
  command: string;
}

export interface PRPreview {
  mode: "dry_run" | "real";
  created: boolean;
  title: string;
  body: string;
  branch: string;
  base_branch: string;
  changed_files: string[];
  diff: string;
  url: string | null;
  notes: string[];
}

export interface LogEntry {
  ts: string;
  level: "info" | "warn" | "error" | "success";
  step: string;
  message: string;
}

export interface FixResult {
  fix_id: string;
  scan_id: string;
  package: string;
  state: JobState;
  plan: FixPlan | null;
  sandbox: SandboxResult | null;
  pr: PRPreview | null;
  logs: LogEntry[];
  error: string | null;
}

export interface FixJobResponse {
  job_id: string;
  state: JobState;
  progress: number;
  error: string | null;
  result: FixResult | null;
  logs: LogEntry[];
}

export interface JobAccepted {
  job_id: string;
}

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init: RequestInit = {}, timeoutMs = 30_000): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      ...init,
      signal: controller.signal,
      headers: {
        Accept: "application/json",
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...init.headers,
      },
    });

    if (!response.ok) {
      let detail = `Request failed with status ${response.status}`;
      try {
        const body: unknown = await response.json();
        if (body && typeof body === "object" && "detail" in body) {
          const raw = (body as { detail: unknown }).detail;
          detail = typeof raw === "string" ? raw : JSON.stringify(raw);
        }
      } catch {
        // Non-JSON error bodies keep the status-based message.
      }
      throw new ApiError(detail, response.status);
    }

    return (await response.json()) as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new ApiError("The request timed out. The backend may still be working.", 0);
    }
    if (error instanceof TypeError) {
      throw new ApiError(`Cannot reach the API at ${API_BASE}. Is the backend running?`, 0);
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

export function getHealth(): Promise<Health> {
  return request<Health>("/health", {}, 8_000);
}

export function startScan(repoUrl: string, includeDev = true): Promise<JobAccepted> {
  return request<JobAccepted>("/api/scan", {
    method: "POST",
    body: JSON.stringify({ repo_url: repoUrl, include_dev: includeDev }),
  });
}

export function getScan(jobId: string): Promise<ScanJobResponse> {
  return request<ScanJobResponse>(`/api/scan/${encodeURIComponent(jobId)}`, {}, 20_000);
}

export function startFix(
  scanId: string,
  pkg: string,
  fixType: FixType | null = null,
  applyLlm = false,
): Promise<JobAccepted> {
  return request<JobAccepted>("/api/fix", {
    method: "POST",
    body: JSON.stringify({ scan_id: scanId, package: pkg, fix_type: fixType, apply_llm: applyLlm }),
  });
}

export function getFix(jobId: string): Promise<FixJobResponse> {
  return request<FixJobResponse>(`/api/fix/${encodeURIComponent(jobId)}`, {}, 20_000);
}

/** EventSource URL for the fix log stream; the UI falls back to polling when it fails. */
export function fixStreamUrl(jobId: string): string {
  return `${API_BASE}/api/fix/${encodeURIComponent(jobId)}/stream`;
}

export function isGithubRepoUrl(value: string): boolean {
  return /^https?:\/\/(www\.)?github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\/?$/.test(value.trim());
}

export function parseRepoName(value: string): string {
  if (value.trim() === DEMO_REPO) return "demo-rotten-app";
  const match = value.trim().match(/github\.com\/([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+)/);
  return match ? `${match[1]}/${match[2]}` : value;
}
/* ------------------------------------------------------------------------- */
/* Feature 2: pull-request review                                            */
/* ------------------------------------------------------------------------- */

export interface ReviewFinding {
  agent: "static" | "security" | "architecture" | "style";
  severity: "critical" | "high" | "medium" | "low" | "info";
  path: string;
  line: number;
  message: string;
  suggestion: string;
  source: "rule" | "llm";
}

export interface ReviewReport {
  repo: string;
  pull_number: number | null;
  commit_sha: string | null;
  state: JobState;
  agents_run: string[];
  llm_used: boolean;
  posted: boolean;
  dry_run: boolean;
  summary: string;
  error: string | null;
  notes: string[];
  findings: ReviewFinding[];
}

export interface ReviewJobResponse {
  job_id: string;
  state: JobState;
  progress: number;
  error: string | null;
  report: ReviewReport | null;
  logs: LogEntry[];
}

export function startReview(repo: string, pullNumber: number, useLlm = true): Promise<JobAccepted> {
  return request<JobAccepted>("/api/review", {
    method: "POST",
    body: JSON.stringify({ repo, pull_number: pullNumber, use_llm: useLlm }),
  });
}

export function getReview(jobId: string): Promise<ReviewJobResponse> {
  return request<ReviewJobResponse>(`/api/review/${encodeURIComponent(jobId)}`, {}, 20_000);
}

export function getLearnedPatterns(repo: string): Promise<{ repo: string; patterns: unknown[] }> {
  return request(`/api/learned/${encodeURIComponent(repo)}`);
}

/** Accepts "owner/repo#12" or a GitHub pull-request URL. */
export function parsePullReference(value: string): { repo: string; pull: number } | null {
  const trimmed = value.trim();
  const urlMatch = trimmed.match(/github\.com\/([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+)\/pull\/(\d+)/);
  if (urlMatch) {
    return { repo: `${urlMatch[1]}/${urlMatch[2]}`, pull: Number(urlMatch[3]) };
  }
  const shortMatch = trimmed.match(/^([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+)#(\d+)$/);
  if (shortMatch) {
    return { repo: `${shortMatch[1]}/${shortMatch[2]}`, pull: Number(shortMatch[3]) };
  }
  return null;
}
