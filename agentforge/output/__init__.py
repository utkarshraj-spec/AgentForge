"""Stage 6 — final output delivery."""

from agentforge.output.audit_report import write_audit_report
from agentforge.output.final_assembler import assemble

__all__ = ["assemble", "write_audit_report"]
