# Operations

Use the contract-aware entrypoint for new `REVIEW`, `OPTIMIZE`, and `FULL` tasks.

- Normalize a layered task: `python scripts/review_contract.py intake --text "..."`
- Show the user-facing contract: `python scripts/review_contract.py show REQ-ID`
- Confirm a proposed/edit-capable contract after explicit user approval: `python scripts/review_contract.py confirm REQ-ID`
- Dry-run the confirmed plan: `python scripts/review_contract.py start REQ-ID --dry-run`
- Execute it: `python scripts/review_contract.py start REQ-ID`

The contract runner derives the internal logical depth from the confirmed dimensions and assurance profile. `STAGED_REVISION` runs a structural checkpoint before expensive detail review. `BATCH_AFTER_FULL_REVIEW` preserves the older full-pass-then-edit strategy.

Use the legacy controller for observation and compatibility operations:

- View state: `python scripts/control.py status`
- Inspect multiscale logic: `python scripts/control.py logic`
- Trace one logical node: `python scripts/control.py logic --node NODE-ID`
- List requests: `python scripts/control.py list`
- Derive the next safe action for legacy requests: `python scripts/control.py guide --request REQ-ID`
- Verify the provenance log: `python scripts/control.py verify-log`

Do not use legacy `control.py start` to bypass an unconfirmed `SKILL_PROPOSED` review contract. For a material change to dimensions, assurance, revision strategy, or budget, normalize a new request so the old contract remains immutable provenance.
