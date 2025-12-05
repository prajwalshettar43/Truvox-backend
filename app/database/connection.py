from pymongo import MongoClient
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../.env"))

MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = os.getenv("MONGO_DB")

if not MONGO_URI:
    raise ValueError("❌ MONGO_URI not found. Check your .env file location.")
if not DB_NAME:
    raise ValueError("❌ MONGO_DB not found. Check your .env file location.")

client = MongoClient(MONGO_URI)
db = client[DB_NAME]

election_collection = db["elections"]
vote_collection = db["votes"]
voter_collection = db["voters"]
log_collection = db["logs"]