import os
import base64
import datetime
from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder
from typing import Tuple
from pathlib import Path
from models.election_model import Election, Candidate
from database.connection import election_collection, voter_collection, vote_collection
from bson import ObjectId

router = APIRouter(prefix="/election", tags=["Election"])

UPLOAD_DIR = Path("./uploads/candidate_photos")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

def save_base64_image(base64_str: str, prefix: str = "candidate") -> Tuple[str,str]:
    """
    Save a base64 image string to disk.
    - base64_str: may be raw base64 or a data URL (data:image/jpeg;base64,...)
    Returns: (filename, filepath)
    """
    if not base64_str:
        raise ValueError("empty base64 string")

    s = base64_str.strip()
    # if data URL present, strip header
    if s.startswith("data:"):
        comma = s.find(",")
        if comma != -1:
            s = s[comma+1 : ]

    # sanitize whitespace/newlines
    s = "".join(s.split())

    # decode to bytes
    try:
        data = base64.b64decode(s)
    except Exception as e:
        raise ValueError(f"invalid base64: {e}")

    # choose extension heuristically (optional; here assume jpeg)
    # you can try to sniff bytes' magic numbers if you need exact ext
    filename = f"{prefix}_{int(datetime.datetime.utcnow().timestamp()*1000)}.jpg"
    filepath = UPLOAD_DIR / filename
    with open(filepath, "wb") as f:
        f.write(data)

    return filename, str(filepath.resolve())


@router.post("/create")
def create_election(election: Election):
    try:
        # Convert Pydantic model to JSON-serializable dict
        data = jsonable_encoder(election)

        # Convert election_date to datetime for storage (same logic as you had)
        ed = election.election_date
        if isinstance(ed, datetime.date) and not isinstance(ed, datetime.datetime):
            data["election_date"] = datetime.datetime.combine(ed, datetime.time.min)
        else:
            try:
                if isinstance(data.get("election_date"), str):
                    data["election_date"] = datetime.datetime.fromisoformat(data["election_date"])
            except Exception:
                pass

        # Process candidate images (if provided)
        processed_candidates = []
        for idx, cand in enumerate(data.get("candidates", [])):
            # cand is a dict with keys: name, party, symbol_url, candidate_photo_base64?
            photo_base64 = cand.pop("candidate_photo_base64", None)
            # create a copy we will insert
            cand_record = dict(cand)

            if photo_base64:
                try:
                    filename, filepath = save_base64_image(photo_base64, prefix=f"cand{idx}")
                    # store path or URL in candidate record (choose your preference)
                    cand_record["photo_path"] = filepath  # local file path
                    # OR cand_record["photo_url"] = f"/static/uploads/{filename}"    # if you serve static files
                except ValueError as e:
                    # If you prefer to reject invalid image data, raise 422:
                    raise HTTPException(status_code=422, detail=f"Invalid candidate image for {cand_record.get('name')}: {e}")

            processed_candidates.append(cand_record)

        # Replace the candidates list with processed list
        data["candidates"] = processed_candidates

        result = election_collection.insert_one(data)
        return {"message": "Election created successfully!", "election_id": str(result.inserted_id)}
    except HTTPException:
        # re-raise HTTP exceptions so they reach client
        raise
    except Exception as e:
        print("Error creating election:", e)
        raise HTTPException(status_code=500, detail="Internal Server Error")
        
@router.get("/all")
def get_all_elections():
    try:
        elections = list(election_collection.find())

        for e in elections:
            e["_id"] = str(e["_id"])

            # normalize election_date
            ed = e.get("election_date")
            if isinstance(ed, datetime.datetime):
                e["election_date"] = ed.date().isoformat()
            elif isinstance(ed, datetime.date):
                e["election_date"] = ed.isoformat()

            # Process candidates
            candidates = e.get("candidates", [])
            for cand in candidates:
                if not isinstance(cand, dict):
                    continue

                # Check for photo_path and convert to accessible URL
                photo_path = cand.get("photo_path")
                if photo_path and isinstance(photo_path, str):
                    filename = os.path.basename(photo_path)
                    # Create URL that matches the static mount
                    cand["photo_url"] = f"http://localhost:8000/uploads/candidate_photos/{filename}"
                    # Remove internal path
                    cand.pop("photo_path", None)
                
                # Ensure symbol_url exists
                if "symbol_url" not in cand and "symbol" in cand:
                    cand["symbol_url"] = cand.get("symbol")

            e["candidates"] = candidates

        return {"elections": elections}
    except Exception as exc:
        print("Error fetching elections:", exc)
        raise HTTPException(status_code=500, detail="Internal Server Error")
        
@router.get("/report/{election_id}")
def get_election_report(election_id: str):
    try:
        # 1️⃣ Fetch election
        try:
            oid = ObjectId(election_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid election_id format")

        election = election_collection.find_one({"_id": oid})
        if not election:
            raise HTTPException(status_code=404, detail="Election not found")

        election_type = election.get("election_type")
        constituency = election.get("constituency")
        candidates = election.get("candidates", [])
        election_date = election.get("election_date")

        # 🔹 Convert photo_path -> photo_url (like /all route)
        BASE_URL = "http://localhost:8000"
        normalized_candidates = []
        for cand in candidates:
            if not isinstance(cand, dict):
                continue

            cand_copy = dict(cand)

            photo_path = cand_copy.get("photo_path")
            if photo_path and isinstance(photo_path, str):
                filename = os.path.basename(photo_path)
                cand_copy["photo_url"] = f"{BASE_URL}/uploads/candidate_photos/{filename}"
                cand_copy.pop("photo_path", None)

            # optional: ensure symbol_url exists
            if "symbol_url" not in cand_copy and cand_copy.get("symbol"):
                cand_copy["symbol_url"] = cand_copy["symbol"]

            normalized_candidates.append(cand_copy)

        candidates = normalized_candidates

        # 2️⃣ Decide which constituency field to use for voters
        if election_type == "MLA Election":
            constituency_field = "karnatakaConstituencies"
        elif election_type == "MP Election":
            constituency_field = "parliamentaryConstituencies"
        else:
            constituency_field = "karnatakaConstituencies"

        # 3️⃣ Eligible voters (same constituency)
        eligible_voters = list(
            voter_collection.find({constituency_field: constituency})
        )
        total_eligible = len(eligible_voters)

        # 4️⃣ Gender stats for eligible voters
        gender_eligible = {"male": 0, "female": 0, "other": 0}
        for v in eligible_voters:
            g = (v.get("gender") or "").strip().lower()
            if g == "male":
                gender_eligible["male"] += 1
            elif g == "female":
                gender_eligible["female"] += 1
            else:
                gender_eligible["other"] += 1

        # 5️⃣ Age distribution helper
        def compute_age_distribution(voters_list):
            age_buckets = {"18-25": 0, "26-40": 0, "41-60": 0, "60+": 0}
            today = datetime.date.today()
            for v in voters_list:
                dob_str = v.get("dob")
                if not dob_str:
                    continue
                try:
                    dob = datetime.date.fromisoformat(dob_str)
                except Exception:
                    continue
                age = (
                    today.year
                    - dob.year
                    - ((today.month, today.day) < (dob.month, dob.day))
                )
                if age < 18:
                    continue
                if age <= 25:
                    age_buckets["18-25"] += 1
                elif age <= 40:
                    age_buckets["26-40"] += 1
                elif age <= 60:
                    age_buckets["41-60"] += 1
                else:
                    age_buckets["60+"] += 1
            return age_buckets

        age_eligible = compute_age_distribution(eligible_voters)

        # 6️⃣ Fetch votes for this election
        # ⬅️ FIX: use embedded election["votes"] first
        embedded_votes = election.get("votes", [])
        if isinstance(embedded_votes, list) and embedded_votes:
            votes = embedded_votes
        else:
            # optional fallback if you ALSO store them in a separate collection
            election_id_str = str(election["_id"])
            votes = list(
                vote_collection.find(
                    {
                        "$or": [
                            {"election_id": election_id_str},
                            {"electionId": election_id_str},
                        ]
                    }
                )
            )

        total_votes = len(votes)

        # 7️⃣ Map candidate -> vote count
        candidate_results = []
        for c in candidates:
            cname = c.get("name")
            vote_count = 0
            for v in votes:
                # ⬅️ FIX: your field is "candidate" in embedded votes
                vcname = (
                    v.get("candidate")
                    or v.get("candidate_name")
                    or v.get("candidateName")
                )
                if vcname == cname:
                    vote_count += 1

            percentage = (
                round((vote_count / total_votes) * 100, 2)
                if total_votes > 0
                else 0.0
            )

            candidate_results.append(
                {
                    "name": cname,
                    "party": c.get("party"),
                    "symbol": c.get("symbol"),
                    "symbol_url": c.get("symbol_url"),
                    "photo_url": c.get("photo_url"),
                    "votes": vote_count,
                    "vote_percentage": percentage,
                }
            )

        candidate_results.sort(key=lambda x: x["votes"], reverse=True)
        winner = candidate_results[0] if candidate_results else None

        # 8️⃣ Voters who actually voted (for gender/age of voted subset)
        # ⬅️ FIX: your field is "epic_id" in embedded votes
        voter_keys = []
        for v in votes:
            epic = v.get("epic_id") or v.get("voter_id") or v.get("epic")
            if epic:
                voter_keys.append(epic)

        voter_keys = list(set(voter_keys))  # dedupe

        voted_voters = []
        if voter_keys:
            voted_voters = list(
                voter_collection.find(
                    {
                        "$or": [
                            {"_id": {"$in": voter_keys}},   # when _id is epic_id
                            {"epic": {"$in": voter_keys}},  # backup if epic stored separately
                        ]
                    }
                )
            )

        gender_voted = {"male": 0, "female": 0, "other": 0}
        for v in voted_voters:
            g = (v.get("gender") or "").strip().lower()
            if g == "male":
                gender_voted["male"] += 1
            elif g == "female":
                gender_voted["female"] += 1
            else:
                gender_voted["other"] += 1

        age_voted = compute_age_distribution(voted_voters)

        # 9️⃣ Build report response
        turnout_percentage = (
            round((total_votes / total_eligible) * 100, 2)
            if total_eligible > 0
            else 0.0
        )

        report = {
            "election_details": {
                "election_id": str(election["_id"]),
                "election_type": election_type,
                "state": election.get("state"),
                "district": election.get("district"),
                "constituency": constituency,
                "election_date": (
                    election_date.date().isoformat()
                    if isinstance(election_date, datetime.datetime)
                    else election_date.isoformat()
                    if isinstance(election_date, datetime.date)
                    else election_date
                ),
                "total_candidates": len(candidates),
            },
            "voter_statistics": {
                "total_eligible_voters": total_eligible,
                "total_voted": total_votes,
                "turnout_percentage": turnout_percentage,
                "eligible_gender_distribution": gender_eligible,
                "eligible_age_distribution": age_eligible,
                "voted_gender_distribution": gender_voted,
                "voted_age_distribution": age_voted,
                "total_female_voters_eligible": gender_eligible["female"],
                "total_female_voters_voted": gender_voted["female"],
            },
            "candidates_summary": candidate_results,
            "winner": winner,
            "eligible_candidates": candidates,
        }

        return {"success": True, "report": report}

    except HTTPException:
        raise
    except Exception as e:
        print("Error generating election report:", e)
        raise HTTPException(status_code=500, detail="Internal Server Error")
