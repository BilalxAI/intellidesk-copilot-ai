"""
Pydantic models for request/response validation.
"""

from pydantic import BaseModel, Field
from typing import Optional


class ChatRequest(BaseModel):
    """User chat request"""
    message: str = Field(..., min_length=1, max_length=1000, description="User's IT issue")
    conversation_id: Optional[str] = Field(
        None,
        description="Existing conversation id. Omit for a new conversation."
    )
    user_id: Optional[str] = Field(
        None,
        description="External user id, for example Microsoft Teams user id."
    )
    defer_response: bool = Field(
        False,
        description="If true, returns immediately with a request_id and you can poll /chat/result/{request_id}.",
    )
    
    class Config:
        example = {
            "message": "Teams microphone is not working",
            "conversation_id": "optional-existing-id",
            "user_id": "optional-user-id",
            "defer_response": False,
        }


class ChatResponse(BaseModel):
    """API response model"""
    conversation_id: str = Field(description="Conversation id to reuse for follow-up messages")
    user_input: str = Field(description="Original user input")
    category: str = Field(description="Detected issue category")
    response: str = Field(description="Generated IT support response")
    confidence: Optional[float] = Field(None, description="Category detection confidence")
    is_follow_up: bool = Field(False, description="Whether prior conversation context was used")
    request_id: Optional[str] = Field(None, description="Present when defer_response=true")
    status: Optional[str] = Field(None, description="Optional status: pending/success/error")
    
    class Config:
        example = {
            "conversation_id": "c3f51e8e-4dbf-4ee1-9734-30e5af0c6f45",
            "user_input": "My system is running slow",
            "category": "HARDWARE_ISSUE",
            "response": "Step 1: Check running processes...\nStep 2: ...",
            "confidence": 0.95,
            "is_follow_up": False
        }


class HealthResponse(BaseModel):
    """Health check response"""
    status: str
    ollama_available: bool
    model: str


class ErrorResponse(BaseModel):
    """Error response"""
    error: str
    details: Optional[str] = None
