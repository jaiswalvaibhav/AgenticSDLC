# Engineering agent (out of scope — stub only)

Not implemented. Reserved for a future agent that works `technical_design_solution` and
`data_solution_development` steps. It would implement `AgentRuntime`-compatible task(s) the
same way `analyst/tasks.py` does, registered against the corresponding `config/workflow.yaml`
step ids, so the orchestrator needs no changes to call it.
