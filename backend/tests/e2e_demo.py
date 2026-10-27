"""Verify the full workflow against the real demo repository.

Starts the app in-process, scans the demo, then exercises a safe bump and a
package replacement through the sandbox. This is the end-to-end proof that the
dashboard's flow works, independent of the UI.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402


def wait_for(client: TestClient, path: str, timeout: float = 300.0) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        response = client.get(path)
        last = response.json()
        if last.get("state") in {"succeeded", "failed"}:
            return last
        time.sleep(1.0)
    return last


def main() -> None:
    settings = get_settings()
    settings.allow_untrusted_local_execution = True
    settings.sandbox_mode = "local_restricted"

    with TestClient(app) as client:
        print("== scan ==")
        started = time.time()
        response = client.post("/api/scan", json={"repo_url": "demo"})
        response.raise_for_status()
        scan_id = response.json()["job_id"]
        job = wait_for(client, f"/api/scan/{scan_id}")
        print(f"state={job['state']} in {time.time() - started:.1f}s")
        if job["state"] != "succeeded":
            print("ERROR:", job.get("error"))
            for entry in job.get("logs", []):
                print(f"  {entry['level']:7} {entry['message']}")
            raise SystemExit(1)

        result = job["result"]
        summary = result["summary"]
        print(
            f"deps={summary['total_dependencies']} rotten={summary['rotten']} aging={summary['aging']} "
            f"healthy={summary['healthy']} alerts={summary['total_alerts']} "
            f"observed_alerts={summary['alerts_with_observed_usage']}"
        )
        print(f"graph nodes={len(result['graph']['nodes'])} edges={len(result['graph']['edges'])}")
        for dep in result["dependencies"]:
            fixes = ", ".join(option["type"] for option in dep["fix_options"]) or "-"
            print(
                f"  {dep['name']:14} {str(dep['version']):9} rot={dep['rot']['value']:3} "
                f"{dep['rot']['label']:8} {dep['reachability']['classification']:13} fixes=[{fixes}]"
            )

        for package in ("lodash", "moment"):
            print(f"\n== fix {package} ==")
            started = time.time()
            body = {"scan_id": scan_id, "package": package}
            if package == "moment":
                body["fix_type"] = "package_replacement"
            response = client.post("/api/fix", json=body)
            if response.status_code != 202:
                print("  request failed:", response.status_code, response.text)
                continue
            fix_id = response.json()["job_id"]
            job = wait_for(client, f"/api/fix/{fix_id}")
            print(f"  state={job['state']} in {time.time() - started:.1f}s")
            if job["state"] != "succeeded":
                print("  ERROR:", job.get("error"))
                continue

            fix = job["result"]
            plan = fix.get("plan") or {}
            sandbox = fix.get("sandbox") or {}
            pr = fix.get("pr") or {}
            print(f"  plan: {plan.get('type')} {plan.get('from_version')} -> {plan.get('to_version') or plan.get('replacement')}")
            print(f"  files: {', '.join(plan.get('files_to_change') or [])}")
            print(f"  sandbox: {sandbox.get('status')} baseline={sandbox.get('baseline_passed')} after={sandbox.get('after_passed')}")
            print(f"  pr: mode={pr.get('mode')} created={pr.get('created')} url={pr.get('url')}")
            print(f"  diff lines: {len((sandbox.get('diff') or '').splitlines())}")
            if sandbox.get("error"):
                print(f"  sandbox error: {sandbox['error']}")
            if sandbox.get("baseline_output_tail"):
                print(f"  baseline tail: {(sandbox.get('baseline_output_tail') or '')[-600:]}")


if __name__ == "__main__":
    main()
