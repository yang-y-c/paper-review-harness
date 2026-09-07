"""Source-grounded multiscale review, deterministic routing, and trace gates.

This is a conservative TeX source inventory, not a TeX expansion engine. Raw math,
macros, captions and lists remain in their surrounding source paragraph. Unsupported
conditional/dynamic structure is reported and blocks coverage certification.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from paper_review_lib import (
    HarnessError, load_config, load_json, make_run_id, manuscript_snapshot,
    merge_review_output, resolve_repo_path, sha256_value, utc_now,
    validate_with_schema, write_json,
    update_state,
)
from provenance import append_event


POLICY = {
    "section_threshold": 40,
    "sentence_threshold": 40,
    "weights": {
        "SECTION_GAP": 40, "PARAGRAPH_GAP": 40, "CORE_CLAIM": 40,
        "INFERENCE_CONNECTOR": 45, "STRONG_ASSERTION": 45,
        "FORMULA": 40, "DISCOURSE_CONNECTOR": 20,
        "MISSING_PREMISE": 50, "SCOPE_JUMP": 50,
        "UNSUPPORTED_CAUSALITY": 50, "AMBIGUOUS_REFERENCE": 40,
        "FORMULA_CONCLUSION": 45, "STRONG_CLAIM": 45, "TRANSITION_GAP": 40,
        "RELATION_DISAGREEMENT": 50,
        "DEFINITION_OR_ASSUMPTION": 40, "SEMANTIC_ROLE": 40,
    },
}
DEEP_MODES = {"PARAGRAPH", "SENTENCE", "ADAPTIVE"}
STRUCTURE = re.compile(
    r"\\(?P<heading>chapter|section|subsection|subsubsection)\*?"
    r"(?:\[[^\]]*\])?\s*\{|\\(?P<include>input|include)\s*\{"
    r"|\\(?P<abstract>begin|end)\{abstract\}"
    r"|\\par(?![A-Za-z])|\n\s*\n"
)
DEPTH = {"chapter": 1, "section": 2, "subsection": 3, "subsubsection": 4}
MATH = re.compile(
    r"\\begin\{(equation\*?|align\*?|gather\*?|multline\*?)\}[\s\S]*?\\end\{\1\}"
    r"|\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)|(?<!\\)\$\$[\s\S]*?(?<!\\)\$\$"
    r"|(?<!\\)\$(?:\\.|[^$])*(?<!\\)\$"
)


def _protect_math(text: str) -> str:
    # Non-whitespace placeholders prevent paragraph/sentence splitting inside math.
    return MATH.sub(lambda m: "\uffff" * len(m[0]), text)


def _braced(text: str, start: int) -> tuple[str, int]:
    depth = 1
    pos = start
    while pos < len(text):
        if text[pos] == "\\":
            pos += 2
            continue
        depth += (text[pos] == "{") - (text[pos] == "}")
        if not depth:
            return text[start:pos], pos + 1
        pos += 1
    raise HarnessError("Unbalanced structural TeX argument")


def _mask_comments(text: str) -> str:
    # Preserve offsets and newlines for exact source locations.
    return re.sub(r"(?<!\\)%[^\n]*", lambda m: " " * len(m[0]), text)


def build_inventory(root: Path) -> dict[str, Any]:
    config = load_config(root)
    units: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    errors: list[str] = []
    sources: dict[str, str] = {}
    stack: list[tuple[int, str]] = []

    def add(kind: str, parent: str | None, text: str, path: str,
            start: int, end: int, depth: int = 0, label: str = "") -> dict[str, Any]:
        start += len(text) - len(text.lstrip())
        end -= len(text) - len(text.rstrip())
        identity = f"{kind}|{parent}|{path}|{label or ' '.join(text.split())}"
        counts[identity] += 1
        uid = "PAPER" if kind == "PAPER" else (
            {"SECTION": "S", "PARAGRAPH": "P", "SENTENCE": "X"}[kind]
            + hashlib.sha256(f"{identity}|{counts[identity]}".encode()).hexdigest()[:16]
        )
        raw = sources.get(path, "")
        line = raw.count("\n", 0, start) + 1
        item = {"id": uid, "parent_id": parent, "level": kind, "depth": depth,
                "text": text.strip(), "path": path, "start": start, "end": end,
                "location": f"{path}:{line}", "ordinal": len(units)}
        units.append(item)
        return item

    add("PAPER", None, "Manuscript", config["main_tex"], 0, 0)

    def parent(path: str) -> str:
        if not stack:
            section = add("SECTION", "PAPER", "Front matter", path, 0, 0, 0, "front-matter")
            stack.append((0, section["id"]))
        return stack[-1][1]

    def paragraph(text: str, path: str, start: int, end: int) -> None:
        if not text.strip():
            return
        p = add("PARAGRAPH", parent(path), text, path, start, end)
        # Split only outside braces and math; retain abbreviations and decimal points.
        boundaries = [0]
        brace_depth = 0
        math = False
        for pos, char in enumerate(_protect_math(text)):
            if pos and text[pos - 1] == "\\":
                continue
            if char == "$":
                math = not math
            if char == "{":
                brace_depth += 1
            elif char == "}":
                brace_depth = max(0, brace_depth - 1)
            elif not brace_depth and not math and char in ".!?。！？":
                tail = text[:pos + 1]
                abbrev = re.search(r"(?:\b(?:e\.g|i\.e|et al|Fig|Eq|Sec|Dr|vs)|\b[A-Z])\.$", tail)
                if not abbrev and (pos + 1 == len(text) or text[pos + 1].isspace() or char in "。！？"):
                    boundaries.append(pos + 1)
        boundaries.append(len(text))
        for left, right in zip(boundaries, boundaries[1:]):
            if text[left:right].strip():
                add("SENTENCE", p["id"], text[left:right], path, start + left, start + right)

    def visit(path: Path, active: tuple[Path, ...] = ()) -> None:
        path = path.resolve()
        relative = path.relative_to(root.resolve()).as_posix()
        if path in active:
            errors.append(f"recursive include:{relative}")
            return
        if not path.is_file():
            errors.append(f"missing include:{relative}")
            return
        raw = path.read_text(encoding="utf-8-sig")
        sources[relative] = raw
        text = _mask_comments(raw)
        scan_text = _protect_math(text)
        if re.search(r"\\(?:if\w*|else|fi|includeonly|import|subimport|subfile|catcode|csname)\b", text):
            errors.append(f"unsupported conditional/dynamic TeX structure:{relative}")
        if re.search(r"\\(?:input|include)\b(?!\s*\{)", text):
            errors.append(f"include requires a literal braced path:{relative}")
        # Macro definitions can hide headings/includes; do not certify an incomplete tree.
        for definition in re.finditer(r"\\(?:newcommand|renewcommand|providecommand|def)\b", text):
            opening = text.find("{", definition.end())
            if opening >= 0:
                body, after = _braced(text, opening + 1)
                if not definition[0].endswith("def"):
                    # First argument names the command; the next braced argument
                    # is its body (possibly after an arity/default-value option).
                    opening = text.find("{", after)
                    if opening >= 0:
                        body, _ = _braced(text, opening + 1)
                if re.search(r"\\(?:chapter|section|subsection|subsubsection|input|include)\b", body):
                    errors.append(f"structure-producing macro:{relative}")
        begin = text.find(r"\begin{document}")
        offset = begin + len(r"\begin{document}") if begin >= 0 else 0
        end = text.find(r"\end{document}", offset)
        end = len(text) if end < 0 else end
        if begin >= 0:
            title = re.search(r"\\title(?:\[[^\]]*\])?\s*\{", text[:begin])
            if title:
                title_text, title_end = _braced(text, title.end())
                paragraph(title_text, relative, title.end(), title_end - 1)
        cursor = offset
        while cursor < end:
            match = STRUCTURE.search(scan_text, cursor, end)
            if not match:
                paragraph(text[cursor:end], relative, cursor, end)
                break
            paragraph(text[cursor:match.start()], relative, cursor, match.start())
            cursor = match.end()
            if match.group("heading"):
                heading, cursor = _braced(text, cursor)
                depth = DEPTH[match.group("heading")]
                while stack and stack[-1][0] >= depth:
                    stack.pop()
                # Front matter is a sibling, never the parent of a real heading.
                if stack and stack[-1][0] == 0:
                    stack.clear()
                node = add("SECTION", stack[-1][1] if stack else "PAPER",
                           heading, relative, match.end(), cursor - 1, depth)
                stack.append((depth, node["id"]))
            elif match.group("include"):
                name, cursor = _braced(text, cursor)
                if any(token in name for token in ("\\", "#", "{")):
                    errors.append(f"dynamic include:{relative}:{name}")
                    continue
                name = name if Path(name).suffix else name + ".tex"
                candidate = path.parent / name
                if not candidate.is_file():
                    candidate = root / name
                try:
                    safe = resolve_repo_path(root, candidate.resolve().relative_to(root.resolve()).as_posix())
                    visit(safe, (*active, path))
                except (ValueError, HarnessError) as exc:
                    errors.append(f"unreadable include:{relative}:{name}:{exc}")
            elif match.group("abstract") == "begin":
                stack.clear()
                node = add("SECTION", "PAPER", "Abstract", relative, match.start(), cursor, 0)
                stack.append((0, node["id"]))
            elif match.group("abstract") == "end":
                stack.clear()

    visit(resolve_repo_path(root, config["main_tex"]))
    if not any(u["level"] == "PARAGRAPH" for u in units):
        errors.append("no reviewable source paragraphs")
    return {"schema_version": 1, "source_snapshot": manuscript_snapshot(root),
            "source_files": {p: hashlib.sha256(t.encode()).hexdigest() for p, t in sorted(sources.items())},
            "units": units, "errors": sorted(set(errors))}


def select_sections(inventory: dict, mode: str) -> list[dict]:
    if mode == "MACRO_ONLY":
        return []
    return [u for u in inventory["units"] if u["level"] == "SECTION"
            and (mode != "SECTION" or u["parent_id"] == "PAPER")]


def policy_from(config: dict) -> dict:
    policy = json.loads(json.dumps(POLICY))
    supplied = config.get("coherence_policy", {})
    for key in ("section_threshold", "sentence_threshold"):
        value = supplied.get(key, policy[key])
        if type(value) is not int or not 1 <= value <= 100:
            raise HarnessError(f"coherence_policy.{key} must be an integer in 1..100")
        policy[key] = value
    return policy


def schedule(inventory: dict, structure: dict, paragraphs: list[dict], claims: list[dict],
             mode: str, policy: dict, disputed_paragraphs: set[str] | None = None) -> dict:
    sections = {n.get("source_unit_id"): n for n in (structure.get("payload") or {}).get("nodes", [])}
    p_records = {r["node_id"]: r for r in paragraphs}
    core = {c["id"] for c in claims if c["centrality"] == "CORE" and c["status"] != "REMOVED"}
    units = {u["id"]: u for u in inventory["units"]}
    routes = []
    for unit in inventory["units"]:
        if unit["level"] != "SENTENCE":
            continue
        p = p_records.get(unit["parent_id"])
        reasons: set[str] = set()
        if unit["parent_id"] in (disputed_paragraphs or set()):
            reasons.add("RELATION_DISAGREEMENT")
        section_id = units[unit["parent_id"]]["parent_id"]
        while section_id != "PAPER":
            if sections.get(section_id, {}).get("status", "PASS") != "PASS":
                reasons.add("SECTION_GAP")
            section_id = units[section_id]["parent_id"]
        if p:
            if p["status"] != "PASS":
                reasons.add("PARAGRAPH_GAP")
            if core.intersection(p["claim_ids"]):
                reasons.add("CORE_CLAIM")
            reasons.update(p["risk_signals"])
            if p["rhetorical_role"] in {"CLAIM", "EVIDENCE", "DEFINITION", "ASSUMPTION"}:
                reasons.add("SEMANTIC_ROLE")
        text = unit["text"]
        if re.search(r"\b(therefore|thus|hence|implies|because|since|consequently)\b|因此|所以|由此|推出|由于", text, re.I):
            reasons.add("INFERENCE_CONNECTOR")
        if re.search(r"\b(define[sd]?|definition|assume[sd]?|assumption|suppose)\b|定义|假设|设定", text, re.I):
            reasons.add("DEFINITION_OR_ASSUMPTION")
        if re.search(r"\b(prove[sd]?|demonstrates?|guarantees?|necessarily|sufficient|equivalent|unique|optimal)\b|证明|保证|必然|充分|等价|唯一", text, re.I):
            reasons.add("STRONG_ASSERTION")
        if re.search(r"\$|\\(?:\[|\(|begin\{(?:equation|align))", text):
            reasons.add("FORMULA")
        if re.search(r"\b(however|specifically|nevertheless|although|in contrast)\b|然而|但是|具体而言", text, re.I):
            reasons.add("DISCOURSE_CONNECTOR")
        score = min(100, sum(policy["weights"][r] for r in reasons))
        section_score = policy["weights"]["SECTION_GAP"] if "SECTION_GAP" in reasons else 0
        selected = mode == "SENTENCE" or (mode == "ADAPTIVE" and (
            score >= policy["sentence_threshold"] or section_score >= policy["section_threshold"]
        ))
        routes.append({"node_id": unit["id"], "paragraph_id": unit["parent_id"],
                       "score": score, "reasons": sorted(reasons), "selected": selected,
                       "deep_review": selected,
                       "decision": "FULL_SENTENCE" if mode == "SENTENCE" else (
                           "RISK_THRESHOLD" if selected else "BELOW_THRESHOLD" if mode == "ADAPTIVE" else "DEPTH_LIMIT")})
    # Recover a whole risky paragraph once. Low-risk sibling sentences are local
    # context nodes, not additional high-intensity inferential checks.
    if mode == "ADAPTIVE":
        expanded = {r["paragraph_id"] for r in routes if r["selected"]}
        for route in routes:
            if route["paragraph_id"] in expanded and not route["selected"]:
                route.update(selected=True, decision="LOCAL_CONTEXT")
    return {"mode": mode, "policy": policy, "routes": routes,
            "selected_sentence_ids": [r["node_id"] for r in routes if r["selected"]]}


def _exact(actual: list[str], expected: list[str], label: str) -> None:
    if len(actual) != len(set(actual)) or set(actual) != set(expected):
        raise HarnessError(f"{label}: missing={sorted(set(expected)-set(actual))}, extra={sorted(set(actual)-set(expected))}, duplicates={len(actual)-len(set(actual))}")


def validate_records(root: Path, output: dict, stage: str, expected: list[str],
                     inventory: dict, records: list[dict]) -> None:
    validate_with_schema(root, "coherence-output.schema.json", output)
    agent = "final_integrity_auditor" if stage == "BOTTOM_UP" else "language_coherence_reviewer"
    if output["stage"] != stage or output["agent"] != agent:
        raise HarnessError("Coherence pass role/stage mismatch")
    if output["humanizer_skill"] != ("NOT_APPLICABLE" if stage == "BOTTOM_UP" else "humanizer"):
        raise HarnessError("Coherence language passes require humanizer")
    selected = output["bottom_up"] if stage == "BOTTOM_UP" else output["records"]
    if output["records"] if stage == "BOTTOM_UP" else output["bottom_up"]:
        raise HarnessError("Coherence pass includes records from another stage")
    _exact([r["node_id"] for r in selected], expected, stage + " coverage")
    issues = {i["id"] for i in output["issues"]}
    known_claims = {c["id"] for c in load_json(root / ".review/claims.json")["claims"] if c["status"] != "REMOVED"}
    _known = {
        "claim_ids": known_claims,
        "terminology_ids": {t["id"] for t in load_json(root / ".review/terminology.json")["payload"]["concepts"]},
        "notation_ids": {n["id"] for n in load_json(root / ".review/notation.json")["payload"]["symbols"]},
        "argument_node_ids": {n["id"] for n in load_json(root / ".review/argument_graph.json")["payload"]["nodes"]},
    }
    units = {u["id"]: u for u in inventory["units"]}
    reviewed = {r["node_id"] for r in records}
    bottom_seen: set[str] = set()
    linked_issues: set[str] = set()
    for record in selected:
        if set(record["issue_ids"]) - issues:
            raise HarnessError("Coherence record references an unknown Issue")
        linked_issues.update(record["issue_ids"])
        if record["status"] != "PASS" and not record["issue_ids"]:
            raise HarnessError("Every coherence gap needs a structured Issue")
        if record["status"] == "PASS" and record["issue_ids"]:
            raise HarnessError("PASS cannot carry unresolved coherence Issues")
        if stage == "BOTTOM_UP":
            children = [u["id"] for u in inventory["units"]
                        if u["parent_id"] == record["node_id"] and u["id"] in reviewed]
            _exact(record["child_ids"], children, "Bottom-up child coverage")
            if any(c in expected and c not in bottom_seen for c in children):
                raise HarnessError("Bottom-up must reconstruct children before parents")
            bottom_seen.add(record["node_id"])
        else:
            if units[record["node_id"]]["level"] != stage:
                raise HarnessError("Coherence record level mismatch")
            for field, known in _known.items():
                if set(record[field]) - known:
                    raise HarnessError(f"Unknown coherence {field}")
            if stage == "SENTENCE" and not all(record[f].strip() for f in ("inference", "conclusion", "discourse_relation")):
                raise HarnessError("Critical sentence must expose inference/conclusion/relation")
    if issues - linked_issues:
        raise HarnessError("Every coherence Issue must link to a source node")


def _bindings(root: Path) -> dict:
    bindings = {name: sha256_value(load_json(root / ".review" / name)) for name in (
        "global_contract.json", "structure.json", "terminology.json", "notation.json",
        "argument_graph.json",
    )}
    claims = load_json(root / ".review/claims.json")["claims"]
    bindings["claims.json"] = sha256_value([
        {k: v for k, v in c.items() if k not in {"reviewed_by", "adversarial_status", "notes", "status"}}
        for c in claims if c["status"] != "REMOVED"
    ])
    return bindings


def _base_records(root: Path, inventory: dict, mode: str) -> list[dict]:
    macro = load_json(root / ".review/global_contract.json")["payload"]["contract"]
    structure = load_json(root / ".review/structure.json")
    nodes = (structure.get("payload") or {}).get("nodes", [])
    expected = [u["id"] for u in select_sections(inventory, mode)]
    _exact([n.get("source_unit_id", "MISSING") for n in nodes], expected, "Source/hierarchy coverage")
    mapping = {n["id"]: n["source_unit_id"] for n in nodes}
    by_id = {u["id"]: u for u in inventory["units"]}
    records = [{"node_id": "PAPER", "purpose": macro["theme"]["research_question"],
                "inputs": [macro["abstract"]["gap"]], "outputs": macro["conclusion"]["supported_findings"],
                "logic_chain": macro["logic_chain"], "claim_ids": sorted({c for n in macro["logic_chain"] for c in n["claim_ids"]}),
                "status": "PASS" if all(n["status"] == "SUPPORTED" for n in macro["logic_chain"]) else "GAP"}]
    for n in nodes:
        source = by_id[n["source_unit_id"]]
        if (mapping.get(n["parent_id"]) if n["parent_id"] else "PAPER") != source["parent_id"]:
            raise HarnessError("Hierarchy/source parent mismatch")
        records.append({"node_id": source["id"], "hierarchy_id": n["id"], "purpose": n["purpose"],
                        "inputs": [n["input_from_previous"]], "outputs": [n["output_to_next"]],
                        "claim_ids": n["claim_ids"], "transition_in": n["transition_in"],
                        "transition_out": n["transition_out"], "status": n["status"]})
    return records


def run_coherence(workflow: Any, inventory: dict, mode: str) -> dict:
    from logic_graph import run_relations, escalation_paragraphs, materialize_summary, record_relation_issues
    root = workflow.root
    if inventory["errors"]:
        raise HarnessError("Cannot certify source coverage: " + "; ".join(inventory["errors"]))
    run_id = make_run_id("multiscale")
    before = _bindings(root)
    records = _base_records(root, inventory, mode)
    structure = load_json(root / ".review/structure.json")
    claims = load_json(root / ".review/claims.json")["claims"]
    policy = policy_from(load_config(root))
    units = {u["id"]: u for u in inventory["units"]}
    passes = []

    def call(stage: str, targets: list[str], plan: dict | None = None) -> dict:
        agent = "final_integrity_auditor" if stage == "BOTTOM_UP" else "language_coherence_reviewer"
        update_state(root, phase="COHERENCE_" + stage, active_run_id=run_id + "-" + stage.lower())
        assignment = {
            "task": "MULTISCALE_COHERENCE", "stage": stage, "mode": mode,
            "output_schema": "coherence-output.schema.json",
            "source_snapshot": inventory["source_snapshot"],
            "target_units": [units[i] for i in targets],
            "parent_contracts": records,
            "source_context": [u for u in inventory["units"] if u["level"] != "SENTENCE" or u["id"] in targets],
            "registry_ids": {k: v["payload"] for k, v in workflow.invariant_ledgers.items()},
            "scheduler": plan,
            "instructions": (
                "Independently reconstruct actual child outputs from source, in child-before-parent order. "
                "For each paragraph/section/paper compare the reconstructed output with its parent purpose. "
                "Include every reviewed direct child ID; do not treat top-down PASS as evidence. "
                "Do not read reviser explanations."
                if stage == "BOTTOM_UP" else
                "Load $humanizer. Preserve science. Record each paragraph's rhetorical role, purpose, inputs, "
                "outputs and canonical Claim/argument/term/symbol IDs. For critical sentences expose "
                "premises, inference, conclusion and discourse relation; test connectors, formulas and "
                "references. State 'not stated' for missing premises, and raise a GAP/NEEDS_AUTHOR Issue. "
                "Record risk signals and precise transitions to adjacent units. Do not rereview unrelated content."
            ),
        }
        output = workflow.runner.run(agent, assignment, run_id + "-" + stage.lower(),
                                     output_schema="coherence-output.schema.json")
        update_state(root, last_run_id=run_id + "-" + stage.lower(), active_run_id=None)
        validate_records(root, output, stage, targets, inventory, records)
        passes.append({"stage": stage, "run_id": run_id + "-" + stage.lower(), "output": output})
        return output

    p_ids = [u["id"] for u in inventory["units"] if u["level"] == "PARAGRAPH"] if mode in DEEP_MODES else []
    if p_ids:
        output = call("PARAGRAPH", p_ids)
        records.extend(output["records"])
    coarse_graph = run_relations(workflow, inventory, records, "coarse")
    plan = schedule(inventory, structure, [r for r in records if r["node_id"] in p_ids], claims, mode, policy,
                    escalation_paragraphs(coarse_graph, inventory))
    # Persist routing before execution, including unselected candidates and their reasons.
    plan_path = root / ".review/runs" / run_id / "routing.json"
    write_json(plan_path, plan)
    append_event(root, "coherence.routed", {"run_id": run_id, "selected": len(plan["selected_sentence_ids"])},
                 run_id=run_id, artifact_refs=[{"path": plan_path.relative_to(root).as_posix()}])
    if plan["selected_sentence_ids"]:
        output = call("SENTENCE", plan["selected_sentence_ids"], plan)
        records.extend(output["records"])
    logic_graph = run_relations(workflow, inventory, records, "fine") if plan["selected_sentence_ids"] else coarse_graph
    reviewed = {r["node_id"] for r in records}
    bottom_ids = [u["id"] for u in reversed(inventory["units"])
                  if u["id"] in reviewed and u["level"] != "SENTENCE"]
    bottom = call("BOTTOM_UP", bottom_ids, plan)
    if build_inventory(root) != inventory or _bindings(root) != before:
        raise HarnessError("Source or parent contracts changed during multiscale review")
    for item in passes:
        out = item["output"]
        merge_review_output(root, {k: out[k] for k in ("agent", "reviewed_claims", "issues", "review_notes")})
    registry = {"schema_version": 1, "status": "CURRENT", "run_id": run_id, "updated_at": utc_now(),
                "mode": mode, "inventory": inventory, "bindings": _bindings(root), "scheduler": plan,
                "records": records, "bottom_up": bottom["bottom_up"], "passes": passes,
                "coarse_logic_graph": coarse_graph, "logic_graph": logic_graph}
    validate_with_schema(root, "coherence-registry.schema.json", registry)
    path = root / ".review/coherence_registry.json"
    write_json(path, registry)
    write_json(root / ".review/logic/structural_tree.json", {
        "units": inventory["units"], "source_snapshot": inventory["source_snapshot"],
        "edges": [{"source_id": u["parent_id"], "target_id": u["id"], "relation": "CONTAINS"}
                  for u in inventory["units"] if u["parent_id"]],
    })
    write_json(root / ".review/logic/section_contracts.json", {"records": [r for r in records if units[r["node_id"]]["level"] in {"PAPER", "SECTION"}]})
    write_json(root / ".review/logic/paragraph_roles.json", {"records": [r for r in records if units[r["node_id"]]["level"] == "PARAGRAPH"]})
    materialize_summary(root, registry)
    record_relation_issues(root, logic_graph, records)
    append_event(root, "coherence.solidified", {"run_id": run_id, "records": len(records)}, run_id=run_id,
                 artifact_refs=[{"path": ".review/coherence_registry.json"}])
    return registry


def trace(registry: dict, node_id: str | None = None) -> dict:
    if registry.get("status") != "CURRENT":
        return {"status": registry.get("status", "NOT_RUN"), "chains": []}
    units = {u["id"]: u for u in registry["inventory"]["units"]}
    records = {r["node_id"]: r for r in registry["records"]}
    bottom = {r["node_id"]: r for r in registry["bottom_up"]}
    routes = {r["node_id"]: r for r in registry["scheduler"]["routes"]}
    relations = registry["logic_graph"]["relations"]
    local_graphs = registry["logic_graph"]["local_graphs"]
    targets = [node_id] if node_id else list(records)
    chains = []
    for target in targets:
        if target not in units:
            raise HarnessError(f"Unknown coherence node: {target}")
        chain = []
        current = target
        seen = set()
        while current:
            if current in seen or current not in units:
                raise HarnessError("Broken coherence parent chain")
            seen.add(current)
            u = units[current]
            chain.append({"id": current, "level": u["level"], "location": u["location"],
                          "contract": records.get(current), "bottom_up": bottom.get(current),
                          "routing": routes.get(current),
                          "semantic_edges": [r for r in relations if current in {r["source_id"], r["target_id"]}],
                          "local_attachments": [n for g in local_graphs for n in g["mapped"]["nodes"] if n["node_id"] == current],
                          "coverage_challenges": [c for g in local_graphs for c in g["challenge"]["checks"] if c["node_id"] == current]})
            current = u["parent_id"]
        chains.append({"target_id": target, "chain": list(reversed(chain))})
    return {"status": registry["status"], "source_snapshot": registry["inventory"]["source_snapshot"], "chains": chains}


def gate_failures(root: Path) -> dict[str, list[str]]:
    from logic_graph import relation_failures, escalation_paragraphs
    failures: dict[str, list[str]] = {g: [] for g in ("G24", "G25", "G26", "G27", "G28")}
    try:
        registry = load_json(root / ".review/coherence_registry.json")
        validate_with_schema(root, "coherence-registry.schema.json", registry)
        if registry["status"] != "CURRENT":
            raise HarnessError("multiscale registry is not CURRENT")
        inventory = build_inventory(root)
        if inventory != registry["inventory"] or _bindings(root) != registry["bindings"]:
            raise HarnessError("multiscale source/contract snapshot is stale")
        if inventory["errors"]:
            raise HarnessError("source inventory contains unresolved extraction errors")
        mode = registry["mode"]
        state = load_json(root / ".review/state.json")
        request_id = state.get("active_request_id") or state.get("last_request_id")
        if request_id:
            request = load_json(root / f".review/requests/{request_id}.json")
            if request["granularity"]["level"] != mode:
                raise HarnessError("registry mode differs from request")
        base = _base_records(root, inventory, mode)
        if registry["records"][:len(base)] != base:
            raise HarnessError("source/macro/hierarchy bindings drift")
    except (HarnessError, KeyError, TypeError, ValueError) as exc:
        for errors in failures.values():
            errors.append(str(exc))
        return failures
    try:
        source_levels = {u["id"]: u["level"] for u in inventory["units"]}
        paragraphs = [r for r in registry["records"] if source_levels.get(r["node_id"]) == "PARAGRAPH"]
        plan = schedule(inventory, load_json(root / ".review/structure.json"), paragraphs,
                        load_json(root / ".review/claims.json")["claims"], mode, policy_from(load_config(root)),
                        escalation_paragraphs(registry["coarse_logic_graph"], inventory))
        if plan != registry["scheduler"]:
            failures["G26"].append("risk plan differs from deterministic recomputation")
        p_ids = [u["id"] for u in inventory["units"] if u["level"] == "PARAGRAPH"] if mode in DEEP_MODES else []
        expected = [r["node_id"] for r in base] + p_ids + plan["selected_sentence_ids"]
        _exact([r["node_id"] for r in registry["records"]], expected, "Multiscale coverage")
        expected_stages = (["PARAGRAPH"] if p_ids else []) + (["SENTENCE"] if plan["selected_sentence_ids"] else []) + ["BOTTOM_UP"]
        _exact([p["stage"] for p in registry["passes"]], expected_stages, "Pass coverage")
        accumulated = list(base)
        for stage in expected_stages:
            item = next(p for p in registry["passes"] if p["stage"] == stage)
            out = item["output"]
            targets = p_ids if stage == "PARAGRAPH" else plan["selected_sentence_ids"] if stage == "SENTENCE" else [r["node_id"] for r in accumulated if r["node_id"] not in plan["selected_sentence_ids"]]
            validate_records(root, out, stage, targets, inventory, accumulated)
            if stage != "BOTTOM_UP":
                accumulated.extend(out["records"])
            elif out["bottom_up"] != registry["bottom_up"]:
                failures["G27"].append("bottom-up output differs from its pass")
        if accumulated != registry["records"]:
            failures["G25"].append("registry records differ from validated passes")
        trace(registry)  # Validate every reviewed parent chain.
        core = {c["id"] for c in load_json(root / ".review/claims.json")["claims"] if c["centrality"] == "CORE"}
        for item in registry["passes"]:
            for issue in item["output"]["issues"]:
                if issue["severity"] == "BLOCKER" or (issue["severity"] == "MAJOR" and issue["claim_id"] in core):
                    failures["G27"].append(f"unresolved critical logic:{issue['id']}")
        failures["G28"].extend(relation_failures(root, registry))
        # The first-stage graph is retained as evidence for why finer review ran.
        coarse_view = {**registry, "records": [r for r in registry["records"] if r["node_id"] not in plan["selected_sentence_ids"]],
                       "logic_graph": registry["coarse_logic_graph"]}
        failures["G26"].extend(e for e in relation_failures(root, coarse_view)
                              if not e.startswith("critical relation requires re-review"))
    except (HarnessError, KeyError, TypeError, ValueError) as exc:
        failures["G25"].append(str(exc))
        failures["G27"].append(str(exc))
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect multiscale logical chains")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--node")
    parser.add_argument("--inventory", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    result = build_inventory(root) if args.inventory else trace(load_json(root / ".review/coherence_registry.json"), args.node)
    if not args.inventory:
        result["gate_failures"] = gate_failures(root)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
