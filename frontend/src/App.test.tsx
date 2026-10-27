import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import type { ScanResult } from "./api";

// Cytoscape renders to a canvas, which jsdom does not provide. The graph is
// covered separately in DependencyGraph.test.tsx; here it is replaced with a
// lightweight stand-in so the dashboard logic can be exercised.
vi.mock("./components/DependencyGraph", () => ({
  DependencyGraph: ({
    graph,
    onSelect,
  }: {
    graph: { nodes: { id: string; label: string; kind: string }[] };
    selected: string | null;
    onSelect: (nodeId: string) => void;
  }) => (
    <div data-testid="dependency-graph">
      {graph.nodes.map((node) => (
        <button
          key={node.id}
          type="button"
          onClick={() => onSelect(node.id === "__project__" ? "" : node.id.replace(/^pkg:/, ""))}
        >
          {node.label}
        </button>
      ))}
    </div>
  ),
}));

/** A minimal but realistic scan payload, shaped like the backend's response. */
function scanFixture(): ScanResult {
  return {
    scan_id: "scan123",
    repo_url: "demo",
    repo_name: "demo-rotten-app",
    repo_slug: null,
    ref: "main",
    base_commit: "abc",
    scanned_at: "2026-10-26T00:00:00Z",
    summary: {
      total_dependencies: 2,
      rotten: 1,
      aging: 0,
      healthy: 1,
      total_alerts: 4,
      alerts_with_observed_usage: 0,
      resolved_versions: 2,
      unresolved_versions: 0,
    },
    graph: {
      nodes: [
        { id: "__project__", label: "demo-rotten-app", kind: "project", rot: 0, label_kind: "healthy", is_dev: false, alerts: 0, observed: false },
        { id: "pkg:jsonwebtoken", label: "jsonwebtoken", kind: "dependency", rot: 90, label_kind: "rotten", is_dev: false, alerts: 4, observed: false },
        { id: "pkg:semver", label: "semver", kind: "dependency", rot: 0, label_kind: "healthy", is_dev: false, alerts: 0, observed: true },
      ],
      edges: [
        { source: "__project__", target: "pkg:jsonwebtoken" },
        { source: "__project__", target: "pkg:semver" },
      ],
    },
    errors: ["No package-lock.json was found."],
    dependencies: [
      {
        name: "jsonwebtoken",
        version: "3.2.2",
        version_source: "lockfile",
        declared_range: "3.2.2",
        is_dev: false,
        latest_version: "9.0.3",
        repository_url: "https://github.com/auth0/node-jsonwebtoken",
        license: "MIT",
        health: {
          latest_version: "9.0.3",
          published_at: "2015-04-01T00:00:00Z",
          latest_published_at: "2026-07-01T00:00:00Z",
          deprecated: true,
          deprecated_message: "Critical vulnerability fix in v5.0.0.",
          repository_url: "https://github.com/auth0/node-jsonwebtoken",
          repo_archived: false,
          repo_pushed_at: "2026-06-25T00:00:00Z",
          downloads_last_week: 1000,
          scorecard_score: 7.1,
          metadata_status: "partial",
          data_gaps: ["The advisory service could not be reached."],
        },
        rot: {
          value: 90,
          label: "rotten",
          computed_at: "2026-10-26T00:00:00Z",
          metadata_status: "partial",
          reasons: ["The installed release was published 4200 days ago."],
          components: [
            { key: "staleness", label: "Staleness", points: 30, max_points: 30, reason: "Published 4200 days ago.", known: true },
            { key: "abandonment", label: "Abandonment", points: 25, max_points: 25, reason: "Marked deprecated on npm.", known: true },
            { key: "vulnerabilities", label: "Vulnerabilities", points: 25, max_points: 25, reason: "4 advisories; highest critical.", known: true },
            { key: "inactivity", label: "Maintainer inactivity", points: 0, max_points: 10, reason: "Pushed 123 days ago.", known: true },
            { key: "version_lag", label: "Version lag", points: 10, max_points: 10, reason: "At least one major behind.", known: true },
          ],
        },
        vulnerabilities: [
          {
            id: "GHSA-c7hr-j4mj-j2w6",
            aliases: ["CVE-2015-9235"],
            summary: "Verification bypass in jsonwebtoken",
            severity: "critical",
            severity_source: "database_specific",
            cvss_score: null,
            affected_ranges: ["introduced 0, fixed 4.2.2"],
            fixed_versions: ["4.2.2"],
            references: ["https://osv.dev/vulnerability/GHSA-c7hr-j4mj-j2w6"],
            affected_functions: [],
          },
        ],
        reachability: {
          classification: "not_observed",
          explanation:
            "No import or usage of `jsonwebtoken` was found in the 6 source file(s) that were scanned. This does not prove the package is unused.",
          matches: [],
          used_members: [],
          scanned_files: 6,
          limitation:
            "Source scanning is a heuristic. A package with no observed import may still be reached transitively.",
        },
        fix_options: [
          {
            type: "version_bump",
            summary: "Every upstream fix is in a different major version.",
            from_version: "3.2.2",
            to_version: "9.0.0",
            replacement: null,
            requires_human_review: true,
            migration_notes: ["A breaking change."],
          },
        ],
      },
      {
        name: "semver",
        version: "7.8.5",
        version_source: "lockfile",
        declared_range: "7.8.5",
        is_dev: false,
        latest_version: "7.8.5",
        repository_url: "https://github.com/npm/node-semver",
        license: "ISC",
        health: {
          latest_version: "7.8.5",
          published_at: "2026-06-19T00:00:00Z",
          latest_published_at: "2026-06-19T00:00:00Z",
          deprecated: false,
          deprecated_message: null,
          repository_url: "https://github.com/npm/node-semver",
          repo_archived: false,
          repo_pushed_at: "2026-10-21T00:00:00Z",
          downloads_last_week: 900000,
          scorecard_score: 8.2,
          metadata_status: "ok",
          data_gaps: [],
        },
        rot: {
          value: 0,
          label: "healthy",
          computed_at: "2026-10-26T00:00:00Z",
          metadata_status: "ok",
          reasons: [],
          components: [
            { key: "staleness", label: "Staleness", points: 0, max_points: 30, reason: "Recent release.", known: true },
            { key: "abandonment", label: "Abandonment", points: 0, max_points: 25, reason: "Maintained.", known: true },
            { key: "vulnerabilities", label: "Vulnerabilities", points: 0, max_points: 25, reason: "No advisories.", known: true },
            { key: "inactivity", label: "Maintainer inactivity", points: 0, max_points: 10, reason: "Active.", known: true },
            { key: "version_lag", label: "Version lag", points: 0, max_points: 10, reason: "Current.", known: true },
          ],
        },
        vulnerabilities: [],
        reachability: {
          classification: "observed",
          explanation: "`semver` was observed in 1 reference across 1 file using require.",
          matches: [
            { file: "src/versions.js", line: 6, column: 13, snippet: 'const semver = require("semver");', kind: "require", specifier: "semver", members: ["semver"] },
          ],
          used_members: ["semver"],
          scanned_files: 6,
          limitation: "Source scanning is a heuristic.",
        },
        fix_options: [],
      },
    ],
  };
}

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}

/** Route mocked fetch calls by URL. */
function installFetch(handlers: Record<string, () => unknown>) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    for (const [fragment, handler] of Object.entries(handlers)) {
      if (url.includes(fragment)) return handler();
    }
    return jsonResponse({ detail: `unmocked: ${url}` }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const HEALTH = {
  status: "ok",
  version: "0.1.0",
  dry_run: true,
  github_configured: false,
  llm_configured: false,
  sandbox_mode: "auto",
  time: "2026-10-26T00:00:00Z",
};

describe("dashboard shell", () => {
  beforeEach(() => {
    installFetch({ "/health": () => jsonResponse(HEALTH) });
  });

  it("renders the product identity", async () => {
    render(<App />);

    expect(screen.getByText("Ghost Maintainer")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /adopt the orphans/i })).toBeInTheDocument();
  });

  it("shows the API connection state", async () => {
    render(<App />);

    await waitFor(() => expect(screen.getByText("API online")).toBeInTheDocument());
  });

  it("reports an offline backend without crashing", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    render(<App />);

    await waitFor(() => expect(screen.getByText("API offline")).toBeInTheDocument());
  });

  it("shows the three-stage explainer before any scan", () => {
    render(<App />);

    expect(screen.getByText("Scan")).toBeInTheDocument();
    expect(screen.getByText("Prove")).toBeInTheDocument();
    expect(screen.getByText("Fix")).toBeInTheDocument();
    expect(screen.getByText("No scan yet")).toBeInTheDocument();
  });

  it("reflects dry-run and LLM state from the health endpoint", async () => {
    render(<App />);

    await waitFor(() => expect(screen.getByText("dry-run")).toBeInTheDocument());
    expect(screen.getByText("off")).toBeInTheDocument();
  });

  it("validates the repository URL inline", async () => {
    const user = userEvent.setup();
    render(<App />);

    const input = screen.getByLabelText(/github repository url/i);
    await user.type(input, "not-a-repo");
    await user.tab();

    expect(await screen.findByText(/enter a public repository url/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /scan repo/i })).toBeEnabled();
  });

  it("disables the scan button when the field is empty", () => {
    render(<App />);

    expect(screen.getByRole("button", { name: /scan repo/i })).toBeDisabled();
  });
});

describe("scan results", () => {
  it("renders the real summary counts, not hardcoded ones", async () => {
    installFetch({
      "/health": () => jsonResponse(HEALTH),
      "/api/scan/": () => jsonResponse({ job_id: "scan123", state: "succeeded", progress: 100, error: null, logs: [], result: scanFixture() }),
      "/api/scan": () => jsonResponse({ job_id: "scan123" }, 202),
    });
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: /demo repo/i }));

    await waitFor(() => expect(screen.getByText("Rotten")).toBeInTheDocument());

    const rottenCard = screen.getByText("Rotten").closest("div")!;
    expect(within(rottenCard).getByText("1")).toBeInTheDocument();
    const healthyCard = screen.getByText("Healthy").closest("div")!;
    expect(within(healthyCard).getByText("1")).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument();
  });

  it("lists the dependencies with their scores and usage", async () => {
    installFetch({
      "/health": () => jsonResponse(HEALTH),
      "/api/scan/": () => jsonResponse({ job_id: "scan123", state: "succeeded", progress: 100, error: null, logs: [], result: scanFixture() }),
      "/api/scan": () => jsonResponse({ job_id: "scan123" }, 202),
    });
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: /demo repo/i }));

    const table = await screen.findByRole("table");
    await waitFor(() => expect(within(table).getByText("jsonwebtoken")).toBeInTheDocument());
    expect(within(table).getByText("semver")).toBeInTheDocument();
    expect(within(table).getByText("not observed")).toBeInTheDocument();
  });

  it("shows the reachability limitation, not just a verdict", async () => {
    installFetch({
      "/health": () => jsonResponse(HEALTH),
      "/api/scan/": () => jsonResponse({ job_id: "scan123", state: "succeeded", progress: 100, error: null, logs: [], result: scanFixture() }),
      "/api/scan": () => jsonResponse({ job_id: "scan123" }, 202),
    });
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: /demo repo/i }));

    await waitFor(() => expect(screen.getByText("Reachability")).toBeInTheDocument());
    expect(screen.getAllByText(/may still be reached transitively/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/Source scanning is a heuristic/i)).toBeInTheDocument();
  });

  it("surfaces scan warnings", async () => {
    installFetch({
      "/health": () => jsonResponse(HEALTH),
      "/api/scan/": () => jsonResponse({ job_id: "scan123", state: "succeeded", progress: 100, error: null, logs: [], result: scanFixture() }),
      "/api/scan": () => jsonResponse({ job_id: "scan123" }, 202),
    });
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: /demo repo/i }));

    await waitFor(() => expect(screen.getByText(/No package-lock\.json was found/i)).toBeInTheDocument());
  });

  it("marks a fix that needs human review", async () => {
    installFetch({
      "/health": () => jsonResponse(HEALTH),
      "/api/scan/": () => jsonResponse({ job_id: "scan123", state: "succeeded", progress: 100, error: null, logs: [], result: scanFixture() }),
      "/api/scan": () => jsonResponse({ job_id: "scan123" }, 202),
    });
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: /demo repo/i }));
    const table = await screen.findByRole("table");
    await waitFor(() => expect(within(table).getByText("jsonwebtoken")).toBeInTheDocument());

    expect(screen.getAllByText("review").length).toBeGreaterThan(0);
    expect(screen.getByText(/different major version/i)).toBeInTheDocument();
  });

  it("shows a scan failure as an error state with a retry", async () => {
    installFetch({
      "/health": () => jsonResponse(HEALTH),
      "/api/scan/": () => jsonResponse({ job_id: "scan123", state: "failed", progress: 0, error: "Could not clone the repository.", logs: [], result: null }),
      "/api/scan": () => jsonResponse({ job_id: "scan123" }, 202),
    });
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: /demo repo/i }));

    await waitFor(() => expect(screen.getByText("Scan failed")).toBeInTheDocument());
    expect(screen.getByText("Could not clone the repository.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });

  it("reports an API rejection on scan start", async () => {
    installFetch({
      "/health": () => jsonResponse(HEALTH),
      "/api/scan": () => jsonResponse({ detail: "That is not a GitHub repository URL or an existing local directory." }, 422),
    });
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText(/github repository url/i), "https://github.com/owner/repo");
    await user.click(screen.getByRole("button", { name: /scan repo/i }));

    await waitFor(() => expect(screen.getByText("Scan failed")).toBeInTheDocument());
    expect(screen.getByText(/not a GitHub repository URL/i)).toBeInTheDocument();
  });
});

describe("graph selection", () => {
  it("selects a package from the graph and shows its evidence", async () => {
    installFetch({
      "/health": () => jsonResponse(HEALTH),
      "/api/scan/": () => jsonResponse({ job_id: "scan123", state: "succeeded", progress: 100, error: null, logs: [], result: scanFixture() }),
      "/api/scan": () => jsonResponse({ job_id: "scan123" }, 202),
    });
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: /demo repo/i }));
    const graph = await screen.findByTestId("dependency-graph");

    // jsonwebtoken is the rotten node; selecting it must load its detail panel.
    await user.click(within(graph).getByRole("button", { name: "jsonwebtoken" }));

    await waitFor(() => expect(screen.getByText("Score breakdown")).toBeInTheDocument());
    expect(screen.getByText(/Critical vulnerability fix in v5\.0\.0/)).toBeInTheDocument();
  });
});

describe("fix pipeline", () => {
  const fixResult = {
    fix_id: "fix1",
    scan_id: "scan123",
    package: "jsonwebtoken",
    state: "succeeded",
    error: null,
    logs: [
      { ts: "2026-10-26T00:00:01Z", level: "info", step: "plan", message: "Planning a fix for jsonwebtoken" },
      { ts: "2026-10-26T00:00:02Z", level: "info", step: "baseline", message: "Collecting baseline test results" },
    ],
    plan: {
      type: "version_bump",
      package: "jsonwebtoken",
      from_version: "3.2.2",
      to_version: "9.0.0",
      replacement: null,
      files_to_change: ["package.json"],
      explanation: "Upgrade jsonwebtoken.",
      migration_notes: [],
      evidence: [],
      requires_human_review: true,
      changes: [],
    },
    sandbox: {
      status: "needs_human_review",
      mode: "local_restricted",
      baseline_passed: true,
      after_passed: false,
      baseline_exit_code: 0,
      after_exit_code: 1,
      baseline_output_tail: "Tests: 21 passed",
      test_output_tail: "Tests: 3 failed",
      duration_seconds: 42.5,
      diff: "",
      files_changed: ["package.json"],
      error: "The test suite fails after the fix.",
      command: "npm test",
    },
    pr: null,
  };

  it("streams real log lines into the console", async () => {
    installFetch({
      "/health": () => jsonResponse(HEALTH),
      "/api/scan/": () => jsonResponse({ job_id: "scan123", state: "succeeded", progress: 100, error: null, logs: [], result: scanFixture() }),
      "/api/scan": () => jsonResponse({ job_id: "scan123" }, 202),
      "/api/fix/": () => jsonResponse({ job_id: "fix1", state: "succeeded", progress: 100, error: null, result: fixResult, logs: fixResult.logs }),
      "/api/fix": () => jsonResponse({ job_id: "fix1" }, 202),
    });
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: /demo repo/i }));
    const table = await screen.findByRole("table");
    await waitFor(() => expect(within(table).getByText("jsonwebtoken")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: /adopt fix/i }));

    await waitFor(() => expect(screen.getByText(/Planning a fix for jsonwebtoken/)).toBeInTheDocument());
    // The step appears in both the pipeline checklist and the log stream.
    expect(screen.getAllByText(/Collecting baseline test results/).length).toBeGreaterThan(0);
  });

  it("never shows a failed test run as a success", async () => {
    installFetch({
      "/health": () => jsonResponse(HEALTH),
      "/api/scan/": () => jsonResponse({ job_id: "scan123", state: "succeeded", progress: 100, error: null, logs: [], result: scanFixture() }),
      "/api/scan": () => jsonResponse({ job_id: "scan123" }, 202),
      "/api/fix/": () => jsonResponse({ job_id: "fix1", state: "succeeded", progress: 100, error: null, result: fixResult, logs: fixResult.logs }),
      "/api/fix": () => jsonResponse({ job_id: "fix1" }, 202),
    });
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: /demo repo/i }));
    const table = await screen.findByRole("table");
    await waitFor(() => expect(within(table).getByText("jsonwebtoken")).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: /adopt fix/i }));

    await waitFor(() => expect(screen.getByText(/needs human review/i)).toBeInTheDocument());
    expect(screen.getByText(/baseline: passed/i)).toBeInTheDocument();
    expect(screen.getByText(/post-fix: failed/i)).toBeInTheDocument();
    // The failing output must be visible, not hidden behind a success state.
    expect(screen.getByText(/Tests: 3 failed/)).toBeInTheDocument();
  });
});

describe("review tab", () => {
  it("switches to the pull-request reviewer", async () => {
    installFetch({ "/health": () => jsonResponse(HEALTH) });
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("tab", { name: /pr review/i }));

    expect(screen.getByText("Review a pull request")).toBeInTheDocument();
    expect(screen.getByLabelText(/pull request reference/i)).toBeInTheDocument();
  });

  it("rejects a malformed reference without calling the API", async () => {
    const fetchMock = installFetch({ "/health": () => jsonResponse(HEALTH) });
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("tab", { name: /pr review/i }));
    await user.type(screen.getByLabelText(/pull request reference/i), "nonsense");
    await user.click(screen.getByRole("button", { name: /^review$/i }));

    expect(await screen.findByText(/owner\/repo#123/i)).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/api/review"))).toBe(false);
  });

  it("renders findings grouped by agent", async () => {
    installFetch({
      "/health": () => jsonResponse(HEALTH),
      "/api/review/": () =>
        jsonResponse({
          job_id: "rev1",
          state: "succeeded",
          progress: 100,
          error: null,
          logs: [],
          report: {
            repo: "expressjs/express",
            pull_number: 7561,
            commit_sha: "abc",
            state: "succeeded",
            agents_run: ["security", "style", "static", "architecture"],
            llm_used: false,
            posted: false,
            dry_run: true,
            summary: "Found 1 issue: 1 critical.",
            error: null,
            notes: ["1 file(s) reviewed."],
            findings: [
              {
                agent: "security",
                severity: "critical",
                path: "src/app.js",
                line: 3,
                message: "`eval` executes arbitrary code from a string.",
                suggestion: "Replace `eval`.",
                source: "rule",
              },
            ],
          },
        }),
      "/api/review": () => jsonResponse({ job_id: "rev1" }, 202),
    });
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("tab", { name: /pr review/i }));
    await user.type(screen.getByLabelText(/pull request reference/i), "expressjs/express#7561");
    await user.click(screen.getByRole("button", { name: /^review$/i }));

    await waitFor(() => expect(screen.getByText(/eval.*executes arbitrary code/i)).toBeInTheDocument());
    expect(screen.getAllByText("Security").length).toBeGreaterThan(0);
    expect(screen.getByText("src/app.js:3")).toBeInTheDocument();
    expect(screen.getByText("dry run")).toBeInTheDocument();
    expect(screen.getByText("rules only")).toBeInTheDocument();
  });
});