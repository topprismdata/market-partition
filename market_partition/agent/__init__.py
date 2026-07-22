"""Agent layer — LLM-driven partition loop with self-verifying tools.

This module provides self-describing tools that report their own health
diagnostics, enabling an LLM agent to run a plan→act→judge loop without a
human in the loop. Pattern inspired by GISclaw / LLM-Geo (self-verifying GIS).

See tools.py for the tool implementations and the module docstring there
for the agent-loop contract.
"""

from .tools import (
    ToolResult,
    fetch_barrier,
    run_partition,
    reconstruct_ring,
    check_landmarks,
    render_result,
    visual_check,
)

__all__ = [
    "ToolResult",
    "fetch_barrier",
    "run_partition",
    "reconstruct_ring",
    "check_landmarks",
    "render_result",
    "visual_check",
]
