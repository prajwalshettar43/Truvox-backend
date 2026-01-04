from fastapi import APIRouter, HTTPException
from models.vote_model import Vote
# Ensure you add vote_ledger to your database connection file
from database.connection import election_collection, voter_collection, vote_ledger 
from bson import ObjectId
import hashlib
import uuid
from datetime import datetime

vote_router = APIRouter(prefix="/vote", tags=["Vote"])

# ------------------------------
# ✅ BLOCKCHAIN SIMULATION HELPERS
# ------------------------------

def calculate_hash(data: str, prev_hash: str) -> str:
    """Generates a SHA-256 hash to simulate blockchain linking."""
    raw_string = f"{data}{prev_hash}"
    return hashlib.sha256(raw_string.encode()).hexdigest()

def add_to_ledger(epic_id: str, candidate_name: str, election_id: str) -> str:
    """
    Acts as the 'Smart Contract'. 
    Adds a new block to the vote_ledger collection with a hash link to the previous vote.
    Returns the Transaction ID.
    """
    txn_id = str(uuid.uuid4())
    
    # 1. Get the last block to chain hashes (Blockchain behavior)
    last_block = vote_ledger.find_one(sort=[("_id", -1)])
    prev_hash = last_block["current_hash"] if last_block else "GENESIS_BLOCK"

    # 2. Create data payload
    vote_data_string = f"{epic_id}|{candidate_name}|{election_id}|{txn_id}"
    
    # 3. Calculate Hash
    current_hash = calculate_hash(vote_data_string, prev_hash)

    # 4. Create Ledger Entry (Immutable Source of Truth)
    ledger_entry = {
        "transaction_id": txn_id,
        "epic_id": epic_id,
        "candidate": candidate_name,
        "election_id": election_id,
        "previous_hash": prev_hash,
        "current_hash": current_hash,
        "timestamp": datetime.utcnow()
    }

    # 5. Insert into Ledger Database
    vote_ledger.insert_one(ledger_entry)
    
    print(f"🔗 Block added to Ledger: {txn_id}")
    return txn_id

def get_vote_from_ledger(txn_id: str) -> dict:
    """
    Fetches the immutable record from the ledger database.
    """
    record = vote_ledger.find_one({"transaction_id": txn_id})
    if not record:
        raise Exception("Transaction not found in Ledger")
    return record


# ------------------------------
# ✅ CAST VOTE API
# ------------------------------
@vote_router.post("/cast")
def cast_vote(vote: Vote):
    """
    Casts a vote and records the transaction on the Shadow Ledger (Blockchain).
    """
    # ✅ Validate election ID
    try:
        election_obj_id = ObjectId(vote.election_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid election ID format.")

    # ✅ Fetch election
    election = election_collection.find_one({"_id": election_obj_id})
    if not election:
        raise HTTPException(status_code=404, detail="Election not found.")

    # ✅ Verify candidate
    candidate_names = [c["name"] for c in election.get("candidates", [])]
    if vote.candidate_name not in candidate_names:
        raise HTTPException(status_code=404, detail="Candidate not found in election.")

    # ✅ Prevent double voting
    for existing_vote in election.get("votes", []):
        if existing_vote["epic_id"] == vote.epic_id:
            raise HTTPException(status_code=400, detail="Voter has already voted.")

    # ✅ 1. WRITE TO LEDGER (The "Blockchain")
    # We write here first. If this fails, the vote doesn't count.
    try:
        txn_id = add_to_ledger(vote.epic_id, vote.candidate_name, vote.election_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ledger Write Error: {str(e)}")

    # ✅ 2. UPDATE REAL DATABASE (The Display DB)
    vote_data = {
        "epic_id": vote.epic_id,
        "candidate": vote.candidate_name,
        "transaction_id": txn_id,
    }

    election_collection.update_one(
        {"_id": election_obj_id},
        {"$push": {"votes": vote_data}}
    )

    return {
        "message": "Vote cast successfully!",
        "candidate": vote.candidate_name,
        "transaction_id": txn_id
    }

# ------------------------------
# ✅ CHECK IF USER HAS ALREADY VOTED
# ------------------------------
@vote_router.get("/check/{election_id}/{epic_id}") 
def check_vote(election_id: str, epic_id: str):
    try:
        election_obj_id = ObjectId(election_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid election ID format.")

    election = election_collection.find_one(
        {"_id": election_obj_id}, 
        {"votes": 1, "constituency": 1}
    )
    if not election:
        raise HTTPException(status_code=404, detail="Election not found.")

    voter = voter_collection.find_one({"_id": epic_id})
    if not voter:
        raise HTTPException(status_code=404, detail="Voter not found.")

    voter_k = voter.get("karnatakaConstituencies")
    voter_p = voter.get("parliamentaryConstituencies")
    election_const = election.get("constituency")

    if election_const not in [voter_k, voter_p]:
        raise HTTPException(
            status_code=403,
            detail=f"Voter does not belong to election constituency: {election_const}"
        )

    for v in election.get("votes", []):
        if v["epic_id"] == epic_id:
            return {
                "status": "already_voted",
                "details": {
                    "epic_id": v["epic_id"],
                    "candidate": v["candidate"],
                    "txn": v.get("transaction_id")
                }
            }

    return {"status": "not_voted", "message": "Voter can proceed to vote."}


@vote_router.get("/results/{election_id}")
def get_results(election_id: str):
    try:
        election_obj_id = ObjectId(election_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid election ID format.")

    pipeline = [
        {"$match": {"_id": election_obj_id}},
        {"$unwind": "$votes"},
        {"$group": {"_id": "$votes.candidate", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}}
    ]

    results = list(election_collection.aggregate(pipeline))
    return {"results": results}

@vote_router.post("/verify-election-integrity")
def verify_election_integrity(data: dict):
    """
    Verifies votes against the Shadow Ledger.
    Returns the EXACT output format as the original Blockchain code.
    """
    election_id = data.get("election_id")
    if not election_id:
        raise HTTPException(status_code=400, detail="Missing election_id.")

    try:
        election_obj_id = ObjectId(election_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid election ID format.")

    # 1. Fetch the mutable election data
    election = election_collection.find_one({"_id": election_obj_id})
    if not election:
        raise HTTPException(status_code=404, detail="Election not found.")

    votes = election.get("votes", [])
    
    # These lists match your original structure exactly
    corrected = []
    verified = []

    print(f"🔍 Starting Integrity Check for Election: {election_id}")

    for vote in votes:
        db_epic = vote.get("epic_id")
        db_candidate = vote.get("candidate")
        txn_id = vote.get("transaction_id")

        if not txn_id:
            continue

        try:
            # 2. Query the 'Shadow Ledger' (Truth Source)
            ledger_record = vote_ledger.find_one({"transaction_id": txn_id})
            
            if not ledger_record:
                print(f"⚠️ Txn {txn_id} not found in ledger")
                continue

            ledger_epic = ledger_record.get("epic_id")
            ledger_candidate = ledger_record.get("candidate")

            # 3. Compare (DB vs Ledger)
            # We format strings like 'EPIC-CANDIDATE' to match your original output style
            db_string = f"{db_epic}-{db_candidate}"
            ledger_string = f"{ledger_epic}-{ledger_candidate}"

            if db_string != ledger_string:
                print(f"❌ Mismatch: DB({db_string}) vs Ledger({ledger_string})")

                # 4. Auto-Correct Real Database
                election_collection.update_one(
                    {"_id": election_obj_id, "votes.transaction_id": txn_id},
                    {
                        "$set": {
                            "votes.$.epic_id": ledger_epic,
                            "votes.$.candidate": ledger_candidate
                        }
                    }
                )

                # ✅ MATCHING ORIGINAL OUTPUT STRUCTURE
                corrected.append({
                    "transaction_id": txn_id,
                    "old": db_string,       # e.g., "ABC12345-Hacker"
                    "new": ledger_string    # e.g., "ABC12345-RealCandidate"
                })
            else:
                # ✅ MATCHING ORIGINAL OUTPUT STRUCTURE
                verified.append({
                    "transaction_id": txn_id,
                    "epic_id": db_epic,
                    "candidate": db_candidate
                })

        except Exception as e:
            print(f"⚠️ Error verifying txn {txn_id}: {e}")
            continue

    # ✅ RETURN EXACTLY AS ORIGINAL
    return {
        "status": "completed",
        "verified_count": len(verified),
        "corrected_count": len(corrected),
        "corrected": corrected,
        "verified": verified
    }