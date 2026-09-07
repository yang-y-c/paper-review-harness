from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from paper_review_lib import (
    HarnessError,
    find_root,
    load_config,
    load_json,
    load_state,
    resolve_repo_path,
    route_claim,
    validate_with_schema,
)


@dataclass
class Check:
    id: str
    name: str
    status: str
    detail: str

    @property
    def passed(self) -> bool:
        return self.status in {"PASS", "SKIP"}


def _check(identifier: str, name: str, passed: bool, detail: str) -> Check:
    return Check(identifier, name, "PASS" if passed else "FAIL", detail)


def _schema_checks(root: Path) -> list[Check]:
    targets = [
        ("claims.json", "claims.schema.json"),
        ("issues.json", "issues.schema.json"),
        ("revisions.json", "revisions.schema.json"),
        ("verifications.json", "verifications.schema.json"),
        ("global_contract.json", "global-contract.schema.json"),
        ("structure.json", "structure.schema.json"),
        ("granular_review.json", "granular-review.schema.json"),
        ("final_audit.json", "final-audit.schema.json"),
    ]
    checks: list[Check] = []
    for file_name, schema_name in targets:
        try:
            validate_with_schema(root, schema_name, load_json(root / ".review" / file_name))
            checks.append(_check("S01", f"Schema: {file_name}", True, "valid"))
        except HarnessError as exc:
            checks.append(_check("S01", f"Schema: {file_name}", False, str(exc)))
    return checks


def _duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicate: set[str] = set()
    for value in values:
        if value in seen:
            duplicate.add(value)
        seen.add(value)
    return sorted(duplicate)


def _find_dependency_cycle(claims: list[dict[str, Any]]) -> list[str] | None:
    graph = {claim["id"]: claim.get("dependencies", []) for claim in claims}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str, trail: list[str]) -> list[str] | None:
        if node in visiting:
            index = trail.index(node)
            return trail[index:] + [node]
        if node in visited:
            return None
        visiting.add(node)
        for dependency in graph.get(node, []):
            cycle = visit(dependency, trail + [dependency])
            if cycle:
                return cycle
        visiting.remove(node)
        visited.add(node)
        return None

    for node in graph:
        cycle = visit(node, [node])
        if cycle:
            return cycle
    return None


def _integrity_checks(root: Path) -> list[Check]:
    claims = load_json(root / ".review" / "claims.json")["claims"]
    issues = load_json(root / ".review" / "issues.json")["issues"]
    revisions = load_json(root / ".review" / "revisions.json")["revisions"]
    verifications = load_json(root / ".review" / "verifications.json")["verifications"]
    claim_ids = [claim["id"] for claim in claims]
    issue_ids = [issue["id"] for issue in issues]
    known_claims = set(claim_ids)
    known_issues = set(issue_ids)
    checks = [
        _check("S02", "Unique Claim IDs", not _duplicates(claim_ids), str(_duplicates(claim_ids)) or "unique"),
        _check("S03", "Unique Issue IDs", not _duplicates(issue_ids), str(_duplicates(issue_ids)) or "unique"),
    ]
    unknown_dependencies = sorted(
        {
            dependency
            for claim in claims
            for dependency in claim.get("dependencies", [])
            if dependency not in known_claims
        }
    )
    checks.append(
        _check(
            "S04",
            "Claim dependency references",
            not unknown_dependencies,
            str(unknown_dependencies) if unknown_dependencies else "all dependencies exist",
        )
    )
    cycle = _find_dependency_cycle(claims) if not unknown_dependencies else None
    checks.append(
        _check("S05", "Claim dependency graph", cycle is None, " -> ".join(cycle) if cycle else "acyclic")
    )
    bad_issue_claims = sorted(
        issue["id"]
        for issue in issues
        if issue.get("claim_id") is not None and issue["claim_id"] not in known_claims
    )
    checks.append(
        _check(
            "S06",
            "Issue Claim references",
            not bad_issue_claims,
            str(bad_issue_claims) if bad_issue_claims else "all Claim references exist",
        )
    )
    bad_revision_issues = sorted(
        revision["id"] for revision in revisions if revision["issue_id"] not in known_issues
    )
    bad_verification_issues = sorted(
        verification["id"]
        for verification in verifications
        if verification["issue_id"] not in known_issues
    )
    checks.append(
        _check(
            "S07",
            "Revision Issue references",
            not bad_revision_issues,
            str(bad_revision_issues) if bad_revision_issues else "all Issue references exist",
        )
    )
    checks.append(
        _check(
            "S08",
            "Verification Issue references",
            not bad_verification_issues,
            str(bad_verification_issues) if bad_verification_issues else "all Issue references exist",
        )
    )
    invalid_resolved = sorted(
        issue["id"]
        for issue in issues
        if issue["status"] == "RESOLVED" and issue["verification_status"] != "PASS"
    )
    invalid_pass = sorted(
        issue["id"]
        for issue in issues
        if issue["verification_status"] == "PASS" and issue["status"] != "RESOLVED"
    )
    bad_status = sorted(set(invalid_resolved + invalid_pass))
    checks.append(
        _check(
            "S09",
            "Resolution invariant",
            not bad_status,
            str(bad_status) if bad_status else "RESOLVED iff verifier status is PASS",
        )
    )
    return checks


def _gate_checks(root: Path, config: dict[str, Any]) -> list[Check]:
    claims_ledger = load_json(root / ".review" / "claims.json")
    claims = claims_ledger["claims"]
    coverage = claims_ledger["coverage"]
    issues = load_json(root / ".review" / "issues.json")["issues"]
    global_contract = load_json(root / ".review" / "global_contract.json")
    structure = load_json(root / ".review" / "structure.json")
    granular_review = load_json(root / ".review" / "granular_review.json")
    final_audit = load_json(root / ".review" / "final_audit.json")
    state = load_state(root)
    request_id = state.get("active_request_id") or state.get("last_request_id")
    selected_granularity = None
    if request_id:
        request_path = root / ".review" / "requests" / f"{request_id}.json"
        if request_path.is_file():
            selected_granularity = load_json(request_path).get("granularity", {}).get("level")
    revisions = load_json(root / ".review" / "revisions.json")["revisions"]
    gates = config["gates"]
    claim_by_id = {claim["id"]: claim for claim in claims}
    def unresolved(issue: dict[str, Any]) -> bool:
        return issue["status"] != "RESOLVED"

    checks: list[Check] = []

    checks.append(
        _check(
            "G12",
            "Global manuscript contract solidified",
            not gates.get("require_global_contract", True)
            or global_contract["status"] == "CURRENT",
            global_contract["status"],
        )
    )
    checks.append(
        _check(
            "G13",
            "Hierarchy review completed at selected depth",
            not gates.get("require_hierarchy_review", True)
            or (
                structure["status"] == "NOT_APPLICABLE"
                if selected_granularity == "MACRO_ONLY"
                else structure["status"] == "CURRENT"
            ),
            structure["status"],
        )
    )
    checks.append(
        _check(
            "G14",
            "Humanizer-backed granular review completed",
            not gates.get("require_granular_review", True)
            or granular_review["status"] == "CURRENT",
            granular_review["status"],
        )
    )
    checks.append(
        _check(
            "G15",
            "Final cross-layer integrity audit passed",
            not gates.get("require_final_integrity_audit", True)
            or final_audit["status"] == "PASS",
            final_audit["status"],
        )
    )

    blockers = sorted(issue["id"] for issue in issues if issue["severity"] == "BLOCKER" and unresolved(issue))
    checks.append(
        _check(
            "G04",
            "No unresolved BLOCKER",
            not gates.get("block_open_blocker", True) or not blockers,
            str(blockers) if blockers else "none",
        )
    )
    core_majors = sorted(
        issue["id"]
        for issue in issues
        if issue["severity"] == "MAJOR"
        and unresolved(issue)
        and issue.get("claim_id") in claim_by_id
        and claim_by_id[issue["claim_id"]]["centrality"] == "CORE"
    )
    checks.append(
        _check(
            "G05",
            "No unresolved MAJOR on CORE Claims",
            not gates.get("block_core_major", True) or not core_majors,
            str(core_majors) if core_majors else "none",
        )
    )
    core_without_evidence = sorted(
        claim["id"] for claim in claims if claim["centrality"] == "CORE" and not claim["evidence"]
    )
    checks.append(
        _check(
            "G06",
            "Every CORE Claim has evidence",
            not gates.get("require_core_evidence", True) or not core_without_evidence,
            str(core_without_evidence) if core_without_evidence else "all covered",
        )
    )
    unchallenged = sorted(
        claim["id"]
        for claim in claims
        if claim["strength"] in {"STRONG", "EXTREME"}
        and claim["adversarial_status"] != "REVIEWED"
    )
    checks.append(
        _check(
            "G07",
            "Every strong Claim has adversarial review",
            not gates.get("require_strong_claim_challenge", True) or not unchallenged,
            str(unchallenged) if unchallenged else "all covered",
        )
    )
    severe_revised_issue_ids = {
        revision["issue_id"]
        for revision in revisions
        if claim_by_id.get(
            next(
                (issue.get("claim_id") for issue in issues if issue["id"] == revision["issue_id"]),
                None,
            ),
            {},
        ).get("centrality")
        == "CORE"
        or next(
            (issue["severity"] for issue in issues if issue["id"] == revision["issue_id"]),
            None,
        )
        in {"BLOCKER", "MAJOR"}
    }
    issue_by_id = {issue["id"]: issue for issue in issues}
    unverified = sorted(
        issue_id
        for issue_id in severe_revised_issue_ids
        if issue_id not in issue_by_id
        or issue_by_id[issue_id]["status"] != "RESOLVED"
        or issue_by_id[issue_id]["verification_status"] != "PASS"
    )
    checks.append(
        _check(
            "G08",
            "Severe/Core revisions independently verified",
            not gates.get("require_independent_verification", True) or not unverified,
            str(unverified) if unverified else "all covered",
        )
    )
    checks.append(
        _check(
            "G09",
            "Conclusion Claim mapping declared complete",
            not gates.get("require_conclusion_mapping", True) or coverage["conclusion_mapped"],
            "complete" if coverage["conclusion_mapped"] else "claim mapper reports incomplete coverage",
        )
    )
    abstract_ok = coverage["abstract_mapped"] and coverage["abstract_numerical_claims_mapped"]
    checks.append(
        _check(
            "G10",
            "Abstract and numerical Claim mapping declared complete",
            not gates.get("require_abstract_mapping", True) or abstract_ok,
            "complete" if abstract_ok else "claim mapper reports incomplete coverage",
        )
    )
    missing_reviews: list[str] = []
    for claim in claims:
        expected = {agent for agent in route_claim(claim) if agent != "challenger"}
        missing = sorted(expected - set(claim.get("reviewed_by", [])))
        if missing:
            missing_reviews.append(f"{claim['id']}:{','.join(missing)}")
    checks.append(
        _check(
            "G11",
            "Dynamic reviewer coverage",
            not gates.get("require_routed_review_coverage", True) or not missing_reviews,
            str(missing_reviews) if missing_reviews else "all routed reviewers completed",
        )
    )
    return checks


def _format_build_command(command: list[str], root: Path, main_tex: Path) -> list[str]:
    values = {
        "root": str(root),
        "main_tex": str(main_tex),
        "main_dir": str(main_tex.parent),
        "main_name": main_tex.name,
    }
    return [str(part).format(**values) for part in command]


def _select_build(config: dict[str, Any], root: Path, main_tex: Path) -> tuple[list[str], Path] | None:
    configured = config.get("build_command", [])
    if configured:
        if not isinstance(configured, list) or not all(isinstance(item, str) for item in configured):
            raise HarnessError("build_command must be an array of command arguments")
        return _format_build_command(configured, root, main_tex), root
    candidates = [
        ("latexmk", ["latexmk", "-pdf", "-interaction=nonstopmode", main_tex.name]),
        ("tectonic", ["tectonic", main_tex.name]),
        ("xelatex", ["xelatex", "-interaction=nonstopmode", main_tex.name]),
        ("pdflatex", ["pdflatex", "-interaction=nonstopmode", main_tex.name]),
    ]
    for executable, command in candidates:
        if shutil.which(executable):
            return command, main_tex.parent
    return None


def _compile_checks(root: Path, config: dict[str, Any], final: bool) -> list[Check]:
    if not final:
        return [Check("G01", "LaTeX compilation", "SKIP", "use --final to compile")]
    if not config["gates"].get("require_compilation", True):
        return [Check("G01", "LaTeX compilation", "SKIP", "disabled in config")]
    try:
        main_tex = resolve_repo_path(root, config["main_tex"])
    except HarnessError as exc:
        return [_check("G01", "LaTeX compilation", False, str(exc))]
    if not main_tex.is_file():
        return [_check("G01", "LaTeX compilation", False, f"main TeX file not found: {main_tex}")]
    try:
        selected = _select_build(config, root, main_tex)
    except HarnessError as exc:
        return [_check("G01", "LaTeX compilation", False, str(exc))]
    if selected is None:
        return [_check("G01", "LaTeX compilation", False, "no configured command or supported TeX engine found")]
    command, cwd = selected
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=int(config.get("compile_timeout_seconds", 180)),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [_check("G01", "LaTeX compilation", False, str(exc))]
    combined = completed.stdout + "\n" + completed.stderr
    log_path = main_tex.parent / f"{main_tex.stem}.log"
    if log_path.is_file():
        combined += "\n" + log_path.read_text(encoding="utf-8", errors="replace")
    unresolved_refs = bool(
        re.search(r"undefined references|reference .+ undefined|rerun to get cross-references right", combined, re.I)
    )
    unresolved_citations = bool(re.search(r"undefined citations|citation .+ undefined", combined, re.I))
    return [
        _check(
            "G01",
            "LaTeX compilation",
            completed.returncode == 0,
            "success" if completed.returncode == 0 else f"exit code {completed.returncode}",
        ),
        _check("G02", "No unresolved references", not unresolved_refs, "none" if not unresolved_refs else "warnings detected"),
        _check("G03", "No unresolved citations", not unresolved_citations, "none" if not unresolved_citations else "warnings detected"),
    ]


def evaluate(root: Path, final: bool = False) -> dict[str, Any]:
    config = load_config(root)
    checks: list[Check] = []
    try:
        checks.extend(_schema_checks(root))
        if all(check.passed for check in checks):
            checks.extend(_integrity_checks(root))
        if all(check.passed for check in checks):
            checks.extend(_gate_checks(root, config))
    except HarnessError as exc:
        checks.append(_check("S00", "Harness state readable", False, str(exc)))
    checks.extend(_compile_checks(root, config, final))
    return {
        "passed": all(check.passed for check in checks),
        "final": final,
        "checks": [asdict(check) for check in checks],
    }


def render_text(report: dict[str, Any]) -> str:
    lines = []
    for check in report["checks"]:
        lines.append(f"[{check['status']}] {check['id']} {check['name']}: {check['detail']}")
    lines.append("PASS" if report["passed"] else "FAIL")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run deterministic Paper Review Harness gates")
    parser.add_argument("--root", type=Path, help="paper repository root")
    parser.add_argument("--final", action="store_true", help="include compilation gates")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)
    try:
        root = find_root(args.root)
        report = evaluate(root, final=args.final)
    except HarnessError as exc:
        report = {"passed": False, "final": args.final, "checks": [asdict(_check("S00", "Harness setup", False, str(exc)))]}
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render_text(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
