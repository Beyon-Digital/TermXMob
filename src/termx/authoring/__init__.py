"""One-shot, non-persisting drafting of agent, bot, skill and character definitions.

A draft is a suggestion for a form the person still reviews and saves. Nothing here
creates, writes or runs anything, and a draft can never carry more authority than
the existing creation paths would grant by default.
"""
from termx.authoring.drafts import KINDS, DraftError, draft_definition, pick_provider

__all__ = ["KINDS", "DraftError", "draft_definition", "pick_provider"]
