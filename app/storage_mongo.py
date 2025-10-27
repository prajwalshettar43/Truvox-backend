# storage_mongo.py
import os
from typing import Optional, Dict, Any
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# MongoDB configuration from environment variables
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "voting_system")
COLLECTION_NAME = "voters"

class MongoStorage:
    def __init__(self):
        """Initialize MongoDB connection"""
        try:
            self.client = MongoClient(MONGO_URI)
            self.db = self.client[MONGO_DB]
            self.collection = self.db[COLLECTION_NAME]
            
            # Create unique index on EPIC to prevent duplicates
            self.collection.create_index("epic", unique=True)
            
            # Test connection
            self.client.server_info()
            logger.info(f"Connected to MongoDB at {MONGO_URI}, database: {MONGO_DB}")
        except Exception as e:
            logger.error(f"Failed to connect to MongoDB: {e}")
            raise

    def save_voter(self, voter_id: str, voter_data: Dict[str, Any]) -> bool:
        """
        Save voter record to MongoDB
        
        Args:
            voter_id: The EPIC number (unique identifier)
            voter_data: Dictionary containing voter information
            
        Returns:
            True if saved successfully, False otherwise
        """
        try:
            # Add the voter_id as _id for MongoDB
            voter_data["_id"] = voter_id
            voter_data["voter_id"] = voter_id
            
            # Insert the document
            result = self.collection.insert_one(voter_data)
            logger.info(f"Voter {voter_id} saved successfully")
            return result.acknowledged
            
        except DuplicateKeyError:
            logger.warning(f"Voter {voter_id} already exists")
            return False
        except Exception as e:
            logger.error(f"Error saving voter {voter_id}: {e}")
            return False

    def get_voter(self, voter_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve voter record from MongoDB
        
        Args:
            voter_id: The EPIC number
            
        Returns:
            Voter data dictionary if found, None otherwise
        """
        try:
            voter = self.collection.find_one({"_id": voter_id})
            if voter:
                logger.info(f"Voter {voter_id} retrieved successfully")
                # Remove MongoDB's _id field from response if needed
                # voter.pop("_id", None)
            return voter
        except Exception as e:
            logger.error(f"Error retrieving voter {voter_id}: {e}")
            return None

    def list_voters(self) -> Dict[str, Dict[str, Any]]:
        """
        List all voters in the database
        
        Returns:
            Dictionary of voter_id -> voter_data
        """
        try:
            voters = {}
            cursor = self.collection.find({})
            for voter in cursor:
                voter_id = voter.get("voter_id") or voter.get("_id")
                voters[voter_id] = voter
            logger.info(f"Retrieved {len(voters)} voters")
            return voters
        except Exception as e:
            logger.error(f"Error listing voters: {e}")
            return {}

    def update_voter(self, voter_id: str, update_data: Dict[str, Any]) -> bool:
        """
        Update voter record in MongoDB
        
        Args:
            voter_id: The EPIC number
            update_data: Dictionary containing fields to update
            
        Returns:
            True if updated successfully, False otherwise
        """
        try:
            result = self.collection.update_one(
                {"_id": voter_id},
                {"$set": update_data}
            )
            if result.modified_count > 0:
                logger.info(f"Voter {voter_id} updated successfully")
                return True
            return False
        except Exception as e:
            logger.error(f"Error updating voter {voter_id}: {e}")
            return False

    def delete_voter(self, voter_id: str) -> bool:
        """
        Delete voter record from MongoDB
        
        Args:
            voter_id: The EPIC number
            
        Returns:
            True if deleted successfully, False otherwise
        """
        try:
            result = self.collection.delete_one({"_id": voter_id})
            if result.deleted_count > 0:
                logger.info(f"Voter {voter_id} deleted successfully")
                return True
            return False
        except Exception as e:
            logger.error(f"Error deleting voter {voter_id}: {e}")
            return False

    def check_duplicate_epic(self, epic: str) -> bool:
        """
        Check if EPIC already exists in database
        
        Args:
            epic: The EPIC number to check
            
        Returns:
            True if exists, False otherwise
        """
        try:
            exists = self.collection.find_one({"epic": epic}) is not None
            return exists
        except Exception as e:
            logger.error(f"Error checking duplicate EPIC {epic}: {e}")
            return False

    def close(self):
        """Close MongoDB connection"""
        try:
            self.client.close()
            logger.info("MongoDB connection closed")
        except Exception as e:
            logger.error(f"Error closing MongoDB connection: {e}")

# Create singleton instance
storage = MongoStorage()

# Export convenience functions that match the original interface
def save_voter(voter_id: str, voter_data: Dict[str, Any]) -> bool:
    return storage.save_voter(voter_id, voter_data)

def get_voter(voter_id: str) -> Optional[Dict[str, Any]]:
    return storage.get_voter(voter_id)

def list_voters() -> Dict[str, Dict[str, Any]]:
    return storage.list_voters()

def update_voter(voter_id: str, update_data: Dict[str, Any]) -> bool:
    return storage.update_voter(voter_id, update_data)

def delete_voter(voter_id: str) -> bool:
    return storage.delete_voter(voter_id)

def check_duplicate_epic(epic: str) -> bool:
    return storage.check_duplicate_epic(epic)