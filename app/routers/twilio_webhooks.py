import logging
import json
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse
from twilio.twiml.voice_response import VoiceResponse, Connect, Stream

from ..services.twilio_service import twilio_service
from ..services.dynamodb_service import dynamodb_service
from ..services.realtime_service import realtime_service

router = APIRouter(prefix="/webhooks", tags=["Twilio Webhooks"])
logger = logging.getLogger(__name__)


@router.post("/call-started")
async def call_started(request: Request):
    """Handles an incoming call and sets up AI-driven conversation via OpenAI real-time API."""
    try:
        form_data = await request.form()
        call_sid = form_data.get("CallSid")
        test_id = request.query_params.get("test_id")

        logger.info(f"Incoming call received - CallSid: {call_sid}, Test ID: {test_id}")

        # Get test data from memory or database
        test_data = dynamodb_service.get_test(test_id) or {}
        test_case = test_data.get("test_case", {})
        questions = test_case.get("config", {}).get("questions", [])
        first_question = (
            questions[0].get("text", "Tell me about your service.")
            if questions
            else "No questions available."
        )

        # Start OpenAI session (handles AI-generated responses)
        await realtime_service.initialize_session(
            call_sid=call_sid,
            test_id=test_id,
            persona=test_case.get("persona", {}),
            behavior=test_case.get("behavior", {}),
            knowledge_base=test_case.get("config", {}).get("knowledge_base", {}),
        )

        # Generate WebSocket URL for streaming
        websocket_url = f"wss://your-api-gateway.amazonaws.com/dev/media-stream?test_id={test_id}&call_sid={call_sid}"
        logger.info(f"Using WebSocket URL: {websocket_url}")

        # Generate TwiML response with question + media stream
        response = VoiceResponse()
        response.say(first_question)  # Speak the first question
        connect = Connect()
        stream = Stream(name="media_stream", url=websocket_url)
        connect.append(stream)
        response.append(connect)

        logger.info(f"TwiML Generated: {str(response)}")
        return HTMLResponse(content=str(response), media_type="application/xml")

    except Exception as e:
        logger.error(f"Error in call-started: {str(e)}")
        response = VoiceResponse()
        response.say("An error occurred. Ending call.")
        return HTMLResponse(content=str(response), media_type="application/xml")


@router.post("/call-status")
async def call_status(request: Request):
    """Handles Twilio call status updates (e.g., completed, failed, ongoing)."""
    try:
        form_data = await request.form()
        call_sid = form_data.get("CallSid")
        call_status = form_data.get("CallStatus")
        test_id = request.query_params.get("test_id")

        logger.info(f"Call Status Update - CallSid: {call_sid}, Status: {call_status}")

        if call_status in ["completed", "failed"]:
            # End the OpenAI session if call is over
            await realtime_service.end_session(call_sid)
            logger.info(f"Ended OpenAI session for {call_sid}")

        return {"status": "received"}

    except Exception as e:
        logger.error(f"Error in call-status: {str(e)}")
        return {"status": "error", "message": str(e)}
