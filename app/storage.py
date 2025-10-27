# app/storage.py
import json
import os
from typing import Optional, Dict, Any
from config import DUMMY_DB_PATH

# Make sure data folder exists
os.makedirs(os.path.dirname(DUMMY_DB_PATH), exist_ok=True)
if not os.path.exists(DUMMY_DB_PATH):
    with open(DUMMY_DB_PATH, "w") as f:
        json.dump({"voters": {}}, f, indent=2)


def _read_db() -> Dict[str, Any]:
    """
    Read the dummy DB file safely.
    If file is empty or corrupted, auto-reset to {"voters": {}}.
    """
    try:
        with open(DUMMY_DB_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        # Auto-reset if DB file is empty or invalid
        reset_data = {"voters": {}}
        _write_db(reset_data)
        return reset_data


def _write_db(data: Dict[str, Any]):
    with open(DUMMY_DB_PATH, "w") as f:
        json.dump(data, f, indent=2)


def save_voter(voter_id: str, voter_record: Dict[str, Any]) -> None:
    """
    Save a voter record into dummy DB.
    Replace this with a MongoDB insert/update when integrating.
    """
    db = _read_db()
    db["voters"][voter_id] = voter_record
    _write_db(db)


def get_voter(voter_id: str) -> Optional[Dict[str, Any]]:
    """
    Get a voter record by voter_id. Replace with DB query in final integration.
    """
    db = _read_db()
    return db["voters"].get(voter_id)


def list_voters() -> Dict[str, Dict]:
    return _read_db()["voters"]
