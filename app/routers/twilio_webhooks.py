import logging
import json

from fastapi import (
    APIRouter,
    Request,
    HTTPException,
    Response,
    BackgroundTasks,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import HTMLResponse
from twilio.twiml.voice_response import VoiceResponse, Connect, Stream
from typing import Dict, Any, Optional


from ..services.twilio_service import twilio_service
from ..services.dynamodb_service import dynamodb_service
from ..services.realtime_service import realtime_service
from ..services.evaluator import evaluator_service

router = APIRouter(prefix="/webhooks", tags=["Twilio Webhooks"])
logger = logging.getLogger(__name__)

active_websockets = {}


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
        await realtime_service.initialize_session(call_sid=call_sid, test_id=test_id)

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

        logger.info(
            f"Call Status Update - CallSid: {call_sid}, Status: {call_status}, Test ID: {test_id}"
        )

        if call_status in ["completed", "failed", "busy", "no-answer", "canceled"]:
            # Trigger the end session flow which will handle evaluation
            from ..services.realtime_service import realtime_service

            await realtime_service.end_session(call_sid)
            logger.info(f"Ended session for {call_sid}")

        return {"status": "received"}

    except Exception as e:
        logger.error(f"Error in call-status: {str(e)}")
        import traceback

        logger.error(f"Call status error traceback: {traceback.format_exc()}")
        return {"status": "error", "message": str(e)}


# WebSocket endpoint for client-side communication
@router.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    """
    WebSocket endpoint for client-side communication.
    This allows the web interface to get real-time updates on call progress.
    """
    await websocket.accept()
    active_websockets[client_id] = websocket

    try:
        while True:
            # Wait for messages from the client
            data = await websocket.receive_text()
            message = json.loads(data)

            # Process commands from client
            command = message.get("command")

            if command == "subscribe":
                # Subscribe to updates for a specific test/call
                test_id = message.get("test_id")
                if test_id:
                    await websocket.send_json(
                        {"type": "subscription", "status": "active", "test_id": test_id}
                    )

            elif command == "get_status":
                # Get status for a specific test
                test_id = message.get("test_id")
                if test_id:
                    status = "unknown"

                    # Check if test is in memory
                    if test_id in evaluator_service.active_tests:
                        status = evaluator_service.active_tests[test_id].get(
                            "status", "unknown"
                        )

                    await websocket.send_json(
                        {"type": "status", "test_id": test_id, "status": status}
                    )

            elif command == "get_conversation":
                # Get conversation for a specific test
                test_id = message.get("test_id")
                if test_id:
                    conversation = []

                    # Check if test is in memory
                    if test_id in evaluator_service.active_tests:
                        conversation = evaluator_service.active_tests[test_id].get(
                            "conversation", []
                        )

                    await websocket.send_json(
                        {
                            "type": "conversation",
                            "test_id": test_id,
                            "turns": conversation,
                        }
                    )

            elif command == "end_call":
                # End an active call
                call_sid = message.get("call_sid")
                if call_sid:
                    result = twilio_service.end_call(call_sid)
                    await websocket.send_json(
                        {
                            "type": "call_control",
                            "call_sid": call_sid,
                            "status": result.get("status", "error"),
                            "message": result.get("error", "Call ended"),
                        }
                    )

    except WebSocketDisconnect:
        # Remove from active websockets
        active_websockets.pop(client_id, None)
        logger.info(f"Client disconnected: {client_id}")

    except Exception as e:
        logger.error(f"Error in websocket connection: {str(e)}")
        # Try to send error if connection is still open
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass

        # Remove from active websockets
        active_websockets.pop(client_id, None)


async def broadcast_update(message: Dict[str, Any]):
    """
    Broadcast an update to all connected websockets.

    Args:
        message: The message to broadcast
    """
    for client_id, websocket in active_websockets.items():
        try:
            await websocket.send_json(message)
        except Exception as e:
            logger.error(f"Error broadcasting to client {client_id}: {str(e)}")
            # Remove dead connections
            active_websockets.pop(client_id, None)
