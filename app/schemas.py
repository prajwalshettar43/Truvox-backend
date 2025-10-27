from pydantic import BaseModel, EmailStr, Field, constr

class DistrictAdminBase(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6)
    districtName: str = Field(..., min_length=3)
    status: str = Field(default="pending")

class DistrictAdminCreate(DistrictAdminBase):
    pass

class DistrictAdminOut(DistrictAdminBase):
    id: str  # this will store MongoDB _id as string

class StatusUpdateRequest(BaseModel):
    status: constr(pattern="^(approve|reject)$")
