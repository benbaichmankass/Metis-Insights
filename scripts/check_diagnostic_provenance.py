#!/usr/bin/env python3
"""CI guard: a diagnostic must carry the provenance of what it printed.

WHY (the defect class this guard exists to prevent recurring)
-------------------------------------------------------------
A tool reports a value under a label that does not describe what it computed,
and nothing in the output reveals the substitution. The number is real. The
label is confident. A reader who trusts the label reaches a confident WRONG
conclusion — and, because every individual component is correct, line-by-line
review keeps coming back clean.

Seven instances landed on 2026-07-30 alone. Every one was caught by luck or
paranoia, not by any guard:

* ``_feature_parity_probe`` printed the shadow log's ``score`` under the label
  "PREDICTED score(volatile)". That field is
  ``MulticlassPredictor.predict == max(proba.values())`` — ``>= 0.5`` by
  construction for a 2-class head — so a head 97% sure the regime is CALM
  printed 0.97 under a label saying volatile. The meaning is INVERTED. That
  line came one step from escalating a false P1 against the live real-money
  BTC vol gate (BL-20260730-PARITY-PROBE-MISLABELS-MAXPROBA, PR #8091).
* The same probe took ``sorted(glob(...))[-1]`` — the alphabetically last
  dataset version — instead of the manifest's pinned one, and labelled it
  "TRAINING dataset". It compared a head against data it never trained on.
* ``vm-diag-snapshot`` posted "Common causes: VM_SSH_KEY unset, VM web-api
  down" for a path-VALIDATION rejection where no VM contact is attempted. A
  false "VM down" diagnosis was recorded off it twice and posted to the
  coordination board (BL-20260730-DIAG-RELAY-FAILURE-COMMENT-MISLEADING,
  PR #8097).
* ``bybit_bracket_audit``'s roll-up printed "every audited symbol is fully
  SL-covered at the broker" while a 444.7% over-coverage sat in the body.

This is the same family as ``canonical-db-resolver``, ``env-gate-guard``,
``silent-empty-guard`` and ``provenance-consumer-guard``: rules and docs did
not prevent it (the canonical "green is not evidence" rule was written the
same day), and a guard can. It is the DIAGNOSTIC-OUTPUT analogue of
``src/runtime/provenance.py``, which does the same job for stored PnL:
*is this number MEASURED or MANUFACTURED, and does the output say so?*

Note the division of labour with ``silent-empty-guard``: that guard catches
the PRODUCER (a broad except that returns ``[]``). This one catches the
CONSUMER (reading ``[]``/a substituted value as a clean, labelled answer).
Neither covers the other.

WHAT IT CHECKS
--------------
Only on the DIAGNOSTIC SURFACE (``_SURFACE_PREFIXES``) — code whose output a
human reads and acts on. Three checks, each mechanically decidable:

A. SEMANTIC SUBSTITUTION — reading a predictor-class-dependent value (the
   shadow log's ``score`` key, or a bare ``.predict(``) in a file that also
   prints a probability-shaped label. ``score`` is ``predict()``: P(positive)
   for a binary head, ``max(proba)`` for a multiclass one. Fix by calling
   ``predict_proba(row)[<class>]`` — the quantity the live gate thresholds —
   or by naming what you actually read.

B. IMPLICIT INPUT SELECTION — ``glob``/``.glob`` then ``[-1]``/``[0]``/
   ``getmtime``-newest, i.e. picking the newest/alphabetically-last input
   rather than the declared or pinned one. Fix by resolving the pin, or at
   minimum by PRINTING the path that was chosen so a fallback is never
   silent.

C. UNQUANTIFIED UNIVERSAL CLAIM — printing "every/all/no/none/nothing …"
   without a denominator in scope. An empty or truncated result then reads as
   a clean negative: a ``curl … || echo '{}'`` poller turned an HTTP 403 into
   ``0 checks``, which read as "nothing pending". Fix by printing the count
   the claim ranges over.

THE OVERRIDE IS VERIFIED, NOT PRESENCE-ONLY
-------------------------------------------
    # provenance: predict_proba — reports P(volatile), the gate's quantity

The token after ``provenance:`` must be an identifier that ACTUALLY APPEARS
elsewhere in the file. This is deliberate. ``new-table-wiring-guard`` accepts
any ``# data-wiring:`` line, so the path of least resistance to silencing it
is to name a table that does not exist — a guard that teaches contributors to
lie to it is worse than no guard. Naming an accessor the file never calls is
rejected here.

Exit 0 = clean. Exit 1 = at least one unannotated finding.

Usage:
    python3 scripts/check_diagnostic_provenance.py [DIFF]   # diff on stdin/argv
    python3 scripts/check_diagnostic_provenance.py --all    # sweep the tree
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import sys
from typing import Dict, Iterable, List, NamedTuple, Optional, Sequence, Set, Tuple

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The diagnostic surface: code whose output a human reads and acts on.
# Deliberately NOT all of src/ — a runtime module that computes a value for
# another machine is out of scope; this is about human-facing claims.
_SURFACE_PREFIXES: Tuple[str, ...] = (
    "scripts/ml/",
    "scripts/research/",
    "scripts/ops/",
    "scripts/analysis/",
    "scripts/macro/",
    "scripts/reports/",
    # Added 2026-07-31 after the guard MISSED the worst instance of its own
    # class: `market_features`'s docstring documented `trend_threshold` as
    # label-defining while `_label_regime` accepts it and never reads it. A
    # dataset builder's docstring is a human-facing claim about what a
    # parameter does — the same defect as a mislabelled print, one level up.
    # Checks D/E below are the ones that apply here; A/B/C rarely fire.
    "ml/datasets/",
    "ml/labeling/",
    "ml/evaluators/",
)
# Guards are diagnostics too — their "clean" is a claim a human acts on.
_SURFACE_FILES: Tuple[str, ...] = tuple(
    f"scripts/{n}" for n in (
        "check_new_table_wiring.py", "check_db_integrity.py",
        "check_strategy_coverage.py", "check_writer_conformance.py",
    )
)
_EXEMPT_RE = re.compile(r"(^|/)tests?/|/test_[^/]+\.py$|"
                        r"scripts/check_diagnostic_provenance\.py$")

# --- A: predictor-class-dependent value reads ------------------------------ #
# `score` in the shadow log / order_packages.model_scores is
# `ShadowPredictor.predict` -> `wrapped.predict(row)`. For a binary head that
# is P(positive); for a MulticlassPredictor it is `max(proba.values())`. One
# field, two meanings, no marker in the data saying which.
_AMBIGUOUS_READ_RES: Tuple[Tuple[str, re.Pattern], ...] = (
    ("shadow `score` field", re.compile(r'''\.get\(\s*["']score["']|\[\s*["']score["']\s*\]''')),
    ("bare .predict()", re.compile(r'\.predict\(')),
)
# NOT the repo's `Predictor` surface: a raw LightGBM/sklearn estimator's
# `.predict(X)` over an array is a different API with unambiguous semantics.
# Flagging those would be a false positive, and a guard that cries wolf gets
# silenced wholesale — the alarm-fatigue failure mode this repo already names
# as its own P1 bug class (MB-20260719-DATASET-AUDIT-NOISE).
_RAW_ESTIMATOR_RE = re.compile(
    r'\b(booster|bst|clf|regressor|estimator|model|cal|calibrator)\s*\.\s*predict\(|'
    r'\.predict\(\s*(np\.|X[\b_\s,)]|matrix\b|arr\b|\[\s*\[)',
    re.IGNORECASE,
)
# A label that asserts a probability / class-conditional quantity.
_PROBABILITY_CLAIM_RE = re.compile(
    r'''P\(|p_vol|prob(?:ability)?\b|likelihood|confidence\b|'''
    r'''\bP\s*\(\s*volatile|volatile\)|calm\)''',
    re.IGNORECASE,
)
# Calling these proves the author reached for the unambiguous accessor.
_DISAMBIGUATING_CALLS = ("predict_proba", "predict_label")

# --- B: implicit input selection -------------------------------------------- #
_GLOB_RE = re.compile(r'\bglob\.glob\(|\.glob\(|\.rglob\(')
_NEWEST_PICK_RE = re.compile(r'\[\s*-\s*1\s*\]|\[\s*0\s*\]|getmtime|getctime|\bmax\(')
# Evidence the choice is declared rather than discovered.
_PIN_HINT_RE = re.compile(
    r'''manifest|dataset\.version|pinned|\bversion\b|_manifest_dataset|'''
    r'''--dataset|args\.dataset''', re.IGNORECASE,
)

# --- C: universal claims ----------------------------------------------------- #
_PRINT_RE = re.compile(r'\bprint\s*\(|\blog(?:ger)?\.(?:info|warning|error)\s*\(')
_UNIVERSAL_CLAIM_RE = re.compile(
    r'''\b(every|all)\s+(?:\w+\s+){0,3}(?:is|are|were|has|have)\b|'''
    r'''\bno\s+(?:\w+\s+){0,3}(?:found|detected|remain|remaining)\b|'''
    r'''\bnothing\s+(?:vacuous|wrong|to\s+flag|offending)\b|'''
    r'''\bnone\s+(?:found|detected)\b|\bclean\b''',
    re.IGNORECASE,
)
# A denominator: ANY value interpolated into the claim, or a %-format slot.
# Deliberately permissive — the target is the claim asserted as a bare string
# literal with nothing behind it ("every audited symbol is fully SL-covered at
# the broker."), which is the shape that reads as a clean bill of health while
# a 444.7% over-coverage sits in the body above it. A claim that shows ANY
# number at least gives the reader something to check.
_DENOMINATOR_RE = re.compile(r'\{[^}]+\}|%[ds]|\bformat\(')

# The verified override.
_ANNOTATION_RE = re.compile(r'#\s*provenance:\s*(.+)$', re.IGNORECASE)
_IDENT_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_.]{2,}')

_CONTEXT_LINES = 3

# --- D: a parameter accepted and never read ---------------------------------- #
# The ROOT of the worst instance this guard initially missed. `_label_regime`
# takes `trend_threshold` and never references it (dead since the 3-class ->
# 2-class collapse), so every caller passing it, and every doc describing it,
# was asserting an effect that does not exist. A parameter the body never reads
# is either dead or a bug; either way no doc may claim it does something.
# Override: `# inert: <param> — <reason>` on the parameter's own line —
# deliberate back-compat is fine, silently pretending is not.
#
# VERIFIED, NOT PRESENCE-ONLY (2026-09-02). This was a bare `# inert:` marker,
# which made the cheapest way to silence a real finding a four-word comment
# naming nothing — the exact failure mode `new-table-wiring-guard`'s
# presence-only `# data-wiring:` marker had, and the one `annotation_for`
# already refuses for `# provenance:`. A guard cheaper to lie to than to
# satisfy is worse than no guard. The marker must now NAME THE PARAMETER it
# excuses, so it cannot be copy-pasted between parameters and cannot survive a
# rename of the thing it claims to describe.
_INERT_OK_RE = re.compile(r'#\s*inert:\s*(.+)$', re.IGNORECASE)
# Names that are conventionally accepted-and-ignored.
_INERT_EXEMPT = {"self", "cls", "args", "kwargs", "_"}

# --- E: a conclusion printed unconditionally --------------------------------- #
# The m20 probe printed "a materially MORE NEGATIVE mean future_dR in 'hi'
# means the head carries exit information" with every bucket at n=0 — an ABSENT
# measurement rendering identically to a measured one. Sub-class C did not fire
# because the sentence contains no universal quantifier. What makes it wrong is
# that it is UNCONDITIONAL: an interpretation emitted whether or not anything
# was measured.
_CONCLUSION_RE = re.compile(
    r'\b(interpretation|means that|indicates|suggests|implies|'
    r'conclude|conclusion|=\s*the\s+\w+\s+carries|carries\s+\w+\s+information|'
    r'candidate for a[n]?\s+\w+\s+experiment|no\s+\w+\s+signal\s+in)\b',
    re.IGNORECASE,
)


class Finding(NamedTuple):
    path: str
    lineno: int
    check: str
    detail: str
    snippet: str

    def render(self) -> str:
        return (f"{self.path}:{self.lineno} [{self.check}] {self.detail}\n"
                f"      {self.snippet}")


# --------------------------------------------------------------------------- #
# diff parsing
# --------------------------------------------------------------------------- #
def iter_added(diff_text: str) -> Iterable[Tuple[str, int, str]]:
    """Yield ``(path, new_lineno, content)`` for every added line in a diff."""
    path: Optional[str] = None
    lineno = 0
    for raw in diff_text.splitlines():
        if raw.startswith("+++ "):
            target = raw[4:].strip()
            path = (None if target == "/dev/null"
                    else target[2:] if target.startswith(("a/", "b/")) else target)
            lineno = 0
            continue
        if raw.startswith("@@"):
            m = re.search(r"\+(\d+)(?:,\d+)?", raw)
            lineno = int(m.group(1)) - 1 if m else 0
            continue
        if raw.startswith(("---", "diff ")):
            continue
        if raw.startswith("+") and not raw.startswith("++"):
            lineno += 1
            if path:
                yield path, lineno, raw[1:]
            continue
        if raw.startswith("-"):
            continue
        lineno += 1


def in_surface(path: str) -> bool:
    if _EXEMPT_RE.search(path):
        return False
    if not path.endswith(".py"):
        return False
    return path.startswith(_SURFACE_PREFIXES) or path in _SURFACE_FILES


# --------------------------------------------------------------------------- #
# annotation validation — the anti-"lie to the guard" half
# --------------------------------------------------------------------------- #
def _file_identifiers(lines: Sequence[str]) -> Set[str]:
    """Identifiers in the file, EXCLUDING the annotation lines themselves.

    An annotation must not count as evidence for itself. Pooling its own words
    into the identifier set makes any prose ("this is definitely fine, trust
    me") validate, which restores exactly the presence-only behaviour this
    rule exists to remove.
    """
    out: Set[str] = set()
    for line in lines:
        if _ANNOTATION_RE.search(line):
            continue
        out.update(_IDENT_RE.findall(line))
    return out


def annotation_for(
    lines: Sequence[str], idx: int, idents: Set[str],
) -> Tuple[bool, Optional[str]]:
    """Is line ``idx`` covered by a VERIFIED ``# provenance:`` annotation?

    Returns ``(covered, rejection_reason)``. The annotation must sit on the
    line itself or within ``_CONTEXT_LINES`` above it, and must name at least
    one identifier that actually appears elsewhere in the file. A marker that
    names nothing real is rejected — that is the exact failure mode of
    ``new-table-wiring-guard``'s presence-only ``# data-wiring:`` marker,
    whose path of least resistance is to name a table that does not exist.
    """
    reason: Optional[str] = None
    lo = max(0, idx - _CONTEXT_LINES)
    for probe in range(idx, lo - 1, -1):
        m = _ANNOTATION_RE.search(lines[probe])
        if not m:
            continue
        body = m.group(1)
        named = [t for t in _IDENT_RE.findall(body) if t in idents]
        if named:
            return True, None
        reason = (f"`# provenance:` at line {probe + 1} names no identifier "
                  f"that appears in this file — name the exact accessor you "
                  f"called (e.g. `predict_proba`), not prose")
    return False, reason


# --------------------------------------------------------------------------- #
# the three checks
# --------------------------------------------------------------------------- #
def _near(lines: Sequence[str], idx: int, pattern: re.Pattern) -> bool:
    lo, hi = max(0, idx - _CONTEXT_LINES), min(len(lines), idx + _CONTEXT_LINES + 1)
    return any(pattern.search(lines[i]) for i in range(lo, hi))


def _ast_findings(path: str, lines: Sequence[str]) -> List[Finding]:
    """Checks D and E — both need structure, not regex over lines."""
    out: List[Finding] = []
    try:
        tree = ast.parse("\n".join(lines))
    except SyntaxError:
        return out

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        # --- D: parameters accepted and never referenced in the body -------
        # An abstract/stub body ignores every parameter BY DESIGN — an ABC
        # declaring an interface asserts nothing about what the args do.
        # Flagging those is the alarm-fatigue failure this guard must avoid.
        real_body = [st for st in node.body
                     if not (isinstance(st, ast.Expr)
                             and isinstance(st.value, ast.Constant))]
        is_stub = not real_body or all(
            isinstance(st, ast.Pass)
            or (isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant)
                and st.value.value is Ellipsis)
            or (isinstance(st, ast.Raise)
                and "NotImplementedError" in (ast.dump(st) or ""))
            for st in real_body)
        if is_stub or any(
                isinstance(d, ast.Name) and d.id in ("abstractmethod", "overload")
                or isinstance(d, ast.Attribute) and d.attr in ("abstractmethod", "overload")
                for d in node.decorator_list):
            continue

        a = node.args
        params = [p for p in (a.posonlyargs + a.args + a.kwonlyargs)]
        if a.vararg or a.kwarg:
            # **kwargs / *args absorb the unused; the signature is deliberately
            # permissive and "unused" carries no claim.
            params = []
        body_names: Set[str] = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name):
                body_names.add(sub.id)
            elif isinstance(sub, ast.Attribute):
                body_names.add(sub.attr)
        # The signature line itself is not a use.
        for p in params:
            name = p.arg
            if name in _INERT_EXEMPT or name.startswith("_"):
                continue
            if name in body_names:
                continue
            ln = getattr(p, "lineno", node.lineno)
            line = lines[ln - 1] if 0 < ln <= len(lines) else ""
            inert = _INERT_OK_RE.search(line)
            # Word-boundary match on the EXACT parameter name, deliberately not
            # `_IDENT_RE.findall`: that pattern requires >= 3 characters, so a
            # parameter called `df`, `n` or `id` could never satisfy its own
            # override — permanently un-excusable, with renaming as the only
            # escape. Verified: `df` was refused even when correctly named.
            if inert and re.search(rf"\b{re.escape(name)}\b", inert.group(1)):
                continue
            bad_inert = (
                f" (`# inert:` on this line does not name `{name}` — an "
                f"override must name the parameter it excuses)"
                if inert else ""
            )
            out.append(Finding(
                path, ln, "D/inert-parameter",
                f"`{node.name}` accepts `{name}` and never reads it. A parameter "
                f"the body ignores is either dead or a bug — and any doc saying "
                f"it affects behaviour is then a false claim. This is the exact "
                f"root of the trend_threshold mislabel. Remove it, use it, or "
                f"mark it `# inert: {name} — <reason>`" + bad_inert,
                (line.strip() or f"def {node.name}(...)")[:120]))

        # --- E: an interpretation emitted unconditionally ------------------
        # Only top-level statements of the function body: a conclusion nested
        # under `if`/`for`/`try` is at least gated on something.
        # A guard clause (`if <cond>: ... return`) EARLIER in the body makes a
        # later top-level print conditional in effect. That is the idiomatic
        # way to write "no conclusion when there is nothing to conclude from",
        # and flagging it would penalise the exact fix this check asks for.
        guarded_from = None
        for idx, st in enumerate(node.body):
            if isinstance(st, ast.If) and any(
                    isinstance(x, ast.Return) for x in ast.walk(st)):
                guarded_from = idx
                break
        for pos, stmt in enumerate(node.body):
            if guarded_from is not None and pos > guarded_from:
                continue
            if not isinstance(stmt, ast.Expr):
                continue
            call = stmt.value
            if not (isinstance(call, ast.Call)
                    and getattr(call.func, "id", "") == "print"):
                continue
            seg = ast.get_source_segment("\n".join(lines), call) or ""
            if not _CONCLUSION_RE.search(seg):
                continue
            ln = call.lineno
            line = lines[ln - 1] if 0 < ln <= len(lines) else ""
            out.append(Finding(
                path, ln, "E/unconditional-conclusion",
                "prints an interpretation at function top level, so it is "
                "emitted whether or not anything was measured — an ABSENT "
                "result then reads identically to a measured one. Gate it on a "
                "non-zero denominator and say NO CONCLUSION AVAILABLE otherwise",
                line.strip()[:120]))
    return out


def check_file(
    path: str, lines: Sequence[str], targets: Optional[Set[int]] = None,
) -> List[Finding]:
    """Scan ``lines`` (0-indexed) of ``path``. ``targets`` limits to added lines."""
    findings: List[Finding] = []
    idents = _file_identifiers(lines)
    blob = "\n".join(lines)
    file_disambiguates = any(c in blob for c in _DISAMBIGUATING_CALLS)
    # A file that prints a probability-shaped label anywhere is a file whose
    # ambiguous reads matter; a pure data-mover is not making a claim.
    file_claims_probability = bool(_PROBABILITY_CLAIM_RE.search(blob))

    # Structural checks (D, E) run first; they are annotation-aware below.
    for f in _ast_findings(path, lines):
        if targets is not None and f.lineno not in targets:
            continue
        covered, _ = annotation_for(lines, f.lineno - 1, idents)
        if not covered:
            findings.append(f)

    for i, line in enumerate(lines):
        if targets is not None and (i + 1) not in targets:
            continue
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        covered, bad_annotation = annotation_for(lines, i, idents)
        snippet = stripped[:120]

        def _emit(check: str, detail: str) -> None:
            if covered:
                return
            extra = f" ({bad_annotation})" if bad_annotation else ""
            findings.append(Finding(path, i + 1, check, detail + extra, snippet))

        # --- A ------------------------------------------------------------
        if (file_claims_probability and not file_disambiguates
                and not _RAW_ESTIMATOR_RE.search(line)):
            for label, rx in _AMBIGUOUS_READ_RES:
                if rx.search(line):
                    _emit(
                        "A/semantic-substitution",
                        f"reads {label} in a file that prints a probability-shaped "
                        f"label. `score`/`predict()` is P(positive) for a binary "
                        f"head but max(proba) for a MulticlassPredictor — >= 0.5 "
                        f"by construction, and HIGH for a confidently-CALM regime. "
                        f"Call predict_proba(row)[<class>] or state what you read",
                    )
                    break

        # --- B ------------------------------------------------------------
        if _GLOB_RE.search(line) or (
            _NEWEST_PICK_RE.search(line) and _near(lines, i, _GLOB_RE)
        ):
            if _NEWEST_PICK_RE.search(line) or _near(lines, i, _NEWEST_PICK_RE):
                if not _near(lines, i, _PIN_HINT_RE) and not _near(lines, i, _PRINT_RE):
                    _emit(
                        "B/implicit-input-selection",
                        "picks the newest / alphabetically-last discovered input "
                        "instead of a declared or pinned one, and never prints "
                        "which it chose. Resolve the pin, or at minimum print the "
                        "path so a fallback is never silent",
                    )

        # --- C ------------------------------------------------------------
        # The denominator must be in the CLAIM ITSELF, not merely nearby: the
        # bybit-bracket roll-up printed "every audited symbol is fully
        # SL-covered at the broker." three lines below a formatted detail line,
        # and a proximity window would have cleared it. The whole point is that
        # a reader of the summary line alone must be able to check it.
        if _PRINT_RE.search(line) and _UNIVERSAL_CLAIM_RE.search(line):
            if not _DENOMINATOR_RE.search(line):
                _emit(
                    "C/unquantified-universal-claim",
                    "asserts a universal ('every/all/no/none/clean') with no "
                    "denominator in scope. An empty or truncated result then "
                    "reads as a clean negative — print the count the claim "
                    "ranges over",
                )

    return findings


# --------------------------------------------------------------------------- #
# drivers
# --------------------------------------------------------------------------- #
def _read(path: str) -> Optional[List[str]]:
    full = os.path.join(_REPO_ROOT, path)
    try:
        with open(full, encoding="utf-8", errors="replace") as fh:
            return fh.read().splitlines()
    except OSError:
        return None


def scan_diff(diff_text: str) -> List[Finding]:
    """Flag added lines, but judge them with FULL-FILE context.

    Whether a read is a mislabel depends on what the file prints and which
    accessors it calls — neither is visible from the added lines alone.
    """
    added: Dict[str, Set[int]] = {}
    for path, lineno, _ in iter_added(diff_text):
        if in_surface(path):
            added.setdefault(path, set()).add(lineno)
    findings: List[Finding] = []
    for path, linenos in sorted(added.items()):
        lines = _read(path)
        if lines is None:  # deleted in this PR
            continue
        findings.extend(check_file(path, lines, targets=linenos))
    return findings


def scan_tree() -> List[Finding]:
    findings: List[Finding] = []
    for prefix in _SURFACE_PREFIXES:
        root = os.path.join(_REPO_ROOT, prefix)
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".git")]
            for fn in sorted(filenames):
                rel = os.path.relpath(os.path.join(dirpath, fn), _REPO_ROOT)
                if not in_surface(rel):
                    continue
                lines = _read(rel)
                if lines is not None:
                    findings.extend(check_file(rel, lines))
    for rel in _SURFACE_FILES:
        lines = _read(rel)
        if lines is not None:
            findings.extend(check_file(rel, lines))
    return findings


_EXPLAINER = """
  A diagnostic that reports a value under a label describing a DIFFERENT
  quantity does not merely confuse — it produces confident wrong conclusions
  that survive review, because every component is individually correct. On
  2026-07-30 this class printed max(proba) as "P(volatile)" (a 97%-CALM head
  read as saturated-volatile, one step from a false P1 against the live
  real-money BTC gate), compared a head against a dataset it never trained on,
  and blamed the VM for a request that never reached it.

  Fix by making the derivation visible:
    A  call predict_proba(row)[<class>] — the quantity the gate thresholds —
       or label the value for what it is.
    B  resolve the declared/pinned input; print the path either way.
    C  print the denominator the claim ranges over.

  The override is `# provenance: <accessor> — <what it means>` on or just
  above the line. The named accessor MUST appear in the file: naming
  something that does not exist is how new-table-wiring-guard taught
  contributors to lie to it, and is rejected here.
"""


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="diagnostic-provenance guard")
    ap.add_argument("diff", nargs="?", help="unified diff (default: stdin)")
    ap.add_argument("--all", action="store_true",
                    help="sweep the whole diagnostic surface instead of a diff")
    args = ap.parse_args(argv[1:])

    if args.all:
        findings = scan_tree()
    else:
        text = (open(args.diff, encoding="utf-8", errors="replace").read()
                if args.diff else sys.stdin.read())
        findings = scan_diff(text)

    if not findings:
        print("diagnostic-provenance: OK — every scanned diagnostic states "
              "what it computed.")
        return 0

    print("diagnostic-provenance guard: FAIL\n", file=sys.stderr)
    for f in findings:
        print(f"  - {f.render()}", file=sys.stderr)
    print(_EXPLAINER, file=sys.stderr)
    for f in findings:
        print(f"DIAGNOSTIC_PROVENANCE_GUARD\t{f.path}:{f.lineno}\t{f.check}")
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv))
