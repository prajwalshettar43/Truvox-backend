import asyncio
from database import district_admins_collection
from security import hash_password

async def hash_existing_passwords():
    cursor = district_admins_collection.find({})
    async for admin in cursor:
        # Skip if password already looks hashed (starts with $2b$ for bcrypt)
        if admin.get("password") and not admin["password"].startswith("$2b$"):
            hashed = hash_password(admin["password"])
            await district_admins_collection.update_one(
                {"_id": admin["_id"]},
                {"$set": {"password": hashed}}
            )
            print(f"Hashed password for admin {admin.get('email')}")

asyncio.run(hash_existing_passwords())
