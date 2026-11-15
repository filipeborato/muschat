"""Compatibility shim for `muschat.security` during Phase 0 Expand."""

# shortcut: compatibility shim for unmigrated callers; remove in Phase 0 Contract (Ticket 03)
from muschat.security import sanitize as neutralizar

__all__ = ["neutralizar"]
