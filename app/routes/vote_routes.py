from fastapi import APIRouter, HTTPException
from models.vote_model import Vote
from database.connection import election_collection,voter_collection
from bson import ObjectId
import subprocess
import re

vote_router = APIRouter(prefix="/vote", tags=["Vote"])

# ------------------------------
# ✅ CAST VOTE API (Embedded)
# ------------------------------
CHANNEL_NAME = "evidencechannel"
CHAINCODE_NAME = "basic_1"


def execute_blockchain_command(epic_id: str, candidate_name: str) -> str:
    """
    Executes peer chaincode invoke command and returns transaction ID.
    Stores epic_id + candidate_name on blockchain.
    """
    # Construct data to be stored on blockchain (can be customized)
    vote_key = f"{{{{{{{epic_id}|||{candidate_name}}}}}}}"

    # Build the command
    cmd = f"""peer chaincode invoke -o orderer.example.com:7050 \
        --tls true --cafile $ORDERER_CA \
        -C {CHANNEL_NAME} -n {CHAINCODE_NAME} \
        --peerAddresses localhost:7051 \
        --tlsRootCertFiles $PEER0_ORG1_CA \
        -c '{{"Args":["AddEvidence","{vote_key}"]}}'"""

    try:
        # Run command
        result = subprocess.run(
            cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        # Combine outputs for inspection
        output = (result.stdout or result.stderr).strip()
        print("Blockchain invoke output:", output)

        # Extract transaction ID
        import re
        match = re.search(r"Transaction ID:\s*([a-f0-9]+)", output, re.I)
        if match:
            txn_id = match.group(1)
            print("Extracted Transaction ID:", txn_id)
            return txn_id
        else:
            raise Exception("Transaction ID not found in blockchain response")

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Blockchain error: {str(e)}")


@vote_router.post("/cast")
def cast_vote(vote: Vote):
    """
    Casts a vote and records the transaction on blockchain.
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

    # ✅ Call blockchain logic
    txn_id = execute_blockchain_command(vote.epic_id, vote.candidate_name)

    # ✅ Create vote entry
    vote_data = {
        "epic_id": vote.epic_id,
        "candidate": vote.candidate_name,
        "transaction_id": txn_id,
    }

    # ✅ Save in MongoDB
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
    """
    Checks if a voter (EPIC ID) is eligible to vote and has already voted.
    Includes constituency validation.
    """
    try:
        election_obj_id = ObjectId(election_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid election ID format.")

    # Fetch election (include its constituency)
    election = election_collection.find_one(
        {"_id": election_obj_id}, 
        {"votes": 1, "constituency": 1}
    )
    if not election:
        raise HTTPException(status_code=404, detail="Election not found.")

    # Fetch voter constituency details
    voter = voter_collection.find_one(
        {"_id": epic_id}
    )
    if not voter:
        raise HTTPException(status_code=404, detail="Voter not found.")

    voter_k = voter.get("karnatakaConstituencies")
    voter_p = voter.get("parliamentaryConstituencies")
    election_const = election.get("constituency")

    # --------------------------
    # NEW: Constituency Check
    # --------------------------
    if election_const not in [voter_k, voter_p]:
        raise HTTPException(
            status_code=403,
            detail=f"Voter does not belong to the constituency of this election. "
                   f"Election constituency: {election_const}"
        )

    # --------------------------
    # Check if voter already voted
    # --------------------------
    for v in election.get("votes", []):
        if v["epic_id"] == epic_id:
            return {
                "status": "already_voted",
                "details": {
                    "epic_id": v["epic_id"],
                    "candidate": v["candidate"],
                    "txn": v.get("txn")
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
def extract_pattern_from_blockchain(raw_data: bytes) -> str:
    """
    Robust extraction of pattern {{{EPIC|||CANDIDATE}}} from raw binary data returned
    by peer chaincode query (qscc or your chaincode). Uses find() instead of only regex,
    so small binary/control bytes nearby won't prevent extraction.
    Returns a string "EPIC-CANDIDATE" or raises Exception if not found.
    """
    # 1) decode safely (latin-1 preserves byte values 1:1)
    text = raw_data.decode("latin-1", errors="ignore")

    # 2) try simple string-based extraction (most robust)
    start_token = "{{{"
    end_token = "}}}"
    start_idx = text.find(start_token)
    if start_idx != -1:
        end_idx = text.find(end_token, start_idx + len(start_token))
        if end_idx != -1:
            inner = text[start_idx + len(start_token) : end_idx]
            # inner expected format: EPIC|||CANDIDATE
            if "|||" in inner:
                epic, candidate = inner.split("|||", 1)
                return f"{epic.strip()}-{candidate.strip()}"
            # if no |||, still return inner as-is (fallback)
            return inner.strip()

    # 3) fallback: permissive regex that allows escaped braces or noise between braces
    #    This catches cases like \"{{{...}}} or some control bytes between braces
    regex = re.compile(r"\\?\{\{\{\s*([^\|]{1,}?)\s*\|\|\|\s*(.*?)\s*\}\}\}", re.DOTALL)
    m = regex.search(text)
    if m:
        epic = m.group(1).strip()
        candidate = m.group(2).strip()
        return f"{epic}-{candidate}"

    # 4) last resort: try to find any occurrence of triple-braces in the 'strings' of the output
    #    extract all printable substrings length>=4 and check for pattern
    printable_chunks = re.findall(r"[ -~]{4,}", text)  # printable ascii sequences
    for chunk in printable_chunks:
        if "{{{" in chunk and "}}}" in chunk and "|||" in chunk:
            s = chunk
            s = s[s.find("{{{") + 3 : s.find("}}}")]
            if "|||" in s:
                epic, candidate = s.split("|||", 1)
                return f"{epic.strip()}-{candidate.strip()}"

    # If none matched, log the first ~400 chars for debugging and raise
    snippet = text[:400].replace("\n", "\\n")
    logger.debug(f"Blockchain raw snippet (first 400 chars): {snippet!r}")
    raise Exception("Pattern not found in blockchain data")

def query_blockchain_transaction(txn_id: str) -> str:
    """
    Queries Hyperledger Fabric blockchain for a transaction by ID
    and extracts EPIC-CANDIDATE info from its data.
    """
    try:
        result = subprocess.run(
            [
                "peer", "chaincode", "query",
                "-C", "evidencechannel",
                "-n", "qscc",
                "-c", f'{{"Args":["GetTransactionByID","{CHANNEL_NAME}","{txn_id}"]}}'
            ],
            capture_output=True,
            text=False  # keep as bytes
        )
        if result.returncode != 0:
            raise Exception(result.stderr.decode())
            
        # Extract data pattern
        key = extract_pattern_from_blockchain(result.stdout)
        if not key:
            raise Exception("Pattern not found in blockchain data")

        return key

    except Exception as e:
        raise Exception(f"Blockchain query error for {txn_id}: {e}")


# 🧩 Main endpoint
@vote_router.post("/verify-election-integrity")
def verify_election_integrity(data: dict):
    """
    Verifies all votes in a given election against blockchain data.
    If mismatches are found, updates MongoDB to match blockchain.
    
    Request body:
    {
      "election_id": "<MongoDB ObjectId>"
    }
    """
    election_id = data.get("election_id")
    if not election_id:
        raise HTTPException(status_code=400, detail="Missing election_id.")

    try:
        election_obj_id = ObjectId(election_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid election ID format.")

    # ✅ Fetch election document
    election = election_collection.find_one({"_id": election_obj_id})
    if not election:
        raise HTTPException(status_code=404, detail="Election not found.")

    votes = election.get("votes", [])
    if not votes:
        return {"message": "No votes found in this election."}

    corrected = []
    verified = []

    # ✅ Verify each vote against blockchain
    for vote in votes:
        epic_id = vote.get("epic_id")
        candidate = vote.get("candidate")
        txn = vote.get("transaction_id")

        if not txn:
            print(f"⚠️ Skipping vote without transaction ID: {epic_id}")
            continue

        try:
            on_chain_key = query_blockchain_transaction(txn)

            if not on_chain_key or "-" not in on_chain_key:
                print(f"⚠️ Could not extract valid data for txn {txn}")
                continue

            on_chain_epic, on_chain_candidate = on_chain_key.split("-", 1)

            if on_chain_epic != epic_id or on_chain_candidate.strip() != candidate.strip():
                print(f"❌ Mismatch for {txn}: DB({epic_id}-{candidate}) vs BC({on_chain_key})")

                # Update MongoDB to match blockchain data
                election_collection.update_one(
                    {"_id": election_obj_id, "votes.transaction_id": txn},
                    {
                        "$set": {
                            "votes.$.epic_id": on_chain_epic.strip(),
                            "votes.$.candidate": on_chain_candidate.strip()
                        }
                    }
                )

                corrected.append({
                    "transaction_id": txn,
                    "old": f"{epic_id}-{candidate}",
                    "new": on_chain_key
                })
            else:
                verified.append({
                    "transaction_id": txn,
                    "epic_id": epic_id,
                    "candidate": candidate
                })

        except Exception as e:
            print(f"⚠️ Error verifying txn {txn}: {e}")
            continue

    return {
        "status": "completed",
        "verified_count": len(verified),
        "corrected_count": len(corrected),
        "corrected": corrected,
        "verified": verified
    }