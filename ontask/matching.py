"""Rule syntax and classification of a focus target against a profile.

Accepted rule forms (stored as plain strings so they stay hand-editable):

    app:Slack                 match by app name or bundle id
    app:com.tinyspeck.slackmacgap
    site:github.com           matches github.com and any subdomain
    site:*.google.com         explicit wildcard
    site:reddit.com/r/python  host plus path prefix
    Slack                     bare: no dot or slash, treated as an app
    youtube.com               bare: has a dot, treated as a site
"""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch

from .focus import FocusTarget

APPROVED = "approved"
DISAPPROVED = "disapproved"
UNAPPROVED = "unapproved"


@dataclass(frozen=True)
class Rule:
    kind: str  # "app" or "site"
    pattern: str
    raw: str

    @classmethod
    def parse(cls, text: str) -> Rule | None:
        raw = (text or "").strip()
        if not raw or raw.startswith("#"):
            return None
        body = raw
        kind = ""
        for prefix in ("app:", "site:", "url:", "domain:"):
            if body.lower().startswith(prefix):
                kind = "app" if prefix == "app:" else "site"
                body = body[len(prefix):].strip()
                break
        if not body:
            return None
        if not kind:
            kind = "site" if ("." in body or "/" in body) else "app"
        if kind == "site":
            body = _strip_scheme(body)
        return cls(kind=kind, pattern=body.lower(), raw=raw)

    @property
    def specificity(self) -> int:
        """Longer, more qualified patterns win over broad ones."""
        return len(self.pattern) + (10 if "/" in self.pattern else 0)

    def matches(self, target: FocusTarget) -> bool:
        if self.kind == "app":
            return self._matches_app(target)
        return self._matches_site(target)

    def _matches_app(self, target: FocusTarget) -> bool:
        name = (target.app_name or "").lower()
        bundle = (target.bundle_id or "").lower()
        pattern = self.pattern
        if "*" in pattern or "?" in pattern:
            return fnmatch(name, pattern) or fnmatch(bundle, pattern)
        if pattern in (name, bundle):
            return True
        # Allow the last bundle-id segment, e.g. "slackmacgap" or "Slack".
        return bool(bundle) and bundle.endswith("." + pattern)

    def _matches_site(self, target: FocusTarget) -> bool:
        host = target.host
        if not host:
            return False
        pattern_host, _, pattern_path = self.pattern.partition("/")
        if not _host_matches(pattern_host, host):
            return False
        if not pattern_path:
            return True
        wanted = "/" + pattern_path.rstrip("/")
        actual = target.path
        return actual == wanted or actual.startswith(wanted + "/")


def _strip_scheme(value: str) -> str:
    for scheme in ("http://", "https://"):
        if value.lower().startswith(scheme):
            return value[len(scheme):]
    return value


def _host_matches(pattern_host: str, host: str) -> bool:
    if not pattern_host:
        return False
    if "*" in pattern_host or "?" in pattern_host:
        bare = pattern_host.lstrip("*.")
        return fnmatch(host, pattern_host) or host == bare
    return host == pattern_host or host.endswith("." + pattern_host)


def parse_rules(entries) -> list[Rule]:
    rules = []
    for entry in entries or []:
        rule = Rule.parse(entry)
        if rule is not None:
            rules.append(rule)
    return rules


def best_match(rules: list[Rule], target: FocusTarget) -> Rule | None:
    """The most specific matching rule, so site rules can override app rules."""
    matches = [r for r in rules if r.matches(target)]
    return max(matches, key=lambda r: r.specificity) if matches else None


@dataclass(frozen=True)
class Classification:
    status: str
    rule: Rule | None = None

    @property
    def is_approved(self) -> bool:
        return self.status == APPROVED

    @property
    def is_disapproved(self) -> bool:
        return self.status == DISAPPROVED


def classify(target: FocusTarget, approved, disapproved) -> Classification:
    """Decide whether a target is allowed, explicitly blocked, or just unlisted.

    When both lists match, the more specific rule wins, so a profile can approve
    ``github.com`` while still blocking ``github.com/trending``. A tie goes to
    the block list.
    """
    if target.is_unknown:
        # Detection failed (no permission, unsupported desktop). Staying quiet
        # beats nagging on every poll, so treat it as on-task.
        return Classification(APPROVED)
    ok = best_match(parse_rules(approved), target)
    bad = best_match(parse_rules(disapproved), target)
    if bad and (not ok or bad.specificity >= ok.specificity):
        return Classification(DISAPPROVED, bad)
    if ok:
        return Classification(APPROVED, ok)
    return Classification(UNAPPROVED)


def suggest_rule(target: FocusTarget) -> str:
    """The rule string to offer when asking to add a target to a list."""
    if target.host:
        host = target.host[4:] if target.host.startswith("www.") else target.host
        return f"site:{host}"
    return f"app:{target.app_name or target.bundle_id}"
