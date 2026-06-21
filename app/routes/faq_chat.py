from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from typing import List
from app.services.groq_client import call_groq_chat

router = APIRouter()

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    message: str
    history: List[ChatMessage]

@router.post("")
async def faq_chat(request: ChatRequest):
    # Define system prompt
    system_prompt = (
    "You are a helpful assistant for \"AI-Based System for Detection of Manipulated Media Content\", "
    "a deepfake video detection web application. Answer questions clearly and concisely based on "
    "the following facts:\n\n"
    "- The system detects deepfakes/manipulated videos using a ConvNeXt-Tiny model performing "
    "6-class classification identifying five specific manipulation techniques (Deepfakes, "
    "Face2Face, FaceShifter, FaceSwap, NeuralTextures) plus genuine/original content, "
    "deployed for CPU-based execution.\n"
    "- Users upload videos (MP4, AVI, MOV, max 50MB).\n"
    "- The system extracts frames, detects faces, runs AI inference, and returns a confidence "
    "score (0-100%) indicating likelihood of manipulation.\n"
    "- Results include a heatmap visualization (highlighting suspicious facial regions) and a "
    "plain-language explanation of why a video was flagged.\n"
    "- Users must register with email verification to use the platform.\n"
    "- All analyses are saved to the user's dashboard for later review.\n"
    "- The platform is free and designed to run without expensive GPU hardware.\n"
    "- An admin panel exists for managing users, viewing analytics, and monitoring activity.\n\n"
    "If asked something outside this scope, politely say you can only answer questions about "
    "this platform.\n"
    "Keep your responses short (2-4 sentences)."
)
    
    # Convert request history and message to messages list for Groq
    messages = []
    for msg in request.history:
        messages.append({"role": msg.role, "content": msg.content})
    
    # Append the new user message
    messages.append({"role": "user", "content": request.message})
    
    # Call Groq
    try:
        reply = await call_groq_chat(system_prompt, messages)
        return {"reply": reply}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate response: {str(e)}"
        )
