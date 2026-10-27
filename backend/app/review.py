"""AI pull-request review (Feature 2).

Four agents review a pull request: static analysis, security, architecture and
style. Each agent has a deterministic rule set that always runs, and — when an
LLM is configured — may additionally ask the model for findings. Model output is
validated against a strict schema, restricted to files actually changed in the
pull request, and merged with the rule-based findings.

This means the reviewer produces real, defensible findings with no API key, and
better ones with a key. It never posts a finding it cannot attribute to a changed
line.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable

from .config import get_settings
from .llm import LLMClient
from .models import JobState

logger = logging.getLogger("ghost_maintainer.review")


class Agent(str, Enum):
    STATIC = "static"
    SECURITY = "security"
    ARCHITECTURE = "architecture"
    STYLE = "style"


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


@dataclass
class Finding:
    agent: Agent
    severity: Severity
    path: str
    line: int
    message: str
    suggestion: str = ""
    source: str = "rule"  # "rule" or "llm"

    def key(self) -> tuple[str, int, str]:
        return (self.path, self.line, self.message.lower().strip())


@dataclass
class ChangedFile:
    path: str
    added_lines: list[tuple[int, str]] = field(default_factory=list)
    removed: int = 0
    added: int = 0


@dataclass
class ReviewReport:
    repo: str
    pull_number: int | None
    commit_sha: str | None = None
    state: JobState = JobState.QUEUED
    findings: list[Finding] = field(default_factory=list)
    agents_run: list[str] = field(default_factory=list)
    llm_used: bool = False
    posted: bool = False
    dry_run: bool = True
    summary: str = ""
    error: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "pull_number": self.pull_number,
            "commit_sha": self.commit_sha,
            "state": self.state.value,
            "agents_run": self.agents_run,
            "llm_used": self.llm_used,
            "posted": self.posted,
            "dry_run": self.dry_run,
            "summary": self.summary,
            "error": self.error,
            "notes": self.notes,
            "findings": [
                {
                    "agent": finding.agent.value,
                    "severity": finding.severity.value,
                    "path": finding.path,
                    "line": finding.line,
                    "message": finding.message,
                    "suggestion": finding.suggestion,
                    "source": finding.source,
                }
                for finding in self.findings
            ],
        }


# ---------------------------------------------------------------------------
# diff parsing
# ---------------------------------------------------------------------------

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,\d+)? @@")
_FILE_RE = re.compile(r"^\+\+\+ b/(?P<path>.+)$")


def parse_patch(patch: str) -> list[ChangedFile]:
    """Parse a unified diff into changed files with their added line numbers."""
    files: list[ChangedFile] = []
    current: ChangedFile | None = None
    new_line = 0

    for raw_line in patch.splitlines():
        file_match = _FILE_RE.match(raw_line)
        if file_match:
            current = ChangedFile(path=file_match.group("path"))
            files.append(current)
            continue

        hunk = _HUNK_RE.match(raw_line)
        if hunk:
            new_line = int(hunk.group("start"))
            continue

        if current is None:
            continue

        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            current.added_lines.append((new_line, raw_line[1:]))
            current.added += 1
            new_line += 1
        elif raw_line.startswith("-") and not raw_line.startswith("---"):
            current.removed += 1
        elif raw_line.startswith(" "):
            new_line += 1

    return [f for f in files if f.path and f.path != "/dev/null"]


# ---------------------------------------------------------------------------
# deterministic agents
# ---------------------------------------------------------------------------

# Loose equality, without matching inside `===` / `!==`.
_LOOSE_EQUALITY = re.compile(r"(?<![=!<>])(?:==|!=)(?!=)")
_STRICT_EQUALITY = re.compile(r"(?<![=!<>])(?:===|!==)(?!=)")


# A rule is a named check so conventions can suppress individual rules per file.
@dataclass(frozen=True)
class Rule:
    id: str
    pattern: re.Pattern[str]
    severity: Severity
    message: str
    suggestion: str


_SECURITY_RULES: list[Rule] = [
    Rule("no-eval", re.compile(r"\beval\s*\("), Severity.CRITICAL, "`eval` executes arbitrary code from a string.", "Replace `eval` with an explicit parser or a lookup table."),
    Rule("no-shell-exec", re.compile(r"\bexecSync\s*\(|\bexec\s*\("), Severity.HIGH, "Shell execution with a constructed command can allow command injection.", "Use `execFile`/`spawn` with an argument list and validate input."),
    Rule("no-dangerous-html", re.compile(r"dangerouslySetInnerHTML"), Severity.HIGH, "Raw HTML injection into React can allow XSS.", "Sanitise the HTML or render text instead."),
    Rule("no-inner-html", re.compile(r"\.innerHTML\s*="), Severity.MEDIUM, "Assigning to `innerHTML` can introduce XSS.", "Use `textContent` or a sanitiser."),
    Rule("no-hardcoded-secret", re.compile(r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*['\"][A-Za-z0-9_\-]{12,}['\"]"), Severity.CRITICAL, "A credential-like literal appears to be hardcoded.", "Move the value to an environment variable and rotate it."),
    Rule("no-tls-disable", re.compile(r"rejectUnauthorized\s*:\s*false"), Severity.HIGH, "TLS certificate validation is disabled.", "Remove the override; never disable certificate verification."),
    Rule("no-weak-hash", re.compile(r"(?i)createHash\(\s*['\"](md5|sha1)['\"]"), Severity.MEDIUM, "A weak hash algorithm is used.", "Use SHA-256 or a password-specific KDF."),
    Rule("no-plaintext-http", re.compile(r"\bhttp://(?!localhost|127\.0\.0\.1)"), Severity.LOW, "A plaintext HTTP URL is used.", "Use HTTPS."),
]

_STYLE_RULES: list[Rule] = [
    Rule("no-debug-log", re.compile(r"\bconsole\.log\s*\("), Severity.INFO, "Leftover debug logging.", "Remove it, or use the project's logger."),
    Rule("no-debugger", re.compile(r"\bdebugger\b"), Severity.MEDIUM, "A `debugger` statement was committed.", "Remove it before merging."),
    Rule("prefer-strict-equality", _LOOSE_EQUALITY, Severity.LOW, "Loose equality coerces types.", "Use `===` / `!==`."),
    Rule("prefer-const", re.compile(r"\bvar\s+"), Severity.INFO, "`var` is function-scoped.", "Prefer `const` or `let`."),
    Rule("no-todo", re.compile(r"\bTODO\b|\bFIXME\b"), Severity.INFO, "An unresolved TODO/FIXME was added.", "Link it to an issue or resolve it."),
]

_STATIC_RULES: list[Rule] = [
    Rule("no-empty-catch", re.compile(r"catch\s*\([^)]*\)\s*\{\s*\}"), Severity.MEDIUM, "An empty `catch` block swallows errors.", "Handle the error, or log it with context."),
    Rule("no-return-await", re.compile(r"return\s+await\b"), Severity.LOW, "`return await` inside a `try` adds no value.", "Return the promise directly."),
    Rule("guard-json-parse", re.compile(r"JSON\.parse\([^)]*\)(?!\s*[;,]?\s*(try|catch))"), Severity.INFO, "`JSON.parse` can throw on malformed input.", "Guard it with a try/catch or a schema check."),
]

_ARCHITECTURE_RULES: list[Rule] = [
    Rule("no-deep-relative", re.compile(r"require\(['\"]\.\./\.\./\.\./"), Severity.LOW, "A relative import climbs three or more levels.", "Consider a path alias or a closer module boundary."),
]

_AGENT_RULES: dict[Agent, list[Rule]] = {
    Agent.SECURITY: _SECURITY_RULES,
    Agent.STYLE: _STYLE_RULES,
    Agent.STATIC: _STATIC_RULES,
    Agent.ARCHITECTURE: _ARCHITECTURE_RULES,
}


def detect_conventions(changed: ChangedFile) -> set[str]:
    """Infer a file's own conventions from the lines it adds.

    A style rule that contradicts what the file already does is suppressed: this
    agent checks a change against *the repository's* conventions, not against a
    house style the project never adopted.
    """
    suppressed: set[str] = set()
    lines = [text for _, text in changed.added_lines]
    if not lines:
        return suppressed

    var_lines = sum(1 for text in lines if re.search(r"\bvar\s+[A-Za-z_$]", text))
    declaration_lines = sum(1 for text in lines if re.search(r"\b(const|let|var)\s+[A-Za-z_$]", text))
    if declaration_lines >= 3 and var_lines / declaration_lines > 0.6:
        suppressed.add("prefer-const")

    loose = sum(1 for text in lines if _LOOSE_EQUALITY.search(text))
    strict = sum(1 for text in lines if _STRICT_EQUALITY.search(text))
    if loose + strict >= 4 and loose / (loose + strict) > 0.6:
        suppressed.add("prefer-strict-equality")

    return suppressed


def run_rule_agents(files: Iterable[ChangedFile], agents: Iterable[Agent]) -> list[Finding]:
    """Run the deterministic rule set for the requested agents."""
    findings: list[Finding] = []
    agent_list = list(agents)
    for changed in files:
        suppressed = detect_conventions(changed)
        for line_number, text in changed.added_lines:
            for agent in agent_list:
                for rule in _AGENT_RULES.get(agent, []):
                    if rule.id in suppressed:
                        continue
                    if rule.pattern.search(text):
                        findings.append(
                            Finding(
                                agent=agent,
                                severity=rule.severity,
                                path=changed.path,
                                line=line_number,
                                message=rule.message,
                                suggestion=rule.suggestion,
                                source="rule",
                            )
                        )
    return findings


# ---------------------------------------------------------------------------
# LLM agent
# ---------------------------------------------------------------------------

_LLM_SYSTEM = """You are a senior code reviewer. Review only the added lines of a pull request.
Return JSON of the form:
{"findings": [{"path": "...", "line": 12, "severity": "critical|high|medium|low|info", "message": "...", "suggestion": "..."}]}
Rules:
- Only report an issue in a file and line that appears in the supplied diff.
- Do not invent files or line numbers.
- Be specific and concise. Do not restate the code.
- If you find nothing worth reporting, return {"findings": []}.
- Never suggest executing commands or fetching arbitrary URLs."""


def _diff_prompt(files: list[ChangedFile], limit: int = 1200) -> str:
    chunks: list[str] = []
    budget = limit
    for changed in files:
        if budget <= 0:
            break
        lines = [f"FILE: {changed.path}"]
        for line_number, text in changed.added_lines[:80]:
            if budget <= 0:
                break
            lines.append(f"{line_number}: {text}")
            budget -= 1
        chunks.append("\n".join(lines))
    return "\n\n".join(chunks)


def parse_llm_findings(
    payload: dict | None,
    files: list[ChangedFile],
    agent: Agent,
) -> list[Finding]:
    """Validate model output against the diff, dropping anything unverifiable."""
    if not payload:
        return []

    allowed: dict[str, set[int]] = {
        changed.path: {line for line, _ in changed.added_lines} for changed in files
    }

    findings: list[Finding] = []
    raw_findings = payload.get("findings")
    if not isinstance(raw_findings, list):
        return []

    for entry in raw_findings[:25]:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        line = entry.get("line")
        message = entry.get("message")
        if not isinstance(path, str) or not isinstance(message, str) or not message.strip():
            continue
        if path not in allowed:
            logger.info("Dropping an LLM finding for an unchanged file: %s", path)
            continue
        try:
            line_number = int(line)
        except (TypeError, ValueError):
            continue
        if line_number not in allowed[path]:
            logger.info("Dropping an LLM finding for an unchanged line: %s:%s", path, line_number)
            continue

        raw_severity = str(entry.get("severity", "info")).lower()
        try:
            severity = Severity(raw_severity)
        except ValueError:
            severity = Severity.INFO

        suggestion = entry.get("suggestion")
        findings.append(
            Finding(
                agent=agent,
                severity=severity,
                path=path,
                line=line_number,
                message=message.strip()[:500],
                suggestion=suggestion.strip()[:500] if isinstance(suggestion, str) else "",
                source="llm",
            )
        )
    return findings


async def run_llm_agent(
    files: list[ChangedFile],
    agent: Agent,
    *,
    client: LLMClient | None = None,
) -> list[Finding]:
    """Ask the model for findings, then validate them."""
    if not files:
        return []

    settings = get_settings()
    if not settings.llm_configured:
        return []

    prompt = _diff_prompt(files)

    try:
        if client is not None:
            payload = await client.complete_json(system=_LLM_SYSTEM, user=prompt)
        else:
            async with LLMClient() as llm:
                payload = await llm.complete_json(system=_LLM_SYSTEM, user=prompt)
    except Exception as exc:  # noqa: BLE001 - the review continues without the LLM
        logger.warning("The %s LLM agent failed: %s", agent.value, exc)
        return []

    return parse_llm_findings(payload, files, agent)


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------

_SEVERITY_RANK = {
    Severity.CRITICAL: 0,
    Severity.HIGH: 1,
    Severity.MEDIUM: 2,
    Severity.LOW: 3,
    Severity.INFO: 4,
}


def merge_findings(*groups: Iterable[Finding]) -> list[Finding]:
    """Merge and de-duplicate findings, keeping the most severe of each duplicate."""
    best: dict[tuple[str, int, str], Finding] = {}
    for group in groups:
        for finding in group:
            key = finding.key()
            existing = best.get(key)
            if existing is None or _SEVERITY_RANK[finding.severity] < _SEVERITY_RANK[existing.severity]:
                best[key] = finding
            elif existing.source == "rule" and finding.source == "llm":
                # A model finding that agrees with a rule adds no information.
                continue

    return sorted(
        best.values(),
        key=lambda finding: (_SEVERITY_RANK[finding.severity], finding.path, finding.line),
    )


def summarise(findings: list[Finding], agents: list[Agent]) -> str:
    """One-paragraph summary for the pull-request comment."""
    if not findings:
        return (
            f"Ghost Maintainer reviewed this pull request with {len(agents)} agent(s) and found no issues "
            "worth commenting on."
        )

    counts: dict[Severity, int] = {}
    for finding in findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1

    parts = [
        f"{count} {severity.value}"
        for severity, count in sorted(counts.items(), key=lambda item: _SEVERITY_RANK[item[0]])
    ]
    return (
        f"Ghost Maintainer reviewed this pull request with {len(agents)} agent(s) and found "
        f"{len(findings)} issue(s): {', '.join(parts)}."
    )


def build_review_comment_body(report: ReviewReport) -> str:
    """The summary comment posted alongside the inline findings."""
    lines = [f"### Ghost Maintainer review", "", report.summary, ""]
    if report.findings:
        lines.append("| Severity | Agent | File | Line | Issue |")
        lines.append("| --- | --- | --- | ---: | --- |")
        for finding in report.findings[:30]:
            message = finding.message.replace("|", "\\|")
            lines.append(
                f"| {finding.severity.value} | {finding.agent.value} | `{finding.path}` | {finding.line} | {message} |"
            )
        if len(report.findings) > 30:
            lines.append(f"| … | | | | {len(report.findings) - 30} more finding(s) |")
        lines.append("")
    if report.llm_used:
        lines.append("_Model-assisted findings were validated against the diff before posting._")
    else:
        lines.append("_Findings come from deterministic rules; no model was used for this review._")
    lines.append("")
    lines.append("Opened by Ghost Maintainer")
    return "\n".join(lines)


async def review_pull_request(
    *,
    repo: str,
    patch: str,
    pull_number: int | None = None,
    commit_sha: str | None = None,
    agents: list[Agent] | None = None,
    use_llm: bool = True,
) -> ReviewReport:
    """Run all requested agents over a pull request's diff."""
    settings = get_settings()
    report = ReviewReport(
        repo=repo,
        pull_number=pull_number,
        commit_sha=commit_sha,
        state=JobState.RUNNING,
        dry_run=settings.dry_run,
    )

    agents = agents or [Agent.SECURITY, Agent.STYLE, Agent.STATIC, Agent.ARCHITECTURE]
    files = parse_patch(patch)
    if not files:
        report.state = JobState.SUCCEEDED
        report.summary = "The pull request contains no reviewable text changes."
        report.notes.append("No added lines were found in the diff.")
        return report

    rule_findings = run_rule_agents(files, agents)
    report.agents_run = [agent.value for agent in agents]

    llm_findings: list[Finding] = []
    if use_llm and settings.llm_configured:
        report.llm_used = True
        for agent in agents:
            llm_findings.extend(await run_llm_agent(files, agent))

    report.findings = merge_findings(rule_findings, llm_findings)
    report.summary = summarise(report.findings, agents)
    report.state = JobState.SUCCEEDED
    report.notes.append(f"{len(files)} file(s) reviewed.")
    return report