"""GitHub webhook handling for the pull-request reviewer.

Verifies the HMAC signature, ignores events it does not care about, and skips a
push whose head commit has already been reviewed so a re-delivery does not
produce duplicate comments.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass

logger = logging.getLogger("ghost_maintainer.webhook")

RELEVANT_ACTIONS = {"opened", "synchronize", "reopened", "ready_for_review"}


class WebhookError(Exception):
    """The delivery was not acceptable."""


@dataclass
class PullRequestEvent:
    slug: str
    number: int
    action: str
    head_sha: str
    base_branch: str
    title: str
    draft: bool


def verify_signature(*, body: bytes, signature_header: str | None, secret: str) -> bool:
    """Verify a `X-Hub-Signature-256` header."""
    if not secret:
        return False
    if not signature_header or not signature_header.startswith("sha256="):
        return False

    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    provided = signature_header.split("=", 1)[1].strip()
    return hmac.compare_digest(expected, provided)


def parse_event(*, event_header: str | None, body: bytes) -> PullRequestEvent:
    """Extract the fields the reviewer needs from a delivery."""
    if event_header not in {"pull_request", "pull_request_target"}:
        raise WebhookError(f"Ignoring `{event_header}`: only pull-request events are reviewed.")

    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise WebhookError("The webhook body was not valid JSON.") from exc

    if not isinstance(payload, dict):
        raise WebhookError("The webhook body was not an object.")

    action = str(payload.get("action") or "")
    if action not in RELEVANT_ACTIONS:
        raise WebhookError(f"Ignoring pull_request action `{action}`.")

    repository = payload.get("repository")
    pull = payload.get("pull_request")
    if not isinstance(repository, dict) or not isinstance(pull, dict):
        raise WebhookError("The delivery is missing the repository or pull_request object.")

    full_name = repository.get("full_name")
    if not isinstance(full_name, str) or "/" not in full_name:
        raise WebhookError("The delivery is missing a repository name.")

    try:
        number = int(pull.get("number"))
    except (TypeError, ValueError) as exc:
        raise WebhookError("The delivery is missing a pull-request number.") from exc

    head = pull.get("head") if isinstance(pull.get("head"), dict) else {}
    base = pull.get("base") if isinstance(pull.get("base"), dict) else {}

    return PullRequestEvent(
        slug=full_name,
        number=number,
        action=action,
        head_sha=str(head.get("sha") or ""),
        base_branch=str(base.get("ref") or "main"),
        title=str(pull.get("title") or ""),
        draft=bool(pull.get("draft")),
    )


def delivery_key(event: PullRequestEvent) -> str:
    """A stable key used to skip duplicate deliveries."""
    return f"{event.slug}#{event.number}@{event.head_sha}"