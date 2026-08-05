from typing import List, Optional
from pydantic import BaseModel, Field

class CanonicalExtractionDTO(BaseModel):
    schema_version: int = Field(default=1, description="Schema version of the DTO")
    statement: str = Field(..., description="The raw statement or name of the entity extracted from the text.")
    verb: Optional[str] = Field(None, description="The primary action verb associated with the statement, if any.")
    owner: Optional[str] = Field(None, description="The owner, assignee, or responsible party for this item.")
    due_date: Optional[str] = Field(None, description="The deadline or date associated with this item (YYYY-MM-DD or descriptive text).")
    blocks: List[str] = Field(default_factory=list, description="List of milestone names or deliverables that this item is blocking or delaying.")
    confidence: float = Field(default=1.0, description="Confidence score from the extraction.")
    source_document_id: Optional[int] = Field(None, description="The ID of the document from which this was extracted.")
    source_sentence: Optional[str] = Field(None, description="The exact sentence from the source document.")
