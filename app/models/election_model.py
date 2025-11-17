from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import date

class Candidate(BaseModel):
    name: str
    party: str
    symbol: str
    symbol_url: Optional[str] = None  # Make optional if needed
    candidate_photo_base64: Optional[str] = None 

class Election(BaseModel):
    election_type: str = Field(..., example="State Assembly")
    state: str = Field(..., example="Karnataka")
    district: str = Field(..., example="Hubballi")
    election_date: date
    candidates: List[Candidate]
    constituency: str = Field(..., example="Hiriyur")
