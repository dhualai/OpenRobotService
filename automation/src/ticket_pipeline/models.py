"""Models for the ticket-driven automation pipeline."""

from datetime import datetime
from typing import Any, Dict, List

from pydantic import BaseModel, Field


class TicketCandidate(BaseModel):
    """A production ticket that may be converted into candidate test cases."""

    id: int
    title: str
    description: str
    task_type: str
    status: str
    project_id: str
    project_name: str
    tags: List[str] = Field(default_factory=list)
    metadata_info: Dict[str, Any] = Field(default_factory=dict)
    created_by: str = ""
    source: str = ""
    created_at: datetime | None = None
