"""Guards the Lambda's dependency footprint: importing it must never pull in the
heavy analyst-engine deps (bs4/strands/weasyprint/graphviz) — those only belong on
AgentCore, not in the scheduled Lambda. See wiring.py's lazy LocalAgentRuntime import.
"""
import subprocess
import sys


def test_importing_orchestrator_lambda_stays_free_of_heavy_deps():
    script = (
        "import sys; import sdlc.aws.orchestrator_lambda; "
        "heavy = [m for m in sys.modules if any(h in m for h in "
        "('bs4', 'strands', 'weasyprint', 'graphviz'))]; "
        "print(','.join(heavy))"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == ""
