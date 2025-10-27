# app/config.py
# Central place for thresholds and constants

# Cosine similarity threshold for ArcFace embeddings (tune on validation set)
FACE_THRESHOLD = 0.40   # start ~0.35-0.45

# Liveness threshold (nose movement range in pixels, depends on camera/resolution)
# If nose_x moves by this many pixels across frames, treat as live head-movement
LIVENESS_NOSE_MOVE_PX = 8

# Embedding dimension (InsightFace typical 512)
EMBED_DIM = 512

# Dummy DB path (JSON) - replace with your MongoDB or backend endpoints
DUMMY_DB_PATH = "data/dummy_db.json"
