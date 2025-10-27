# app/face_utils.py
import cv2
import numpy as np
from insightface.app import FaceAnalysis
from typing import List, Tuple
from .config import FACE_THRESHOLD, LIVENESS_NOSE_MOVE_PX, EMBED_DIM

# Initialize InsightFace app once (auto-downloads models on first run)
# This approach should work with most InsightFace versions
import os

# Set InsightFace root directory
os.environ.setdefault('INSIGHTFACE_ROOT', os.path.expanduser('~/.insightface'))

# Try different initialization approaches based on version compatibility
face_app = None

try:
    # Method 1: Try with name parameter (newer versions)
    face_app = FaceAnalysis(name='buffalo_l')
    face_app.prepare(ctx_id=0, det_size=(640, 640))
    print("Loaded InsightFace with buffalo_l model")
except Exception as e1:
    print(f"Method 1 failed: {e1}")
    try:
        # Method 2: Try with providers parameter (some versions)
        face_app = FaceAnalysis(providers=['CPUExecutionProvider'])
        face_app.prepare(ctx_id=0, det_size=(640, 640))
        print("Loaded InsightFace with providers parameter")
    except Exception as e2:
        print(f"Method 2 failed: {e2}")
        try:
            # Method 3: Try with minimal parameters (older versions)
            face_app = FaceAnalysis()
            face_app.prepare(ctx_id=0, det_size=(640, 640))
            print("Loaded InsightFace with minimal parameters")
        except Exception as e3:
            print(f"Method 3 failed: {e3}")
            # Method 4: Last resort - lazy initialization
            print("Will initialize FaceAnalysis on first use")
            face_app = None

def get_face_app():
    """Lazy initialization of face app with proper path detection"""
    global face_app
    if face_app is None:
        import os
        
        # Common InsightFace model paths to check
        possible_paths = [
            os.path.expanduser('~/.insightface'),
            os.path.expanduser('~/.insightface/models'),
            os.path.expanduser('~/insightface'),
            os.path.expanduser('~/insightface/models'),
            '/home/slayer/.insightface',
            '/home/slayer/.insightface/models',
            './models',
            './insightface',
            os.path.join(os.getcwd(), 'models'),
        ]
        
        print("Searching for InsightFace models...")
        for path in possible_paths:
            if os.path.exists(path):
                print(f"Found models directory: {path}")
                # List contents to see what's there
                try:
                    contents = os.listdir(path)
                    print(f"Contents: {contents}")
                except:
                    pass
        
        try:
            # Method 1: Try with explicit root path
            model_root = os.path.expanduser('~/.insightface')
            print(f"Trying with model root: {model_root}")
            if os.path.exists(model_root):
                face_app = FaceAnalysis(name='buffalo_l', root=model_root)
                face_app.prepare(ctx_id=0, det_size=(640, 640))
                print("Successfully initialized with explicit root path")
                return face_app
        except Exception as e1:
            print(f"Method 1 failed: {e1}")
        
        try:
            # Method 2: Set environment variable and try
            os.environ['INSIGHTFACE_ROOT'] = os.path.expanduser('~/.insightface')
            face_app = FaceAnalysis(name='buffalo_l')
            face_app.prepare(ctx_id=0, det_size=(640, 640))
            print("Successfully initialized with environment variable")
            return face_app
        except Exception as e2:
            print(f"Method 2 failed: {e2}")
            
        try:
            # Method 3: Try different model names with root
            for model_name in ['buffalo_l', 'buffalo_m', 'buffalo_s']:
                try:
                    face_app = FaceAnalysis(name=model_name, root=os.path.expanduser('~/.insightface'))
                    face_app.prepare(ctx_id=0, det_size=(640, 640))
                    print(f"Successfully initialized with {model_name}")
                    return face_app
                except:
                    continue
        except Exception as e3:
            print(f"Method 3 failed: {e3}")
        
        try:
            # Method 4: Check if models are in current directory or project directory
            current_dir = os.getcwd()
            project_models = os.path.join(current_dir, 'models')
            if os.path.exists(project_models):
                face_app = FaceAnalysis(name='buffalo_l', root=project_models)
                face_app.prepare(ctx_id=0, det_size=(640, 640))
                print("Successfully initialized with project models directory")
                return face_app
        except Exception as e4:
            print(f"Method 4 failed: {e4}")
        
        raise RuntimeError("Could not initialize InsightFace. Please check model paths and installation.")
    
    return face_app


def read_imagefile_bytes(file_bytes: bytes) -> np.ndarray:
    """
    Converts uploaded image bytes to an OpenCV BGR image
    """
    arr = np.frombuffer(file_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    return img


def get_face_embedding(img: np.ndarray) -> Tuple[np.ndarray, dict]:
    """
    Detects first face in image, returns embedding (numpy array) and metadata dict.
    If no face detected, returns (None, None).
    """
    app = get_face_app()
    faces = app.get(img)
    if not faces or len(faces) == 0:
        return None, None

    # Use the first detected face
    face = faces[0]
    emb = face.embedding  # numpy array shape (512,)
    # get a small metadata snapshot
    meta = {
        "det_score": float(face.det_score),
        # if face.landmark exists it may include keypoints (x,y)
        "kps": face.kps.tolist() if hasattr(face, "kps") else None
    }
    return emb, meta


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """
    Compute cosine similarity between two 1-D numpy arrays.
    """
    if a is None or b is None:
        return -1.0
    a = a.astype(np.float32)
    b = b.astype(np.float32)
    num = np.dot(a, b)
    den = np.linalg.norm(a) * np.linalg.norm(b)
    if den == 0:
        return -1.0
    return float(num / den)


def is_face_live_from_frames(frames: List[np.ndarray]) -> bool:
    """
    Simple active-liveness based on nose X movement across frames.
    Strategy:
        - For each frame, detect face and extract nose x from face.kps (InsightFace gives 5 keypoints: left eye, right eye, nose, left mouth, right mouth)
        - Compute the range (max_x - min_x). If > threshold we assume the user moved head (active), hence live.
    Notes:
        - This is a pragmatic and simple approach; it's not a full anti-spoof but works for a basic active-liveness check.
        - For production use, consider hardware-based liveness or trained anti-spoof models.
    """
    app = get_face_app()
    nose_x_positions = []
    for frame in frames:
        faces = app.get(frame)
        if not faces:
            continue
        f = faces[0]
        # Access keypoints: InsightFace returns 'kps' with shape (5,2) typically
        if hasattr(f, "kps") and f.kps is not None:
            try:
                # nose is typically index 2 in 5 keypoints: [left_eye, right_eye, nose, left_mouth, right_mouth]
                nose = f.kps[2]  # [x, y]
                nose_x_positions.append(float(nose[0]))
            except Exception:
                continue

    if not nose_x_positions:
        return False

    # calculate movement range
    move_range = max(nose_x_positions) - min(nose_x_positions)
    # print("Liveness nose move range:", move_range)
    return move_range >= LIVENESS_NOSE_MOVE_PX


def verify_embeddings(emb_live: np.ndarray, emb_stored: np.ndarray, threshold: float = FACE_THRESHOLD) -> Tuple[bool, float]:
    """
    Compare embeddings using cosine similarity and threshold.
    Returns (is_match, score)
    """
    score = cosine_similarity(emb_live, emb_stored)
    is_match = score >= threshold
    return is_match, score