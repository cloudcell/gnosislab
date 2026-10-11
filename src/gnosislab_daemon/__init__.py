"""gnosislab_daemon — persistent non-LLM supervisor for agent Loops.

One daemon per state root. Each Loop drives an existing CLI agent and
an independent checker until the check passes, a limit parks it, or an
operator stops it. The daemon never trusts the agent's own claim of
completion — the checker's exit code is the only verdict.
"""

__version__ = "0.1.0"
