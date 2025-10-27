# main.py
import io
import os
import json
import base64
import logging
from datetime import datetime, timedelta, timezone

# --- Library Imports ---
import numpy as np
import cv2  # Assuming this is used in your actual face_utils
import insightface  # Assuming this is used in your actual face_utils
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Path, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr
from typing import Optional, List, Dict, Any

from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
from bson import ObjectId
from bson.errors import InvalidId

from cryptography.fernet import Fernet
from passlib.context import CryptContext
from jose import JWTError, jwt

from fastapi.middleware.cors import CORSMiddleware
from routes.election_routes import router as election_router

# ==============================================================================
# SECTION 1: CONFIGURATION
# ==============================================================================
print("Loading Configuration...")

# --- Biometric Config ---
FACE_THRESHOLD = 0.5  # Example value
EMBED_DIM = 512       # Example value

# --- Security & JWT Config ---
# In production, use secure, environment-variable-based secrets
SECRET_KEY = os.getenv("SECRET_KEY", "a_very_secret_key_for_dev_only")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# --- Database Config ---
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB_NAME = os.getenv("MONGO_DB", "voting_system")
VOTERS_COLLECTION_NAME = "voters"
ADMINS_COLLECTION_NAME = "district_admins"

# --- Encryption Key for Biometrics ---
# In production: use secure key management (Vault/KMS) and never hardcode keys.
KEY_FILE = "data/secret.key"
os.makedirs("data", exist_ok=True)
if not os.path.exists(KEY_FILE):
    fernet_key = Fernet.generate_key()
    with open(KEY_FILE, "wb") as kf:
        kf.write(fernet_key)
else:
    with open(KEY_FILE, "rb") as kf:
        fernet_key = kf.read()
fernet = Fernet(fernet_key)

# ==============================================================================
# SECTION 2: SCHEMAS (Pydantic Models)
# ==============================================================================
print("Loading Schemas...")

# --- Biometric Schemas ---
class VoterIn(BaseModel):
    name: str
    father_name: Optional[str] = None
    dob: Optional[str] = None
    gender: Optional[str] = None
    address: Optional[str] = None
    epic: str

# --- Admin Schemas ---
class DistrictAdminBase(BaseModel):
    name: str
    email: EmailStr
    district: str
    phone_number: str

class DistrictAdminCreate(DistrictAdminBase):
    password: str

class DistrictAdminOut(DistrictAdminBase):
    id: str
    status: str
    class Config:
        from_attributes = True

class StatusUpdateRequest(BaseModel):
    status: str

# = a=============================================================================
# SECTION 3: SECURITY UTILS (Hashing, Tokens)
# ==============================================================================
print("Loading Security Utilities...")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

# ==============================================================================
# SECTION 4: DATABASE & STORAGE
# ==============================================================================
print("Connecting to Database...")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MongoConnector:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(MongoConnector, cls).__new__(cls)
            try:
                cls._instance.client = MongoClient(MONGO_URI)
                cls._instance.db = cls._instance.client[MONGO_DB_NAME]
                cls._instance.voters_collection = cls._instance.db[VOTERS_COLLECTION_NAME]
                cls._instance.admins_collection = cls._instance.db[ADMINS_COLLECTION_NAME]
                # Create unique indexes
                cls._instance.voters_collection.create_index("epic", unique=True)
                cls._instance.admins_collection.create_index("email", unique=True)
                cls._instance.client.server_info()
                logger.info(f"Connected to MongoDB: {MONGO_DB_NAME}")
            except Exception as e:
                logger.error(f"Failed to connect to MongoDB: {e}")
                raise
        return cls._instance

# --- Biometric Storage Functions ---
def save_voter(voter_id: str, voter_data: Dict[str, Any]) -> bool:
    db = MongoConnector().voters_collection
    try:
        voter_data["_id"] = voter_id
        db.insert_one(voter_data)
        return True
    except DuplicateKeyError:
        logger.warning(f"Voter with EPIC {voter_id} already exists.")
        return False

def get_voter(voter_id: str) -> Optional[Dict[str, Any]]:
    return MongoConnector().voters_collection.find_one({"_id": voter_id})

def list_voters() -> Dict[str, Dict[str, Any]]:
    voters = {}
    cursor = MongoConnector().voters_collection.find({})
    for voter in cursor:
        voter_id = voter.get("_id")
        voters[voter_id] = voter
    return voters

# --- Admin CRUD Functions ---
async def create_admin(admin: DistrictAdminCreate) -> Optional[DistrictAdminOut]:
    db = MongoConnector().admins_collection
    hashed_password = get_password_hash(admin.password)
    admin_data = admin.model_dump()
    admin_data["hashed_password"] = hashed_password
    admin_data["status"] = "pending"
    del admin_data["password"]
    try:
        result = db.insert_one(admin_data)
        created_admin = db.find_one({"_id": result.inserted_id})
        return DistrictAdminOut(id=str(created_admin["_id"]), **created_admin)
    except DuplicateKeyError:
        logger.error(f"Admin with email {admin.email} already exists.")
        return None

async def get_pending_admins() -> list[DistrictAdminOut]:
    db = MongoConnector().admins_collection
    admins = []
    for admin in db.find({"status": "pending"}):
        admins.append(DistrictAdminOut(id=str(admin["_id"]), **admin))
    return admins

async def update_admin_status(admin_id: str, new_status: str) -> Optional[DistrictAdminOut]:
    db = MongoConnector().admins_collection
    if new_status not in ["approved", "rejected"]:
        raise ValueError("Status must be 'approved' or 'rejected'")
    
    result = db.find_one_and_update(
        {"_id": ObjectId(admin_id), "status": "pending"},
        {"$set": {"status": new_status}},
        return_document=True
    )
    if result:
        return DistrictAdminOut(id=str(result["_id"]), **result)
    return None

async def login_admin(email: str, password: str):
    db = MongoConnector().admins_collection
    admin = db.find_one({"email": email})
    if not admin:
        return None, "Admin not found."
    if admin["status"] != "approved":
        return None, "Admin account is not approved."
    if not verify_password(password, admin["hashed_password"]):
        return None, "Incorrect password."
    return admin, None

# ==============================================================================
# SECTION 5: FACE UTILITIES
# ==============================================================================
print("Loading Face Utilities...")
# In a real app, this would be more complex. We'll use mock functions.
# You should replace these with your actual 'face_utils.py' content.
# For demonstration, I'll provide working placeholder functions.
face_analysis_app = insightface.app.FaceAnalysis(providers=['CPUExecutionProvider'])
face_analysis_app.prepare(ctx_id=0, det_size=(640, 640))

def read_imagefile_bytes(file_bytes: bytes) -> np.ndarray:
    """Reads image from bytes and converts to RGB numpy array."""
    nparr = np.frombuffer(file_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    return img_rgb

def get_face_embedding(img: np.ndarray) -> (Optional[np.ndarray], Optional[dict]):
    """Extracts face embedding from a single image."""
    faces = face_analysis_app.get(img)
    if not faces:
        return None, None
    # Use the largest face
    largest_face = sorted(faces, key=lambda x: (x.bbox[2] - x.bbox[0]) * (x.bbox[3] - x.bbox[1]))[-1]
    embedding = largest_face.normed_embedding.astype(np.float32)
    meta = {'box': largest_face.bbox.tolist(), 'kps': largest_face.kps.tolist(), 'det_score': float(largest_face.det_score)}
    return embedding, meta

def is_face_live_from_frames(frames: List[np.ndarray]) -> bool:
    """Placeholder for liveness detection logic."""
    if len(frames) < 2: return False
    # A simple mock liveness check: Ensure nose keypoint moves between frames
    _, meta1 = get_face_embedding(frames[0])
    _, meta2 = get_face_embedding(frames[-1])
    if meta1 and meta2:
        nose1 = meta1['kps'][2]
        nose2 = meta2['kps'][2]
        dist = np.sqrt((nose1[0] - nose2[0])**2 + (nose1[1] - nose2[1])**2)
        # Check if nose moved more than a few pixels (e.g., 5)
        return dist > 5.0
    return False

def verify_embeddings(emb1: np.ndarray, emb2: np.ndarray, threshold: float) -> (bool, float):
    """Verifies if two embeddings are from the same person."""
    # Cosine similarity
    score = np.dot(emb1, emb2) / (np.linalg.norm(emb1) * np.linalg.norm(emb2))
    return score >= threshold, float(score)

# ==============================================================================
# SECTION 6: FASTAPI APPLICATION AND ENDPOINTS
# ==============================================================================
print("Initializing FastAPI App...")
app = FastAPI(title="TRUVOX - Integrated Biometric and Admin API")

origins = [
    "http://localhost:3000",  # For Create React App
    "http://localhost:5173",  # For Vite
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Biometric Endpoints ---

app.include_router(election_router)

@app.post("/enroll", tags=["Biometric Voting"])
async def enroll(
    name: str = Form(...),
    father_name: Optional[str] = Form(None),
    dob: Optional[str] = Form(None),
    gender: Optional[str] = Form(None),
    address: Optional[str] = Form(None),
    epic: str = Form(...),
    photo: UploadFile = File(...)
):
    if get_voter(epic) is not None:
        raise HTTPException(status_code=400, detail=f"Voter with EPIC {epic} already exists.")

    b = await photo.read()
    img = read_imagefile_bytes(b)
    emb, meta = get_face_embedding(img)
    if emb is None:
        raise HTTPException(status_code=400, detail="No face detected in enrollment photo.")

    all_current_voters = list_voters()
    for vid, vdata in all_current_voters.items():
        if "biometrics" in vdata and "embedding_enc" in vdata["biometrics"]:
            emb_enc = base64.b64decode(vdata["biometrics"]["embedding_enc"])
            emb_bytes = fernet.decrypt(emb_enc)
            emb_stored = np.frombuffer(emb_bytes, dtype=np.float32)
            match, _ = verify_embeddings(emb, emb_stored, threshold=FACE_THRESHOLD)
            if match:
                raise HTTPException(status_code=400, detail=f"Face already exists (similar to EPIC {vid}).")

    emb_enc = fernet.encrypt(emb.tobytes())
    voter_record = {
        "name": name, "father_name": father_name, "dob": dob, "gender": gender,
        "address": address, "epic": epic,
        "biometrics": {
            "embedding_enc": base64.b64encode(emb_enc).decode("utf-8"),
            "model": "insightface_arcface_r50", "meta": meta
        }
    }
    if save_voter(epic, voter_record):
        return {"status": "ok", "voter_id": epic, "message": "Enrolled successfully."}
    else:
        raise HTTPException(status_code=500, detail="Failed to save voter.")


# In your main.py file

@app.post("/verify", tags=["Biometric Voting"])
async def verify(
    epic: str = Form(...),
    photo: Optional[UploadFile] = File(None),
    frames: Optional[List[UploadFile]] = File(None)
):
    """
    Verification endpoint.
    Accepts EPIC (voter id) and either:
      - photo: one image (single frame) or
      - frames: list of image files (for active-liveness)
    Returns similarity score, liveness result and verified boolean.
    """
    voter = get_voter(epic)
    if not voter or "biometrics" not in voter:
        raise HTTPException(status_code=404, detail="Voter or biometric data not found.")

    # decrypt stored embedding
    emb_enc = base64.b64decode(voter["biometrics"]["embedding_enc"])
    emb_bytes = fernet.decrypt(emb_enc)
    emb_stored = np.frombuffer(emb_bytes, dtype=np.float32)

    # prepare frames
    if not frames and not photo:
        raise HTTPException(status_code=400, detail="No image(s) provided for verification.")
        
    frame_imgs = []
    if frames:
        for f in frames:
            frame_imgs.append(read_imagefile_bytes(await f.read()))
    elif photo:
        frame_imgs.append(read_imagefile_bytes(await photo.read()))

    # take first frame to compute embedding for quick compare
    emb_live, _ = get_face_embedding(frame_imgs[0])
    if emb_live is None:
        raise HTTPException(status_code=400, detail="No face detected in provided image.")

    # compare embeddings
    match, score = verify_embeddings(emb_live, emb_stored, threshold=FACE_THRESHOLD)

    # liveness check (only meaningful if multiple frames provided)
    liveness_ok = is_face_live_from_frames(frame_imgs) if len(frame_imgs) >= 2 else False

    # decision policy: require both match and liveness
    verified = match and liveness_ok

    # For demonstration, also support fallback
    fallback_used = match and not liveness_ok

    # Build response - THE FIX IS HERE!
    # Explicitly convert all NumPy types (numpy.bool_, numpy.float32) to
    # standard Python types (bool, float) before returning.
    resp = {
        "status": "ok",
        "epic": epic,
        "match_score": float(score),          # <-- CONVERTED
        "match": bool(match),                # <-- CONVERTED
        "liveness": bool(liveness_ok),        # <-- CONVERTED
        "verified": bool(verified),          # <-- CONVERTED
        "fallback_used_or_pending": bool(fallback_used), # <-- CONVERTED
        "note": "liveness requires multiple frames; send multiple frames for robust check."
    }

    return resp


@app.get("/voters", tags=["Biometric Voting"])
async def get_all_voters():
    return list_voters()


@app.get("/health", tags=["Biometric Voting"])
async def health_check():
    return {"status": "healthy", "database": "MongoDB"}


# --- Admin Endpoints ---

@app.post("/district-admin", response_model=DistrictAdminOut, tags=["District Admin"])
async def add_district_admin(admin: DistrictAdminCreate):
    created = await create_admin(admin)
    if not created:
        raise HTTPException(status_code=400, detail="Could not create user. Email may already exist.")
    return created


@app.get("/district-admins/pending", response_model=list[DistrictAdminOut], tags=["District Admin"])
async def list_pending_admins():
    admins = await get_pending_admins()
    return admins


@app.patch("/district-admin/{admin_id}/status", response_model=DistrictAdminOut, tags=["District Admin"])
async def patch_status(admin_id: str, status_update: StatusUpdateRequest):
    try:
        updated_admin = await update_admin_status(admin_id, status_update.status)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except InvalidId:
        raise HTTPException(status_code=400, detail="Invalid admin ID format.")

    if not updated_admin:
        raise HTTPException(status_code=404, detail="Admin not found or not in 'pending' state.")
    return updated_admin


@app.post("/district-admin/login", tags=["District Admin"])
async def admin_login(email: str = Form(...), password: str = Form(...)):
    admin, error = await login_admin(email, password)
    if error:
        raise HTTPException(status_code=401, detail=error)
    token = create_access_token({"sub": str(admin["_id"]), "email": email})
    return {"access_token": token, "token_type": "bearer"}


# --- General Endpoints ---

@app.get("/", tags=["Root"])
def read_root():
    return {"message": "Welcome to the TRUVOX Integrated API"}


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    # Provide a path to a real favicon or return a 204 No Content response
    # return FileResponse("path/to/favicon.ico")
    from fastapi.responses import Response
    return Response(status_code=204)
