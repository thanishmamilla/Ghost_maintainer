import { describe, expect, it } from "vitest";
import {
  ApiError,
  DEMO_REPO,
  getHealth,
  isGithubRepoUrl,
  parsePullReference,
  parseRepoName,
  startScan,
} from "./api";

describe("repository URL validation", () => {
  it.each([
    "https://github.com/owner/repo",
    "https://github.com/owner/repo/",
    "https://github.com/owner/repo.git",
    "https://www.github.com/owner/repo",
    "http://github.com/owner/repo",
    "https://github.com/owner-name/repo_name.js",
  ])("accepts %s", (value) => {
    expect(isGithubRepoUrl(value)).toBe(true);
  });

  it.each([
    "",
    "not-a-url",
    "https://gitlab.com/owner/repo",
    "https://github.com/owner",
    "https://github.com/owner/repo/extra/path",
    "github.com/owner/repo",
  ])("rejects %s", (value) => {
    expect(isGithubRepoUrl(value)).toBe(false);
  });

  it("ignores surrounding whitespace", () => {
    expect(isGithubRepoUrl("  https://github.com/owner/repo  ")).toBe(true);
  });
});

describe("repository naming", () => {
  it("names the demo sentinel", () => {
    expect(parseRepoName(DEMO_REPO)).toBe("demo-rotten-app");
  });

  it("formats a github slug as owner/repo", () => {
    expect(parseRepoName("https://github.com/expressjs/express")).toBe("expressjs/express");
  });

  it("passes through an unrecognised value unchanged", () => {
    expect(parseRepoName("something-else")).toBe("something-else");
  });
});

describe("pull-request reference parsing", () => {
  it("parses the owner/repo#123 shorthand", () => {
    expect(parsePullReference("expressjs/express#7561")).toEqual({
      repo: "expressjs/express",
      pull: 7561,
    });
  });

  it("parses a full pull-request URL", () => {
    expect(parsePullReference("https://github.com/axios/axios/pull/11390")).toEqual({
      repo: "axios/axios",
      pull: 11390,
    });
  });

  it.each(["", "expressjs", "expressjs/express", "expressjs/express#", "#12", "owner/repo#abc"])(
    "rejects %s",
    (value) => {
      expect(parsePullReference(value)).toBeNull();
    },
  );
});

describe("API client", () => {
  it("reports an unreachable backend as a friendly error", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getHealth()).rejects.toThrow(/Cannot reach the API/);
  });

  it("surfaces the backend's error detail", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 422,
      json: async () => ({ detail: "That is not a valid npm package name." }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(startScan("demo")).rejects.toThrow("That is not a valid npm package name.");
  });

  it("keeps the status code on an ApiError", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      json: async () => ({ detail: "Scan job not found." }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(startScan("demo")).rejects.toMatchObject({ status: 404 });
  });

  it("falls back to a status message when the body is not JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => {
        throw new Error("not json");
      },
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(startScan("demo")).rejects.toThrow("status 500");
  });

  it("posts the repository and returns the job id", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 202,
      json: async () => ({ job_id: "abc123" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await startScan("https://github.com/owner/repo");

    expect(result.job_id).toBe("abc123");
    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse(init.body)).toEqual({ repo_url: "https://github.com/owner/repo", include_dev: true });
  });

  it("uses ApiError for every failure mode", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getHealth()).rejects.toBeInstanceOf(ApiError);
  });
});