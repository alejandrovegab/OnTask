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

from ..focus import FocusTarget

APPROVED = "approved"
DISAPPROVED = "disapproved"
UNAPPROVED = "unapproved"

# What each status is called on screen. The names above are internal only.
STATUS_LABELS = {APPROVED: "approved", DISAPPROVED: "disapproved", UNAPPROVED: "not listed"}


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
                body = body[len(prefix) :].strip()
                break
        if not body:
            return None
        if not kind:
            kind = "site" if ("." in body or "/" in body) else "app"
        if kind == "site":
            body = _strip_scheme(body)
        return cls(kind=kind, pattern=body.lower(), raw=raw)

    @property
    def display(self) -> str:
        """The rule as a person would name it: no `app:`/`site:` and no scheme."""
        body = self.raw
        for prefix in ("app:", "site:", "url:", "domain:"):
            if body.lower().startswith(prefix):
                body = body[len(prefix) :].strip()
                break
        return _strip_scheme(body) if self.kind == "site" else body

    @property
    def specificity(self) -> int:
        """Longer, more qualified patterns win over broad ones."""
        return len(self.pattern) + (10 if "/" in self.pattern else 0)

    def rank(self, target: FocusTarget) -> tuple[int, int]:
        """How closely this rule, which matches `target`, picks it out.

        Higher wins. Site rules go by specificity. App rules first go by how
        they matched: the exact app (its bundle ID) beats a name, and a name
        beats a wildcard, so `app:com.apple.Notes` beats `app:Notes`, which
        beats `app:*notes*`. Within the same level the longer rule wins.
        """
        if self.kind == "site":
            return (0, self.specificity)
        if "*" in self.pattern or "?" in self.pattern:
            level = 1
        elif self.pattern == (target.bundle_id or "").lower():
            level = 3
        else:
            level = 2
        return (level, self.specificity)

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
            return value[len(scheme) :]
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


def best_match(rules: list[Rule], target: FocusTarget, kind: str | None = None) -> Rule | None:
    """The matching rule that picks `target` out most closely, of `kind` if given."""
    matches = [r for r in rules if (kind is None or r.kind == kind) and r.matches(target)]
    return max(matches, key=lambda r: r.rank(target)) if matches else None


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
    """Decide whether a target is approved, disapproved, or not listed.

    When both lists match, the rule that picks the target out more closely
    wins (see `Rule.rank`), so a profile can approve ``github.com`` while
    disapproving ``github.com/trending``. A tie goes to the disapproved list.

    On a website, site rules come first: any matching site rule beats the
    browser's own rule. With no site rule, an approved browser counts for
    nothing (approving Safari doesn't approve every site in it, so those are
    not listed), while a disapproved browser still disapproves the site. Where
    there is no site to go by (the browser's new-tab page, a tab that couldn't
    be read, or a browser whose tabs aren't tracked) the browser is an
    ordinary app and its rule counts either way.
    """
    if target.is_unknown:
        # Detection failed (no permission, unsupported desktop). Staying quiet
        # beats nagging on every poll, so treat it as on-task.
        return Classification(APPROVED)
    approved_rules = parse_rules(approved)
    disapproved_rules = parse_rules(disapproved)
    if target.host:
        ok = best_match(approved_rules, target, "site")
        bad = best_match(disapproved_rules, target, "site")
        if ok is None and bad is None:
            browser = best_match(disapproved_rules, target, "app")
            if browser is not None:
                return Classification(DISAPPROVED, browser)
            return Classification(UNAPPROVED)
    else:
        ok = best_match(approved_rules, target)
        bad = best_match(disapproved_rules, target)
    if bad and (not ok or bad.rank(target) >= ok.rank(target)):
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


LIST_NAMES = {APPROVED: "approved", DISAPPROVED: "disapproved"}

APPROVE = "approve"
DISAPPROVE = "disapprove"
REMOVE = "remove"


@dataclass(frozen=True)
class RuleAction:
    """One thing the menu can do to the lists for the target in front.

    `verb` is approve, disapprove or remove; `rule` is the stored rule text to
    add to, or remove from, `listname` ("approved" or "disapproved").
    """

    verb: str
    rule: str
    listname: str


def rule_actions(target: FocusTarget, approved, disapproved) -> list[RuleAction]:
    """Only the list changes that would change `target`'s status.

    Not listed: approve or disapprove it. Listed: put it on the other list
    (just this site, or the broader rule that decided, whichever would work),
    or remove the rule that decided it. Nothing when nothing was detected.
    """
    if target.is_unknown:
        return []
    decided = classify(target, approved, disapproved)
    if decided.status == UNAPPROVED:
        rule = suggest_rule(target)
        return [
            RuleAction(APPROVE, rule, LIST_NAMES[APPROVED]),
            RuleAction(DISAPPROVE, rule, LIST_NAMES[DISAPPROVED]),
        ]
    other = DISAPPROVED if decided.status == APPROVED else APPROVED
    verb = APPROVE if other == APPROVED else DISAPPROVE
    actions = [
        RuleAction(verb, rule, LIST_NAMES[other])
        for rule in _flipping_rules(target, approved, disapproved, other, decided.rule)
    ]
    if decided.rule is not None:
        actions.append(RuleAction(REMOVE, decided.rule.raw, LIST_NAMES[decided.status]))
    return actions


def _flipping_rules(target, approved, disapproved, wanted, decider) -> list[str]:
    """Each rule that, added to `wanted`, would put the target there.

    Two candidates, in this order: the suggested rule (the site or app), then
    the rule that decided, moved across. On docs.google.com with google.com
    disapproved both work: an exception for docs.google.com, or all of
    google.com. With github.com/trending disapproved and github.com approved,
    adding github.com would change nothing, so only the move is offered.
    """
    suggested = suggest_rule(target)
    if decider is None:
        candidates = [suggested]
    elif _same_rule(decider, Rule.parse(suggested)):
        # "google.com" or "site:https://google.com" is the suggested rule written
        # another way: one option, moving the rule as it is written.
        candidates = [decider.raw]
    else:
        candidates = [suggested, decider.raw]
    flipping = []
    for rule in candidates:
        moved = moved_rule(approved, disapproved, rule, LIST_NAMES[wanted])
        if classify(target, *moved).status == wanted:
            flipping.append(rule)
    return flipping


def _same_rule(a: Rule | None, b: Rule | None) -> bool:
    return a is not None and b is not None and (a.kind, a.pattern) == (b.kind, b.pattern)


def moved_rule(approved, disapproved, rule: str, listname: str) -> tuple[list, list]:
    """Both lists after adding `rule` to `listname` and taking it off the other."""
    approved = [r for r in approved or [] if r != rule]
    disapproved = [r for r in disapproved or [] if r != rule]
    if listname == LIST_NAMES[APPROVED]:
        approved.append(rule)
    else:
        disapproved.append(rule)
    return approved, disapproved


def friendly_name(rule: str, target: FocusTarget | None = None) -> str:
    """How banners and the menu name a rule: "Messages", "reddit.com/r/python".

    An app rule written as a bundle id names the app it matched instead, so
    `app:com.tinyspeck.slackmacgap` reads as "Slack" while Slack is in front.
    """
    parsed = Rule.parse(rule)
    if parsed is None:
        return rule
    if (
        parsed.kind == "app"
        and target is not None
        and target.app_name
        and not any(c in parsed.pattern for c in "*?")
        and parsed.pattern != target.app_name.lower()
        and parsed.matches(target)
    ):
        return target.app_name
    return parsed.display
