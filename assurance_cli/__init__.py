"""assurance-cli — folder checks anyone can run without I-Ops.

Shared filesystem I/O (`paths`, `profile`, `gather`) lives in this package so `assurance-mcp` can
import it instead of duplicating the profiler. MCP depends on this package for gathering only; the
CLI does not depend on MCP. Two copies of the profiler is how they disagree.
"""

__version__ = "0.1.0"
