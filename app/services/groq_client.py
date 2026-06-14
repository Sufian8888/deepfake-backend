import os
import httpx
import logging
from fastapi import HTTPException
from app.config import settings

logger = logging.getLogger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

async def call_groq_chat(system_prompt: str, messages: list) -> str:
    """
    Calls the Groq chat completions API with the system prompt, history, and user message.
    messages: list of dicts: [{"role": "user" | "assistant", "content": "..."}]
    """
    api_key = settings.GROQ_API_KEY or os.getenv("GROQ_API_KEY", "")
    
    if not api_key:
        logger.error("GROQ_API_KEY is not configured.")
        raise HTTPException(
            status_code=500,
            detail="Groq API key is not configured on the server. Please add GROQ_API_KEY to your .env file."
        )
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    # Combine system prompt and message history
    formatted_messages = [{"role": "system", "content": system_prompt}] + messages
    
    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": formatted_messages,
        "temperature": 0.2,
        "max_tokens": 500
    }
    
    async with httpx.AsyncClient() as client:
        try:
            logger.info("Sending request to Groq API...")
            response = await client.post(GROQ_API_URL, headers=headers, json=payload, timeout=20.0)
            
            if response.status_code != 200:
                error_detail = response.text
                try:
                    error_json = response.json()
                    error_detail = error_json.get("error", {}).get("message", error_detail)
                except:
                    pass
                logger.error(f"Groq API returned status {response.status_code}: {error_detail}")
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Groq API error: {error_detail}"
                )
            
            result = response.json()
            reply = result["choices"][0]["message"]["content"]
            return reply
            
        except httpx.TimeoutException:
            logger.error("Groq API request timed out.")
            raise HTTPException(
                status_code=504,
                detail="Groq API request timed out. Please try again."
            )
        except httpx.RequestError as e:
            logger.error(f"Failed to connect to Groq API: {str(e)}")
            raise HTTPException(
                status_code=503,
                detail=f"Service unavailable. Failed to connect to AI provider: {str(e)}"
            )
