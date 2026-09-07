# Natural-language intake

Use this path when the user supplies a new paper task.

1. Inspect `.review/config.json`, `.review/state.json`, and the referenced manuscript paths. Do not edit the paper during intake.
2. Preserve the user's wording as the source request. For a short request, call:

   `python scripts/control.py intake --text "<request>" --session-id <session> --turn-id <turn>`

   For multiline or shell-sensitive text, put the exact text in a repository-local temporary intake file using `apply_patch`, call `--text-file`, record the resulting request ID, then remove only that temporary file.
3. The intake coordinator selects one executable intent and produces objectives, scope, constraints, granularity, assumptions, missing information, and success criteria under `request-draft.schema.json`. The controller adds IDs, timestamps, source hashes, confirmation state, and validates `request.schema.json`.
4. For `REVIEW`, `OPTIMIZE`, or `FULL`, require an explicit granularity choice. If absent, ask: "这次希望审查到哪一层：只看全篇宏观结构、章节、小节、逐段、逐句，还是自适应？自适应会逐段检查全文，只对标题、摘要、结论、核心论断、关键衔接和高风险段落做逐句检查。" Put the unanswered question in `missing_information`; do not admit the request yet.
5. Summarize: intent; included/excluded scope; must-have objectives; granularity; allowed/forbidden changes; preservation constraints; assumptions; missing information.
6. If material information is missing, ask one concise question and do not start. If the intent can edit, ask the user to confirm that summary. A read-only request can proceed without a second confirmation when it has no material unknowns.

Do not fabricate a venue, Claim ID, Issue ID, evidence source, or permission. Empty scope arrays mean the repository-configured manuscript scope, not an unknown scope.
