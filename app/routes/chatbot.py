from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List, Dict, Optional
from sqlalchemy.orm import Session
import re
import json

from app.database import get_db
from app.models import User, Video, Frame
from app.auth import get_current_user
from app.services.groq_client import call_groq_chat

router = APIRouter()

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    message: str
    history: List[ChatMessage]

@router.post("/query")
async def chatbot_query(
    request: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    message = request.message
    user_id = current_user.id
    
    # Helper to normalize names (strip extensions, spaces, dots, lowercase)
    def normalize_name(name: str) -> str:
        base = name.rsplit('.', 1)[0] if '.' in name else name
        return "".join(c for c in base.lower() if c.isalnum())

    clean_msg = message.lower()
    msg_norm = "".join(c for c in clean_msg if c.isalnum())
    
    # Query matching
    video_match = None
    user_videos = db.query(Video).filter(Video.user_id == user_id).all()
    
    # 1. Look for direct filename matches in message
    for v in user_videos:
        if v.original_filename.lower() in clean_msg or v.filename.lower() in clean_msg:
            video_match = v
            break

    # 2. Look for base name (no extension) matches in message
    if not video_match:
        for v in user_videos:
            base_name = v.original_filename.rsplit('.', 1)[0]
            if len(base_name) >= 3 and base_name.lower() in clean_msg:
                video_match = v
                break

    # 3. Fuzzy/Alphanumeric normalized matching (e.g., user wrote "0.20mp4" or "my 020", DB has "020.mp4")
    if not video_match:
        for v in user_videos:
            v_norm = normalize_name(v.original_filename)
            if len(v_norm) >= 3 and v_norm in msg_norm:
                video_match = v
                break

    # Context setup
    context = ""
    
    if not video_match:
        # Check if they asked to list their videos (tolerant to common typos like vidoe/vidoes)
        is_list_request = any(word in clean_msg for word in [
            "list", "show", "what are my", "videos", "vidoes", "vidoe", "files", "uploaded"
        ])
        
        if is_list_request:
            if not user_videos:
                context = "You currently have no uploaded videos. Please upload a video to get started!"
            else:
                video_list = "\n".join([f"- {v.original_filename} (Status: {v.status})" for v in user_videos])
                context = f"The user has the following videos uploaded:\n{video_list}"
        else:
            # Polite response to ask for the exact filename
            context = (
                "No specific video was found matching the user's query. "
                "Politely request the user to provide the exact filename (including extension, e.g., 'video.mp4') "
                "of the video they want to discuss, or suggest listing their videos."
            )
    else:
        # Video found! Fetch frame analysis details
        frames = db.query(Frame).filter(Frame.video_id == video_match.id).order_by(Frame.frame_number).all()
        
        total_frames = len(frames)
        fake_frames = sum(1 for f in frames if f.is_fake)
        suspicious_frames = sum(1 for f in frames if f.is_suspicious)
        
        # Calculate percentage of fake frames
        fake_percentage = (fake_frames / total_frames * 100) if total_frames > 0 else 0.0
        
        # Calculate average confidence score
        valid_scores = [f.confidence_score for f in frames if f.confidence_score is not None]
        avg_confidence = (sum(valid_scores) / len(valid_scores)) if valid_scores else (video_match.confidence_score or 0.0)
        
        # Collect regions
        flagged_regions = {}
        for f in frames:
            if f.analysis_details and isinstance(f.analysis_details, dict):
                regions = f.analysis_details.get("flagged_regions") or f.analysis_details.get("regions") or f.analysis_details.get("suspicious_regions")
                if isinstance(regions, list):
                    for r in regions:
                        flagged_regions[str(r)] = flagged_regions.get(str(r), 0) + 1
                elif isinstance(regions, str):
                    flagged_regions[regions] = flagged_regions.get(regions, 0) + 1
        
        regions_str = "None noted"
        if flagged_regions:
            sorted_regions = sorted(flagged_regions.items(), key=lambda x: x[1], reverse=True)
            regions_str = ", ".join([f"{r} ({count} frames)" for r, count in sorted_regions])
            
        verdict = "Likely Deepfake" if video_match.is_deepfake else "Likely Authentic"
        if video_match.status != "completed":
            verdict = f"Analysis not completed (Current Status: {video_match.status})"
            
        context = (
            f"Video: {video_match.original_filename}\n"
            f"Status: {video_match.status}\n"
            f"Total frames analyzed: {total_frames}\n"
            f"Frames flagged as fake: {fake_frames} ({fake_percentage:.1f}%)\n"
            f"Average confidence score: {avg_confidence:.1f}%\n"
            f"Suspicious regions noted: {regions_str}\n"
            f"Overall verdict: {verdict}"
        )

    # Injected RAG bot system prompt
    system_prompt = (
        "You are an AI assistant that explains deepfake video analysis results to users in plain language. "
        "You will be given structured data about a specific video's analysis (frame counts, confidence scores, "
        "flagged regions, verdict). Use ONLY this data to answer the user's question.\n\n"
        "- Explain confidence scores in simple terms (e.g., \"91% confidence means the model is very "
        "certain this video has been manipulated\").\n"
        "- If asked \"why\" a video was flagged, reference the suspicious regions and frame statistics.\n"
        "- Be concise (3-5 sentences) and avoid technical jargon unless the user asks for detail.\n"
        "- If no video data is provided, politely ask the user for the exact filename (including "
        "extension) of the video they want to discuss.\n"
        "- Never make up data that wasn't provided to you."
    )
    
    # Build messages
    messages = []
    # If we have a context string, let's append it to the system prompt
    if context:
        system_prompt += f"\n\nHere is the retrieved context/data to answer the query:\n{context}"
        
    for msg in request.history:
        messages.append({"role": msg.role, "content": msg.content})
        
    messages.append({"role": "user", "content": message})
    
    try:
        reply = await call_groq_chat(system_prompt, messages)
        return {"reply": reply}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate response: {str(e)}"
        )
