# app/routers/twilio_webhooks.py (complete updated version)
import logging
import asyncio
import json
import time
import base64
from fastapi import (
    APIRouter,
    Request,
    HTTPException,
    Response,
    BackgroundTasks,
    WebSocket,
    WebSocketDisconnect,
)
import os
from fastapi.responses import HTMLResponse, JSONResponse
from typing import Dict, Any, Optional, List
from datetime import datetime
from twilio.twiml.voice_response import (
    VoiceResponse,
    Gather,
    Stream,
    Say,
    Pause,
    Record,
    Connect,
)

from ..services.twilio_service import twilio_service
from ..services.evaluator import evaluator_service
from ..services.s3_service import s3_service
from ..services.openai_service import openai_service
from ..services.dynamodb_service import dynamodb_service
from ..services.realtime_service import realtime_service
from ..config import config

router = APIRouter(
    prefix="/webhooks",
    tags=["webhooks"],
    responses={404: {"description": "Not found"}},
)

logger = logging.getLogger(__name__)

# Store active WebSocket connections
active_websockets = {}


def get_test_id_from_request(
    request: Request, form_data: dict, call_sid: str = None
) -> str:
    """
    Extract test_id from various sources to avoid code duplication.
    """
    # First try query params
    test_id = request.query_params.get("test_id")

    # If not in query params, try form data
    if not test_id:
        test_id = form_data.get("test_id")
        if test_id:
            logger.info(f"Found test_id in form data: {test_id}")

    # If still not found and we have a call_sid, check active calls
    if not test_id and call_sid and call_sid in twilio_service.active_calls:
        test_id = twilio_service.active_calls[call_sid].get("test_id")
        logger.info(f"Found test_id in twilio_service.active_calls: {test_id}")

    return test_id


@router.post("/call-started")
async def call_started(request: Request):
    """
    Handle call started webhook from Twilio.
    This webhook is called when a call is connected. It generates
    the TwiML response for streaming audio.
    """
    # Parse the request form data
    form_data = await request.form()
    call_sid = form_data.get("CallSid")

    logger.info(f"Call started webhook received - Form data: {dict(form_data)}")
    logger.info(f"Request URL: {request.url}")
    logger.info(f"Query params: {dict(request.query_params)}")

    # Extract test_id from multiple possible sources
    test_id = get_test_id_from_request(request, form_data, call_sid)
    logger.info(f"Call started webhook - CallSid: {call_sid}, test_id: {test_id}")

    # First check if the test is in memory
    test_in_memory = test_id in evaluator_service.active_tests
    if test_in_memory:
        logger.info(f"Test {test_id} found in memory")
    else:
        logger.info(f"Test {test_id} not in memory, checking DynamoDB")

    # If not in memory, try DynamoDB
    if not test_in_memory and test_id:
        test_data = dynamodb_service.get_test(test_id)

        if test_data:
            logger.info(f"Test {test_id} found in DynamoDB")
            # Load test into memory
            evaluator_service.active_tests[test_id] = test_data
            test_in_memory = True
            logger.info(f"Loaded test data from DynamoDB: {test_data}")
        else:
            logger.info(f"Test {test_id} not found in DynamoDB")

            # Try to find a waiting test as a last resort
            waiting_tests = dynamodb_service.get_waiting_tests()
            if waiting_tests:
                logger.info(f"Found {len(waiting_tests)} waiting tests")
                waiting_test = waiting_tests[0]
                test_id = waiting_test.get("test_id")
                test_data = waiting_test.get("test_data")

                if test_id and test_data:
                    evaluator_service.active_tests[test_id] = test_data
                    test_in_memory = True
                    logger.info(f"Using waiting test {test_id} as fallback")
                else:
                    logger.info(f"Invalid waiting test data")
            else:
                logger.info(f"No waiting tests found")

    # Generate TwiML response based on test data
    if test_id and test_in_memory:
        logger.info(f"Generating TwiML for test {test_id}")

        # Update test status
        previous_status = evaluator_service.active_tests[test_id].get(
            "status", "unknown"
        )
        evaluator_service.active_tests[test_id]["status"] = "in_progress"
        evaluator_service.active_tests[test_id]["call_sid"] = call_sid

        logger.info(
            f"Updated test {test_id} status from {previous_status} to in_progress"
        )

        # Save to DynamoDB - do it once
        dynamodb_service.update_test_status(test_id, "in_progress")
        dynamodb_service.save_test(test_id, evaluator_service.active_tests[test_id])

        # Get test case data
        test_case = evaluator_service.active_tests[test_id].get("test_case", {})

        # Get WebSocket URL from the current request
        host = request.headers.get("host", request.url.hostname)
        protocol = "wss" if request.url.scheme == "https" else "ws"

        # Create WebSocket URL using the host from the current request
        websocket_url = f"{protocol}://{host}/media-stream"
        logger.info(f"Using WebSocket URL: {websocket_url}")

        # Create TwiML
        response = VoiceResponse()

        # Add introduction with instructions
        response.say("Starting real-time AI evaluation call.")
        response.pause(length=1)

        # Create the Stream for media
        connect = Connect()
        stream = Stream(
            name="media_stream",
            url=f"{websocket_url}?test_id={test_id}&call_sid={call_sid}",
        )

        # Add parameters for audio stream
        stream.parameter(name="format", value="audio")
        stream.parameter(name="rate", value="8000")

        connect.stream(stream)
        response.append(connect)

        logger.info(f"Generated TwiML: {str(response)}")
        return HTMLResponse(content=str(response), media_type="application/xml")
    else:
        # Default TwiML if no test found
        response = VoiceResponse()
        response.say("Starting simplified evaluation call.")
        response.pause(length=1)
        response.say(f"No test found with the provided ID: {test_id}")
        response.pause(length=2)
        response.say("Thank you for your time. This concludes our test.")

        logger.info(f"Generated default TwiML: {str(response)}")
        return HTMLResponse(content=str(response), media_type="application/xml")


@router.post("/call-status")
async def call_status(request: Request, background_tasks: BackgroundTasks):
    """
    Handle call status webhook from Twilio.
    This webhook is called when a call status changes.
    """
    try:
        form_data = await request.form()
        logger.info(f"Call status webhook received - Form data: {dict(form_data)}")

        call_sid = form_data.get("CallSid")
        call_status = form_data.get("CallStatus")
        test_id = get_test_id_from_request(request, form_data, call_sid)

        logger.info(
            f"Call status update - SID: {call_sid}, status: {call_status}, test_id: {test_id}"
        )

        # If call is completed or failed, process the test
        if call_status in ["completed", "failed", "busy", "no-answer", "canceled"]:
            if test_id:
                # Run in background to avoid blocking
                background_tasks.add_task(
                    process_completed_call, test_id, call_sid, call_status
                )

        # Return a success response
        return JSONResponse(content={"status": "received"})
    except Exception as e:
        logger.error(f"Error handling call status webhook: {str(e)}")
        return JSONResponse(content={"status": "error", "message": str(e)})


async def process_completed_call(test_id: str, call_sid: str, call_status: str):
    """
    Process a completed call.
    This function is called as a background task with improved timing to wait for transcriptions.
    """
    logger.info(
        f"Processing completed call - SID: {call_sid}, status: {call_status}, test_id: {test_id}"
    )

    try:
        # Wait longer for transcriptions to come in - 15 seconds should be enough for most cases
        # This ensures the transcriptions have time to be processed before generating the report
        await asyncio.sleep(15)

        # Update test status in memory
        if test_id in evaluator_service.active_tests:
            if call_status == "completed":
                evaluator_service.active_tests[test_id]["status"] = "completed"
            else:
                evaluator_service.active_tests[test_id]["status"] = "failed"
                evaluator_service.active_tests[test_id][
                    "error"
                ] = f"Call ended with status: {call_status}"

            evaluator_service.active_tests[test_id]["end_time"] = time.time()

            # Update in DynamoDB
            dynamodb_service.update_test_status(
                test_id, evaluator_service.active_tests[test_id]["status"]
            )

            # Save the full test data to ensure all conversation turns are preserved
            dynamodb_service.save_test(test_id, evaluator_service.active_tests[test_id])

            # Get conversation - first try to get from realtime service
            conversation = []
            if hasattr(realtime_service, "get_conversation"):
                conversation = realtime_service.get_conversation(call_sid)

            # If no conversation from realtime service, get from evaluator
            if not conversation:
                conversation = evaluator_service.active_tests[test_id].get(
                    "conversation", []
                )

            # Generate report
            if conversation:
                report = await evaluator_service.generate_report_from_conversation(
                    test_id, conversation
                )

                # Store the report ID in the test data for later reference
                if report:
                    evaluator_service.active_tests[test_id]["report_id"] = str(
                        report.id
                    )
                    dynamodb_service.save_test(
                        test_id, evaluator_service.active_tests[test_id]
                    )

                logger.info(f"Generated report for test {test_id}")
            else:
                logger.info(f"No conversation data for test {test_id}")
                report = await evaluator_service.generate_empty_report(
                    test_id,
                    f"No conversation data recorded. Call ended with status: {call_status}",
                )

                # Store the report ID even for empty reports
                if report:
                    evaluator_service.active_tests[test_id]["report_id"] = str(
                        report.id
                    )
                    dynamodb_service.save_test(
                        test_id, evaluator_service.active_tests[test_id]
                    )
        else:
            logger.info(f"Test {test_id} not found in memory for completed call")

            # Try to get from DynamoDB
            test_data = dynamodb_service.get_test(test_id)

            if test_data:
                logger.info(f"Found test {test_id} in DynamoDB for completed call")
                evaluator_service.active_tests[test_id] = test_data

                # Update status
                evaluator_service.active_tests[test_id]["status"] = (
                    "completed" if call_status == "completed" else "failed"
                )
                evaluator_service.active_tests[test_id]["end_time"] = time.time()

                # Save to DynamoDB
                dynamodb_service.update_test_status(
                    test_id, evaluator_service.active_tests[test_id]["status"]
                )
                dynamodb_service.save_test(
                    test_id, evaluator_service.active_tests[test_id]
                )

                # Generate empty report
                report = await evaluator_service.generate_empty_report(
                    test_id,
                    f"Test completed but conversation data was lost. Call ended with status: {call_status}",
                )

                # Store report ID
                if report:
                    evaluator_service.active_tests[test_id]["report_id"] = str(
                        report.id
                    )
                    dynamodb_service.save_test(
                        test_id, evaluator_service.active_tests[test_id]
                    )
            else:
                logger.info(f"Test {test_id} not found in DynamoDB for completed call")
    except Exception as e:
        logger.error(f"Error processing completed call: {str(e)}")
        import traceback

        logger.error(f"Traceback: {traceback.format_exc()}")


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
