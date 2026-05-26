"""
app/schemas/chat.py
====================
Pydantic request/response models for the streaming chat endpoint.
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """A single turn in a multi-turn conversation."""

    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=8000)


class ChatRequest(BaseModel):
    """POST /api/chat — request body."""

    species_id: uuid.UUID
    message: str = Field(..., min_length=1, max_length=4000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=40)
