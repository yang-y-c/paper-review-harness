# Natural-language intake

Use this path when the user supplies a new paper task.

1. Inspect `.review/config.json`, `.review/state.json`, and the referenced manuscript paths. Do not edit the paper during intake.
2. Preserve the user's wording as the source request. For a short request, call:

   `python scripts/control.py intake --text "<request>" --session-id <session> --turn-id <turn>`

   For multiline or shell-sensitive text, put the exact text in a repository-local temporary intake file using `apply_patch`, call `--text-file`, record the resulting request ID, then remove only that temporary file.
3. The intake coordinator selects one executable intent and produces objectives, scope, constraints, granularity, assumptions, missing information, and success criteria under `request-draft.schema.json`. The controller adds IDs, timestamps, source hashes, confirmation state, and validates `request.schema.json`.
4. For `REVIEW`, `OPTIMIZE`, or `FULL`, disclose the default: "会检查全文、全部章节和段落，再对高风险段落恢复完整句子逻辑图，重点验证关键推理句。" Set `level=ADAPTIVE`, `explicit=false`, and record the default in `normalization_notes`. Do not add a missing-information question just because no depth was named. Honor an explicit coverage limit or full-sentence request; ask about a budget or exclusion only when it materially changes this baseline.
5. Summarize: intent; included/excluded scope; must-have objectives; granularity; allowed/forbidden changes; preservation constraints; assumptions; missing information.
6. If material information is missing, ask one concise question and do not start. If the intent can edit, ask the user to confirm that summary. A read-only request can proceed without a second confirmation when it has no material unknowns.

Do not fabricate a venue, Claim ID, Issue ID, evidence source, or permission. Empty scope arrays mean the repository-configured manuscript scope, not an unknown scope.
