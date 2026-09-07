# Monitoring

For a one-shot status snapshot, run `python scripts/control.py status` or `python scripts/control.py monitor --once`. Report phase, active request/run, Claim/Issue counts, agent invocation states, latest event, validation state, and blocker.

For an active foreground watch, run `python scripts/control.py monitor --interval 5 --timeout 300`. Keep intervals between 0 and 60 seconds. Stop when the workflow becomes inactive, reaches the timeout, or the user interrupts.

If the user explicitly asks to keep monitoring later or periodically, use the product's heartbeat automation mechanism. Keep quiet while state is unchanged; notify only on completion, failure, a meaningful phase/gate change, or required author input. Do not create a recurring automation merely because the user asked for a current status.
