"""Compatibility shim for `muschat.errors` during Phase 0 Expand."""

# shortcut: compatibility shim for unmigrated callers; remove in Phase 0 Contract (Ticket 03)
from muschat.errors import error_message as mensagem_de_erro

__all__ = ["mensagem_de_erro"]
