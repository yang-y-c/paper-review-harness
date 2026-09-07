# Operate and guide

Use the controller from the paper repository root.

- Confirm an edit-capable request: `python scripts/control.py confirm REQ-ID`
- Evaluate hard admission: `python scripts/control.py admit REQ-ID`
- Preview an admitted request: `python scripts/control.py start REQ-ID --dry-run`
- Start it: `python scripts/control.py start REQ-ID`
- View the current state: `python scripts/control.py status`
- Inspect all multiscale chains: `python scripts/control.py logic`
- Trace a specific source unit: `python scripts/control.py logic --node NODE-ID`
- List requests: `python scripts/control.py list`
- Derive the next safe action: `python scripts/control.py guide --request REQ-ID`

Admission checks schema validity, executable intent, manuscript presence, success criteria, missing inputs, edit confirmation and permission, anti-fabrication policy, conflicts, agents/schemas, bounded rounds, the adaptive baseline or explicit coverage override, `$humanizer`, multiscale extraction/policy readiness and compilation configuration.

When guiding, use actual status and failed gate details. Do not tell the user to rerun blindly. If the user requests a material change to scope or constraints, normalize a new request so the original remains immutable evidence of the earlier instruction.
