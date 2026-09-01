from pydantic import BaseModel, EmailStr, ConfigDict, Field
from datetime import datetime


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(..., max_length=72)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    email: str
    created_at: datetime


class Token(BaseModel):
    access_token: str
    token_type: str


class MessageCreate(BaseModel):
    body: str


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    user_id: int
    body: str
    created_at: datetime
    updated_at: datetime


class FriendshipCreate(BaseModel):
    user_id: int


class FriendshipResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    user_id: int
    friend_id: int
    created_at: datetime


class LikeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    message_id: int
    user_id: int
    created_at: datetime
