from pydantic import BaseModel

class Vote(BaseModel):
    election_id: str
    epic_id: str
    candidate_name: str
    transaction_id: str = None  # future use for blockchain
