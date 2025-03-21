# app/routers/twilio_webhooks.py
import logging
import json

from fastapi import (
    APIRouter,
    Request,
    WebSocket,
    WebSocketDisconnect,
)

from app.services.twilio_service import twilio_service
from app.services.evaluator import evaluator_service

router = APIRouter(prefix="/webhooks", tags=["Twilio Webhooks"])
logger = logging.getLogger(__name__)

active_websockets = {}


@router.post("/call-status")
async def call_status(request: Request):
    """Handles Twilio call status updates."""
    try:
        form_data = await request.form()
        call_sid = form_data.get("CallSid")
        call_status = form_data.get("CallStatus")
        test_id = request.query_params.get("test_id")

        logger.info(
            f"Call Status Update - CallSid: {call_sid}, Status: {call_status}, Test ID: {test_id}"
        )

        # Log but don't end call based on status updates - let the conversation flow control it
        if call_status in ["completed", "failed", "busy", "no-answer", "canceled"]:
            logger.info(
                f"Received terminal status {call_status} for call {call_sid}, but not ending session yet"
            )
            # Don't end the session here - let the conversation handler manage it

        return {"status": "received"}

    except Exception as e:
        logger.error(f"Error in call-status: {str(e)}")
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
