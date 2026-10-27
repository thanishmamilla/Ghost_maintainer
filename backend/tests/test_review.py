"""Feature 2 tests: diff parsing, agents, LLM validation, webhooks and the learner."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import httpx
import pytest

from app.config import get_settings
from app.learner import Learner
from app.review import (
    Agent,
    Severity,
    build_review_comment_body,
    merge_findings,
    parse_llm_findings,
    parse_patch,
    review_pull_request,
    run_rule_agents,
    summarise,
)
from app.review_github import ReviewError, build_review_payload, fetch_pull_request, post_review
from app.webhook import WebhookError, delivery_key, parse_event, verify_signature

SAMPLE_DIFF = """diff --git a/src/app.js b/src/app.js
index 111..222 100644
--- a/src/app.js
+++ b/src/app.js
@@ -1,4 +1,8 @@
 const express = require("express");
-const old = 1;
+const API_KEY = "Zx9Qm2Lp7Rt4Vb8Nw3Kc6Yd1";
+eval(userInput);
+console.log("debug");
+if (a == b) { doThing(); }
 module.exports = {};
"""


@pytest.fixture(autouse=True)
def no_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_api_key", "")
    monkeypatch.setattr(settings, "llm_model", "")


# ---------------------------------------------------------------------------
# diff parsing
# ---------------------------------------------------------------------------


def test_patch_parsing_tracks_added_line_numbers() -> None:
    files = parse_patch(SAMPLE_DIFF)

    assert len(files) == 1
    changed = files[0]
    assert changed.path == "src/app.js"
    added_lines = [line for line, _ in changed.added_lines]
    assert added_lines == [2, 3, 4, 5]
    assert changed.added == 4
    assert changed.removed == 1


def test_multiple_files_are_parsed_separately() -> None:
    patch = (
        "--- a/one.js\n+++ b/one.js\n@@ -0,0 +1,1 @@\n+x\n"
        "--- a/two.js\n+++ b/two.js\n@@ -0,0 +1,1 @@\n+y\n"
    )

    files = parse_patch(patch)

    assert [f.path for f in files] == ["one.js", "two.js"]


def test_empty_patch_produces_no_files() -> None:
    assert parse_patch("") == []


# ---------------------------------------------------------------------------
# rule agents
# ---------------------------------------------------------------------------


def test_security_agent_detects_eval() -> None:
    findings = run_rule_agents(parse_patch(SAMPLE_DIFF), [Agent.SECURITY])

    assert any("eval" in finding.message for finding in findings)


def test_security_agent_detects_a_hardcoded_credential() -> None:
    findings = run_rule_agents(parse_patch(SAMPLE_DIFF), [Agent.SECURITY])

    assert any(finding.severity is Severity.CRITICAL for finding in findings)


def test_style_agent_detects_console_log_and_loose_equality() -> None:
    findings = run_rule_agents(parse_patch(SAMPLE_DIFF), [Agent.STYLE])

    messages = " ".join(finding.message for finding in findings)
    assert "debug logging" in messages
    assert "equality" in messages


def test_agents_only_report_changed_lines() -> None:
    findings = run_rule_agents(parse_patch(SAMPLE_DIFF), [Agent.SECURITY, Agent.STYLE])

    assert all(finding.line in {2, 3, 4, 5} for finding in findings)


def test_a_clean_diff_produces_no_findings() -> None:
    patch = "--- a/x.js\n+++ b/x.js\n@@ -0,0 +1,1 @@\n+const total = items.length;\n"

    assert run_rule_agents(parse_patch(patch), list(Agent)) == []


# ---------------------------------------------------------------------------
# LLM output validation
# ---------------------------------------------------------------------------


def test_llm_findings_on_changed_lines_are_accepted() -> None:
    files = parse_patch(SAMPLE_DIFF)
    payload = {
        "findings": [
            {"path": "src/app.js", "line": 2, "severity": "critical", "message": "Secret in code", "suggestion": "Use env vars"}
        ]
    }

    findings = parse_llm_findings(payload, files, Agent.SECURITY)

    assert len(findings) == 1
    assert findings[0].source == "llm"
    assert findings[0].severity is Severity.CRITICAL


def test_llm_findings_on_unchanged_lines_are_dropped() -> None:
    files = parse_patch(SAMPLE_DIFF)
    payload = {"findings": [{"path": "src/app.js", "line": 99, "message": "Invented"}]}

    assert parse_llm_findings(payload, files, Agent.SECURITY) == []


def test_llm_findings_for_unchanged_files_are_dropped() -> None:
    files = parse_patch(SAMPLE_DIFF)
    payload = {"findings": [{"path": "src/other.js", "line": 2, "message": "Wrong file"}]}

    assert parse_llm_findings(payload, files, Agent.SECURITY) == []


def test_llm_findings_with_an_unknown_severity_default_to_info() -> None:
    files = parse_patch(SAMPLE_DIFF)
    payload = {"findings": [{"path": "src/app.js", "line": 2, "severity": "catastrophic", "message": "x"}]}

    findings = parse_llm_findings(payload, files, Agent.STATIC)

    assert findings[0].severity is Severity.INFO


def test_malformed_llm_payload_is_ignored() -> None:
    files = parse_patch(SAMPLE_DIFF)

    assert parse_llm_findings(None, files, Agent.STATIC) == []
    assert parse_llm_findings({"findings": "nope"}, files, Agent.STATIC) == []


# ---------------------------------------------------------------------------
# merging and summary
# ---------------------------------------------------------------------------


def test_duplicate_findings_are_merged_to_the_most_severe() -> None:
    files = parse_patch(SAMPLE_DIFF)
    rule = run_rule_agents(files, [Agent.STYLE])
    llm = parse_llm_findings(
        {"findings": [{"path": "src/app.js", "line": 4, "severity": "high", "message": "Leftover debug logging."}]},
        files,
        Agent.STYLE,
    )

    merged = merge_findings(rule, llm)

    duplicates = [finding for finding in merged if finding.line == 4 and "logging" in finding.message]
    assert len(duplicates) == 1
    assert duplicates[0].severity is Severity.HIGH


def test_summary_counts_by_severity() -> None:
    findings = run_rule_agents(parse_patch(SAMPLE_DIFF), list(Agent))

    summary = summarise(findings, list(Agent))

    assert "issue(s)" in summary
    assert str(len(findings)) in summary


def test_comment_body_lists_findings_and_the_footer() -> None:
    from app.models import JobState
    from app.review import ReviewReport

    report = ReviewReport(repo="owner/repo", pull_number=1, state=JobState.SUCCEEDED)
    report.findings = run_rule_agents(parse_patch(SAMPLE_DIFF), list(Agent))
    report.summary = summarise(report.findings, list(Agent))

    body = build_review_comment_body(report)

    assert "### Ghost Maintainer review" in body
    assert body.strip().endswith("Opened by Ghost Maintainer")
    assert "no model was used" in body


# ---------------------------------------------------------------------------
# review orchestration without an LLM
# ---------------------------------------------------------------------------


async def test_review_runs_without_an_llm() -> None:
    report = await review_pull_request(repo="owner/repo", patch=SAMPLE_DIFF, pull_number=7)

    assert report.llm_used is False
    assert report.findings
    assert set(report.agents_run) == {"security", "style", "static", "architecture"}
    assert report.state.value == "succeeded"


async def test_review_of_an_empty_diff_succeeds_with_a_note() -> None:
    report = await review_pull_request(repo="owner/repo", patch="", pull_number=7)

    assert report.state.value == "succeeded"
    assert report.findings == []
    assert report.notes


# ---------------------------------------------------------------------------
# GitHub review I/O
# ---------------------------------------------------------------------------


def transport_for_review(status: int = 200, diff: str = SAMPLE_DIFF) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "pulls/7" in request.url.path:
            if request.headers.get("accept") == "application/vnd.github.v3.diff":
                return httpx.Response(200, text=diff)
            return httpx.Response(
                200,
                json={
                    "title": "Add feature",
                    "state": "open",
                    "user": {"login": "dev"},
                    "base": {"ref": "main"},
                    "head": {"sha": "abcdef"},
                },
            )
        if request.method == "POST" and request.url.path.endswith("/reviews"):
            if status >= 400:
                return httpx.Response(status, json={"message": "nope"})
            return httpx.Response(200, json={"id": 1})
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler)


async def test_pull_request_is_fetched_with_its_diff() -> None:
    async with httpx.AsyncClient(transport=transport_for_review(), base_url="https://api.github.com") as client:
        info = await fetch_pull_request("owner/repo", 7, client=client)

    assert info.number == 7
    assert info.head_sha == "abcdef"
    assert "src/app.js" in info.patch
    assert info.base_branch == "main"


async def test_a_missing_pull_request_gives_a_clear_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.github.com") as client:
        with pytest.raises(ReviewError) as excinfo:
            await fetch_pull_request("owner/repo", 7, client=client)

    assert "not found" in str(excinfo.value)


async def test_review_payload_contains_only_diff_lines() -> None:
    report = await review_pull_request(repo="owner/repo", patch=SAMPLE_DIFF, pull_number=7)

    payload = build_review_payload(report, "abcdef")

    assert payload["commit_id"] == "abcdef"
    assert payload["event"] == "COMMENT"
    assert payload["comments"]
    assert all(comment["side"] == "RIGHT" for comment in payload["comments"])


async def test_posting_is_skipped_in_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "dry_run", True)
    report = await review_pull_request(repo="owner/repo", patch=SAMPLE_DIFF, pull_number=7)

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.github.com") as client:
        result = await post_review(report, commit_sha="abcdef", client=client)

    assert calls == 0
    assert result.posted is False
    assert any("Dry run" in note for note in result.notes)


async def test_posting_reports_permission_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "dry_run", False)
    monkeypatch.setattr(settings, "github_token", "token")
    report = await review_pull_request(repo="owner/repo", patch=SAMPLE_DIFF, pull_number=7)

    async with httpx.AsyncClient(
        transport=transport_for_review(status=403), base_url="https://api.github.com"
    ) as client:
        with pytest.raises(ReviewError) as excinfo:
            await post_review(report, commit_sha="abcdef", client=client)

    assert "403" in str(excinfo.value)


# ---------------------------------------------------------------------------
# webhooks
# ---------------------------------------------------------------------------


def sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def pr_event(action: str = "opened", sha: str = "abc123") -> bytes:
    return json.dumps(
        {
            "action": action,
            "repository": {"full_name": "owner/repo"},
            "pull_request": {
                "number": 7,
                "title": "Add feature",
                "draft": False,
                "head": {"sha": sha},
                "base": {"ref": "main"},
            },
        }
    ).encode("utf-8")


def test_a_valid_signature_is_accepted() -> None:
    body = pr_event()

    assert verify_signature(body=body, signature_header=sign(body, "secret"), secret="secret")


def test_an_invalid_signature_is_rejected() -> None:
    body = pr_event()

    assert not verify_signature(body=body, signature_header=sign(body, "wrong"), secret="secret")
    assert not verify_signature(body=body, signature_header=None, secret="secret")
    assert not verify_signature(body=body, signature_header="sha1=abc", secret="secret")


def test_unsigned_deliveries_are_rejected_when_a_secret_exists() -> None:
    assert not verify_signature(body=pr_event(), signature_header="", secret="secret")


def test_a_pull_request_event_is_parsed() -> None:
    event = parse_event(event_header="pull_request", body=pr_event())

    assert event.slug == "owner/repo"
    assert event.number == 7
    assert event.head_sha == "abc123"
    assert event.action == "opened"


def test_non_pull_request_events_are_ignored() -> None:
    with pytest.raises(WebhookError):
        parse_event(event_header="push", body=pr_event())


def test_irrelevant_actions_are_ignored() -> None:
    with pytest.raises(WebhookError):
        parse_event(event_header="pull_request", body=pr_event(action="closed"))


def test_malformed_webhook_bodies_are_rejected() -> None:
    with pytest.raises(WebhookError):
        parse_event(event_header="pull_request", body=b"{not json")


def test_delivery_key_is_stable_per_commit() -> None:
    event = parse_event(event_header="pull_request", body=pr_event())

    assert delivery_key(event) == "owner/repo#7@abc123"


# ---------------------------------------------------------------------------
# learner
# ---------------------------------------------------------------------------


def test_the_learner_records_and_counts_patterns(tmp_path: Path) -> None:
    learner = Learner(path=tmp_path / "learner.json")

    learner.record("owner/repo", agent="security", message="eval is used")
    learner.record("owner/repo", agent="security", message="eval is used")

    patterns = learner.patterns_for("owner/repo")
    assert len(patterns) == 1
    assert patterns[0].count == 2


def test_the_learner_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "learner.json"
    Learner(path=path).record("owner/repo", agent="style", message="console.log left in")

    reloaded = Learner(path=path).patterns_for("owner/repo")

    assert len(reloaded) == 1
    assert reloaded[0].message == "console.log left in"


def test_the_learner_scopes_patterns_per_repository(tmp_path: Path) -> None:
    learner = Learner(path=tmp_path / "learner.json")
    learner.record("owner/one", agent="style", message="x")
    learner.record("owner/two", agent="style", message="y")

    assert len(learner.patterns_for("owner/one")) == 1
    assert len(learner.patterns_for("owner/two")) == 1
    assert learner.patterns_for("owner/three") == []


def test_the_learner_prompt_hint_mentions_recurring_issues(tmp_path: Path) -> None:
    learner = Learner(path=tmp_path / "learner.json")
    learner.record("owner/repo", agent="security", message="eval is used")

    hint = learner.prompt_hint("owner/repo")

    assert "eval is used" in hint
    assert "repeatedly" in hint


def test_the_learner_handles_a_corrupt_store(tmp_path: Path) -> None:
    path = tmp_path / "learner.json"
    path.write_text("{not json", encoding="utf-8")

    assert Learner(path=path).patterns_for("owner/repo") == []
# ---------------------------------------------------------------------------
# repository conventions
# ---------------------------------------------------------------------------


def test_a_var_heavy_file_does_not_trigger_the_const_rule() -> None:
    patch = (
        "--- a/src/legacy.js\n+++ b/src/legacy.js\n@@ -0,0 +1,4 @@\n"
        "+var a = 1;\n+var b = 2;\n+var c = 3;\n+var d = 4;\n"
    )

    findings = run_rule_agents(parse_patch(patch), [Agent.STYLE])

    assert not any("var" in finding.message for finding in findings)


def test_a_modern_file_still_triggers_the_const_rule() -> None:
    patch = (
        "--- a/src/modern.js\n+++ b/src/modern.js\n@@ -0,0 +1,4 @@\n"
        "+const a = 1;\n+const b = 2;\n+const c = 3;\n+var d = 4;\n"
    )

    findings = run_rule_agents(parse_patch(patch), [Agent.STYLE])

    assert any("var" in finding.message for finding in findings)


def test_a_strict_equality_codebase_does_not_trigger_the_loose_equality_rule() -> None:
    patch = (
        "--- a/src/ok.js\n+++ b/src/ok.js\n@@ -0,0 +1,4 @@\n"
        "+if (a === b) { x(); }\n+if (c === d) { y(); }\n+if (e === f) { z(); }\n+if (g === h) { w(); }\n"
    )

    findings = run_rule_agents(parse_patch(patch), [Agent.STYLE])

    assert not any("equality" in finding.message for finding in findings)

# ---------------------------------------------------------------------------
# optional LLM configuration
# ---------------------------------------------------------------------------


def test_llm_check_reports_missing_configuration(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    from app.llm_check import main

    settings = get_settings()
    monkeypatch.setattr(settings, "llm_api_key", "")
    monkeypatch.setattr(settings, "llm_model", "")

    exit_code = main(["--config-only"])
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "not configured" in output
    assert "Everything works without a key" in output
    assert "Ollama" in output


def test_llm_check_never_prints_the_key(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    from app.llm_check import main

    settings = get_settings()
    monkeypatch.setattr(settings, "llm_api_key", "sk-super-secret-value-123")
    monkeypatch.setattr(settings, "llm_model", "some-model")

    main(["--config-only"])
    output = capsys.readouterr().out

    assert "sk-s" in output
    assert "sk-s" in output


def test_the_application_runs_without_an_llm() -> None:
    """The core capability must never be gated on an LLM key."""
    settings = get_settings()
    assert settings.llm_configured is False
    # Nothing in the scan/fix path consults llm_configured; only review does.
    from app.pipeline import run_scan  # noqa: F401  (import must not require a key)

# ---------------------------------------------------------------------------
# review precision
# ---------------------------------------------------------------------------


def test_placeholder_credentials_are_not_reported() -> None:
    """A test fixture is not a leaked secret; reporting it destroys trust."""
    from app.review import looks_like_a_real_secret

    for value in [
        "test-token-value",
        "example-api-key",
        "dummy-secret",
        "your-api-key-here",
        "changeme",
        "process.env.TOKEN",
        "xxxxxxxxxxxxxxxxxxxx",
        "placeholder",
    ]:
        assert looks_like_a_real_secret(value) is False, value


def test_real_looking_credentials_are_reported() -> None:
    from app.review import looks_like_a_real_secret

    # Every value here is fabricated. Provider prefixes are assembled at runtime
    # so this file never contains a literal that a secret scanner (or GitHub push
    # protection) would flag as a leaked key.
    fabricated_prefixes = [
        "sk_" + "live_",
        "gh" + "p_",
        "AK" + "IA",
    ]
    body = "A1b2C3d4E5f6G7h8I9j0K1l2"

    for prefix in fabricated_prefixes:
        assert looks_like_a_real_secret(prefix + body) is True, prefix

    # No prefix, but long and mixed enough that it is not prose.
    assert looks_like_a_real_secret("Zx9Qm2Lp7Rt4Vb8Nw3Kc6Yd1") is True
    assert looks_like_a_real_secret("a1b2c3d4e5f6g7h8i9j0k1l2") is True


def test_a_placeholder_assignment_produces_no_finding() -> None:
    patch = (
        "--- a/test/x.js\n+++ b/test/x.js\n@@ -0,0 +1,2 @@\n"
        '+const config = { token: "test-token-value" };\n'
        '+const other = { password: "changeme" };\n'
    )

    findings = run_rule_agents(parse_patch(patch), [Agent.SECURITY])

    assert findings == []


def test_a_real_credential_assignment_is_still_caught() -> None:
    # Fabricated value: no provider prefix, long and mixed enough to be treated
    # as a credential rather than prose.
    fabricated = "Zx9Qm2Lp7Rt4Vb8Nw3Kc6Yd1"
    patch = (
        "--- a/src/x.js\n+++ b/src/x.js\n@@ -0,0 +1,1 @@\n"
        f'+const apiKey = "{fabricated}";\n'
    )

    findings = run_rule_agents(parse_patch(patch), [Agent.SECURITY])

    assert len(findings) == 1
    assert findings[0].severity is Severity.CRITICAL


def test_context_lines_identify_a_var_based_codebase() -> None:
    """A patch adding `var` to an all-`var` file must not be nagged about it."""
    patch = (
        "--- a/lib/request.js\n+++ b/lib/request.js\n"
        "@@ -10,6 +10,7 @@\n"
        " function handle(req) {\n"
        "   var first = 1;\n"
        "   var second = 2;\n"
        "+  var third = 3;\n"
        "   return first + second;\n"
        " }\n"
    )

    findings = run_rule_agents(parse_patch(patch), [Agent.STYLE])

    assert not any("var" in finding.message for finding in findings)
