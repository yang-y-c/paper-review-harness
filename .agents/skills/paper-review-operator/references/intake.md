# Natural-language intake

Use this path when the user supplies a new layered paper-review task.

1. Inspect `.review/config.json`, `.review/state.json`, and the referenced manuscript paths. Do not edit the paper during intake.
2. Preserve the user's wording as the source request. For a short layered request, call:

   `python scripts/review_contract.py intake --text "<request>" --session-id <session> --turn-id <turn>`

   For multiline or shell-sensitive text, put the exact text in a repository-local temporary intake file, call `--text-file`, record the resulting request ID, then remove only that temporary file.
3. The intake coordinator produces objectives, scope, constraints, and a `review_contract`. The contract separates: review dimensions, assurance level, revision strategy, and budget posture. The controller adds IDs, timestamps, source hashes and confirmation state.
4. The academic core is always present: global structure, Claims, section logic, terminology, notation/definitions, and data consistency. These are persistent/slow state and should not be silently disabled to save tokens. Optional checks include paragraph logic, sentence logic, redundancy, language, citations, and scientific validity.
5. If the user's wording already fixes the contract, encode `selection_source=USER_EXPLICIT`. Otherwise propose one compact contract with `selection_source=SKILL_PROPOSED` and require confirmation. Do not silently choose STRICT/MAX_QUALITY or any other profile.
6. Present the proposal in plain language. The minimum summary is: enabled optional checks; FAST/STANDARD/STRICT assurance; revision strategy; ECONOMY/BALANCED/MAX_QUALITY budget; any explicit exclusions or token cap. Do not expose internal DAG/challenger/verifier switches unless the user asks.
7. If the proposed contract is acceptable, run `python scripts/review_contract.py confirm REQ-ID`. Only after confirmation should `python scripts/review_contract.py start REQ-ID` be used. A SKILL_PROPOSED read-only review also requires contract confirmation because the user, not the skill, chooses the reliability/cost tradeoff.
8. Use `missing_information` only for genuinely material scientific/scope unknowns. Missing internal implementation preferences are handled by the proposal-and-confirmation contract flow.

Do not fabricate a venue, Claim ID, Issue ID, evidence source, budget, or edit permission. Empty scope arrays mean the repository-configured manuscript scope, not an unknown scope.
