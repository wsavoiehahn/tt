# app/routers/twilio_webhooks.py
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

from ..config import config
from ..services.twilio_service import twilio_service
from ..services.dynamodb_service import dynamodb_service
from ..services.realtime_service import realtime_service
from ..services.evaluator import evaluator_service

router = APIRouter(prefix="/webhooks", tags=["Twilio Webhooks"])
logger = logging.getLogger(__name__)

active_websockets = {}

callback_url = config.get_parameter("/ai-evaluator/twilio_callback_url")


@router.post("/call-started")
async def call_started(request: Request):
    """Handles an incoming call and sets up AI-driven conversation."""
    try:
        form_data = await request.form()
        call_sid = form_data.get("CallSid")
        test_id = request.query_params.get("test_id")

        logger.info(f"Call started - CallSid: {call_sid}, Test ID: {test_id}")

        # Get test data
        test_data = dynamodb_service.get_test(test_id) or {}
        test_case = test_data.get("test_case", {})

        # Initialize OpenAI session
        await realtime_service.initialize_session(call_sid=call_sid, test_id=test_id)

        # Generate TwiML to respond to the initial greeting
        response = VoiceResponse()

        # Add pause to ensure smooth conversation
        response.pause(length=0.5)

        # Record the representative's greeting/initial question
        response.record(
            action=f"{callback_url}/webhooks/record-response?test_id={test_id}",
            maxLength=60,  # 60 seconds max
            timeout=2,  # Stop recording after 2 seconds of silence
            playBeep=True,
            recordingStatusCallback=f"{callback_url}/webhooks/recording-status?test_id={test_id}",
        )

        # Get the next response from OpenAI - this will be a redirect
        response.redirect(f"{callback_url}/webhooks/next-response?test_id={test_id}")

        logger.info(f"TwiML Generated for call {call_sid}")
        return HTMLResponse(content=str(response), media_type="application/xml")

    except Exception as e:
        logger.error(f"Error in call-started: {str(e)}")
        response = VoiceResponse()
        response.say("An error occurred. Ending call.")
        return HTMLResponse(content=str(response), media_type="application/xml")


@router.post("/next-response")
async def next_response(request: Request):
    """Handles getting the next response from OpenAI and continuing the conversation."""
    try:
        form_data = await request.form()
        call_sid = form_data.get("CallSid")
        test_id = request.query_params.get("test_id")

        logger.info(f"Getting next response - CallSid: {call_sid}, Test ID: {test_id}")

        # Get the latest OpenAI response
        response_text = realtime_service.get_latest_response(call_sid)

        # Generate TwiML to say the response and continue conversation
        response = VoiceResponse()

        if response_text:
            # Say the OpenAI response
            response.say(response_text)
        else:
            # Fallback if no response is available
            response.say("I'm processing your request. Please wait a moment.")

        # Check if we should continue the conversation
        if realtime_service.should_continue_conversation(call_sid):
            # Record the next representative response
            response.record(
                action=f"{callback_url}/webhooks/record-response?test_id={test_id}",
                maxLength=60,
                timeout=2,
                playBeep=True,
                recordingStatusCallback=f"{callback_url}/webhooks/recording-status?test_id={test_id}",
            )

            # Add pause to ensure smooth conversation
            response.pause(length=0.2)

            # Get the next response from OpenAI
            response.redirect(
                f"{callback_url}/webhooks/next-response?test_id={test_id}"
            )
        else:
            # End the conversation
            response.say("Thank you for your time. This concludes our evaluation.")

            # Trigger evaluation
            await realtime_service.end_conversation_and_evaluate(call_sid)

        return HTMLResponse(content=str(response), media_type="application/xml")

    except Exception as e:
        logger.error(f"Error in next-response: {str(e)}")
        response = VoiceResponse()
        response.say("An error occurred. Ending call.")
        return HTMLResponse(content=str(response), media_type="application/xml")


@router.post("/record-response")
async def record_response(request: Request):
    """Handle recorded response from representative with improved error handling."""
    try:
        form_data = await request.form()
        call_sid = form_data.get("CallSid")
        test_id = request.query_params.get("test_id")
        recording_url = form_data.get("RecordingUrl")
        recording_sid = form_data.get("RecordingSid")

        logger.info(f"Received recording for call {call_sid}: {recording_sid}")
        logger.info(f"Form data: {dict(form_data)}")  # Log all form data for debugging

        # Sometimes Twilio doesn't include the URL in the callback
        if not recording_url and recording_sid:
            logger.info(f"No URL provided, using SID: {recording_sid}")
            recording_url = None  # Let the download_recording method handle it

        # Download the recording
        audio_data = twilio_service.download_recording(recording_url, recording_sid)

        if not audio_data:
            # Try an alternative approach - get recording directly by SID
            logger.info(f"Trying to fetch recording directly by SID")
            recording = twilio_service.client.recordings(recording_sid).fetch()
            recording_url = f"https://api.twilio.com/2010-04-01/Accounts/{twilio_service.account_sid}/Recordings/{recording_sid}.mp3"
            audio_data = twilio_service.download_recording(recording_url)

        if not audio_data:
            logger.error(f"Failed to download recording {recording_sid}")

            # Return TwiML to continue the conversation despite the error
            response = VoiceResponse()
            response.say("I didn't catch that. Let me continue.")

            # Redirect to get the next response
            response.redirect(
                f"{callback_url}/webhooks/next-response?test_id={test_id}"
            )

            return HTMLResponse(content=str(response), media_type="application/xml")

        # Process the response through OpenAI
        await realtime_service.process_agent_response(call_sid, test_id, audio_data)

        # Return TwiML to continue the conversation
        response = VoiceResponse()
        response.redirect(f"{callback_url}/webhooks/next-response?test_id={test_id}")

        return HTMLResponse(content=str(response), media_type="application/xml")

    except Exception as e:
        logger.error(f"Error processing recording: {str(e)}")
        import traceback

        logger.error(traceback.format_exc())

        # Return a graceful error TwiML
        response = VoiceResponse()
        response.say("I'm having trouble processing your response. Let's try again.")
        return HTMLResponse(content=str(response), media_type="application/xml")


@router.post("/recording-status")
async def recording_status(request: Request):
    """Handle recording status updates."""
    try:
        form_data = await request.form()
        recording_sid = form_data.get("RecordingSid")
        recording_status = form_data.get("RecordingStatus")
        recording_url = form_data.get("RecordingUrl")
        call_sid = form_data.get("CallSid")
        test_id = request.query_params.get("test_id")

        logger.info(
            f"Recording Status Update - RecordingSid: {recording_sid}, Status: {recording_status}, URL: {recording_url}"
        )

        # Store recording URL if available
        if recording_status == "completed" and recording_url and call_sid and test_id:
            # Store the recording URL in S3 and get a permanent URL
            from ..services.s3_service import s3_service

            permanent_url = s3_service.save_recording(recording_url, test_id, call_sid)

            # Update the conversation turn with the audio URL
            from ..services.evaluator import evaluator_service

            if test_id in evaluator_service.active_tests:
                conversation = evaluator_service.active_tests[test_id].get(
                    "conversation", []
                )
                # Find the last agent turn without an audio URL
                for turn in reversed(conversation):
                    if turn["speaker"] == "agent" and not turn.get("audio_url"):
                        turn["audio_url"] = permanent_url
                        break

        return {"status": "received"}

    except Exception as e:
        logger.error(f"Error in recording-status: {str(e)}")
        import traceback

        logger.error(traceback.format_exc())
        return {"status": "error", "message": str(e)}


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
