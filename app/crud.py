from database import district_admins_collection
from schemas import DistrictAdminCreate
from bson import ObjectId
from security import hash_password, verify_password

# Create a new admin with hashed password
async def create_admin(data: DistrictAdminCreate):
    admin = data.dict()
    admin["password"] = hash_password(admin["password"])
    if "status" not in admin:
        admin["status"] = "pending"
    result = await district_admins_collection.insert_one(admin)
    created_admin = await district_admins_collection.find_one({"_id": result.inserted_id})
    created_admin["id"] = str(created_admin["_id"])
    created_admin.pop("_id")
    return created_admin

# Get all pending admins
async def get_pending_admins():
    cursor = district_admins_collection.find({"status": "pending"})
    admins = []
    async for admin in cursor:
        admin["id"] = str(admin["_id"])
        admin.pop("_id")
        admins.append(admin)
    return admins

# Update admin status
async def update_admin_status(admin_id: str, new_status: str):
    if new_status not in ["approve", "reject"]:
        raise ValueError("Invalid status. Must be 'approve' or 'reject'.")
    result = await district_admins_collection.update_one(
        {"_id": ObjectId(admin_id), "status": "pending"},
        {"$set": {"status": new_status}}
    )
    if result.modified_count == 0:
        return None
    updated_admin = await district_admins_collection.find_one({"_id": ObjectId(admin_id)})
    updated_admin["id"] = str(updated_admin["_id"])
    updated_admin.pop("_id")
    return updated_admin

# Login admin
async def login_admin(email: str, password: str):
    admin = await district_admins_collection.find_one({"email": email})
    if not admin:
        return None, "Invalid email or password"

    if admin.get("status") != "approve":
        return None, "Admin not approved"

    if not verify_password(password, admin["password"]):
        return None, "Invalid email or password"

    return admin, None
