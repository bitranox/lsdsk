"""Presentation adapters.

Turns domain objects and findings into Rich renderables.  Decides nothing about
the hardware: by the time anything here runs, the diagnosis is already made.
"""

from .report import render_findings, render_header, render_tree, render_verdict
from .tree import render_fabric

__all__ = ["render_fabric", "render_findings", "render_header", "render_tree", "render_verdict"]
