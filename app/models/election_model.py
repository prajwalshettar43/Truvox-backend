from pydantic import BaseModel, Field
from typing import List
from datetime import date

class Candidate(BaseModel):
    name: str
    party: str
    symbol_url: str

class Election(BaseModel):
    election_type: str = Field(..., example="State Assembly")
    state: str = Field(..., example="Karnataka")
    district: str = Field(..., example="Hubballi")
    election_date: date
    candidates: List[Candidate]
