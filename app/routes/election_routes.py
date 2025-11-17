import os
import base64
import datetime
from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder
from typing import Tuple
from pathlib import Path
from models.election_model import Election, Candidate
from database.connection import election_collection
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