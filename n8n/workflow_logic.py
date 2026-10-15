"""Reference steps for the n8n workflow. The backend inline mode calls the same rules."""

from app.webhooks.dispatch import apply_workflow

__all__ = ["apply_workflow"]
