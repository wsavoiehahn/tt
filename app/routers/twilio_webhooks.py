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
)

from ..services.twilio_service import twilio_service
from ..services.evaluator import evaluator_service
from ..services.s3_service import s3_service
from ..services.openai_service import openai_service
from ..services.dynamodb_service import dynamodb_service
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

    Args:
        request: The FastAPI request object
        form_data: Form data from the request
        call_sid: Optional call SID to check in active calls

    Returns:
        The extracted test_id or None if not found
    """
    # First try query params
    test_id = request.query_params.get("test_id")

    # If not in query params, try form data
    if not test_id:
        test_id = form_data.get("test_id")
        if test_id:
            logger.error(f"DEBUG: Found test_id in form data: {test_id}")

    # If still not found and we have a call_sid, check active calls
    if not test_id and call_sid and call_sid in twilio_service.active_calls:
        test_id = twilio_service.active_calls[call_sid].get("test_id")
        logger.error(f"DEBUG: Found test_id in twilio_service.active_calls: {test_id}")

    return test_id


def generate_question_twiml_content(
    test_id: str,
    call_sid: str,
    question_text: str,
    question_index: int,
    request: Request,
) -> dict:
    """
    Generate question TwiML content parameters to avoid code duplication.
    Instead of returning a VoiceResponse object, this returns the parameters needed to add the elements.

    Args:
        test_id: Test ID
        call_sid: Call SID
        question_text: The question to ask
        question_index: Question index (1-based)
        request: The FastAPI request object

    Returns:
        Dictionary with parameters for creating TwiML elements
    """
    callback_url = (
        config.get_parameter("/ai-evaluator/twilio_callback_url", False)
        or f"{request.url.scheme}://{request.url.netloc}"
    )

    # Record the question in the conversation
    evaluator_service.record_conversation_turn(
        test_id=test_id,
        call_sid=call_sid,
        speaker="evaluator",
        text=question_text,
    )

    # Update current question index - just update in memory first, will be saved once
    evaluator_service.active_tests[test_id]["current_question_index"] = question_index

    # Return parameters for TwiML elements
    return {
        "question_text": question_text,
        "action_url": f"{callback_url}/webhooks/recording?test_id={test_id}",
        "transcribe_callback": f"{callback_url}/webhooks/transcription?test_id={test_id}",
    }


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

    logger.error(f"DEBUG: Call started webhook received - Form data: {dict(form_data)}")
    logger.error(f"DEBUG: Request URL: {request.url}")
    logger.error(f"DEBUG: Query params: {dict(request.query_params)}")

    # Extract test_id from multiple possible sources
    test_id = get_test_id_from_request(request, form_data, call_sid)
    logger.error(
        f"DEBUG: Call started webhook - CallSid: {call_sid}, test_id: {test_id}"
    )

    # First check if the test is in memory
    test_in_memory = test_id in evaluator_service.active_tests
    if test_in_memory:
        logger.error(f"DEBUG: Test {test_id} found in memory")
    else:
        logger.error(f"DEBUG: Test {test_id} not in memory, checking DynamoDB")

    # If not in memory, try DynamoDB
    if not test_in_memory and test_id:
        test_data = dynamodb_service.get_test(test_id)

        if test_data:
            logger.error(f"DEBUG: Test {test_id} found in DynamoDB")
            # Load test into memory
            evaluator_service.active_tests[test_id] = test_data
            test_in_memory = True
            logger.error(f"DEBUG: Loaded test data from DynamoDB: {test_data}")
        else:
            logger.error(f"DEBUG: Test {test_id} not found in DynamoDB")

            # Try to find a waiting test as a last resort
            waiting_tests = dynamodb_service.get_waiting_tests()
            if waiting_tests:
                logger.error(f"DEBUG: Found {len(waiting_tests)} waiting tests")
                waiting_test = waiting_tests[0]
                test_id = waiting_test.get("test_id")
                test_data = waiting_test.get("test_data")

                if test_id and test_data:
                    evaluator_service.active_tests[test_id] = test_data
                    test_in_memory = True
                    logger.error(f"DEBUG: Using waiting test {test_id} as fallback")
                else:
                    logger.error(f"DEBUG: Invalid waiting test data")
            else:
                logger.error(f"DEBUG: No waiting tests found")

    # Generate TwiML response based on test data
    if test_id and test_in_memory:
        logger.error(f"DEBUG: Generating TwiML for test {test_id}")

        # Update test status
        previous_status = evaluator_service.active_tests[test_id].get(
            "status", "unknown"
        )
        evaluator_service.active_tests[test_id]["status"] = "in_progress"
        evaluator_service.active_tests[test_id]["call_sid"] = call_sid

        logger.error(
            f"DEBUG: Updated test {test_id} status from {previous_status} to in_progress"
        )

        # Save to DynamoDB
        dynamodb_service.update_test_status(test_id, "in_progress")
        dynamodb_service.save_test(test_id, evaluator_service.active_tests[test_id])

        # Get test case data
        test_case = evaluator_service.active_tests[test_id].get("test_case", {})
        questions = test_case.get("config", {}).get("questions", [])
        special_instructions = test_case.get("config", {}).get("special_instructions")

        # Create TwiML
        response = VoiceResponse()

        # Add introduction
        response.say("Starting evaluation call.")
        response.pause(length=1)

        # Add special instructions if present
        if special_instructions:
            response.say(f"Special instructions: {special_instructions}")
            response.pause(length=1)

            # Record the special instructions as a conversation turn
            evaluator_service.record_conversation_turn(
                test_id=test_id,
                call_sid=call_sid,
                speaker="evaluator",
                text=f"Special instructions: {special_instructions}",
            )

        # Add first question if available
        if questions:
            first_question = questions[0]
            question_text = ""

            if isinstance(first_question, dict):
                question_text = first_question.get("text", "")
            else:
                question_text = first_question

            if question_text:
                # Get TwiML parameters for first question
                twiml_params = generate_question_twiml_content(
                    test_id=test_id,
                    call_sid=call_sid,
                    question_text=question_text,
                    question_index=1,
                    request=request,
                )

                # Add the question and record elements directly
                response.say(twiml_params["question_text"])
                response.record(
                    action=twiml_params["action_url"],
                    timeout=15,
                    playBeep=False,
                    transcribe=True,
                    transcribeCallback=twiml_params["transcribe_callback"],
                )
            else:
                response.say("No question text found for this test.")
        else:
            response.say("No questions found for this test.")

        # Add closing
        response.pause(length=1)
        response.say("Thank you for your response. This concludes our evaluation.")

        logger.error(f"DEBUG: Generated TwiML: {str(response)}")
        return HTMLResponse(content=str(response), media_type="application/xml")
    else:
        # Default TwiML if no test found
        response = VoiceResponse()
        response.say("Starting simplified evaluation call.")
        response.pause(length=1)
        response.say(f"No test found with the provided ID: {test_id}")
        response.pause(length=2)
        response.say("Thank you for your time. This concludes our test.")

        logger.error(f"DEBUG: Generated default TwiML: {str(response)}")
        return HTMLResponse(content=str(response), media_type="application/xml")


@router.post("/recording")
async def recording_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Handle recording webhook from Twilio.
    This webhook is called when a recording is completed.
    """
    form_data = await request.form()
    logger.error(f"DEBUG: Recording webhook received - Form data: {dict(form_data)}")

    recording_sid = form_data.get("RecordingSid")
    recording_url = form_data.get("RecordingUrl")
    call_sid = form_data.get("CallSid")
    test_id = get_test_id_from_request(request, form_data, call_sid)

    logger.error(
        f"DEBUG: Recording completed - SID: {recording_sid}, URL: {recording_url}, test_id: {test_id}"
    )

    # Save the recording to S3
    if test_id:
        background_tasks.add_task(
            process_recording,
            test_id=test_id,
            call_sid=call_sid,
            recording_sid=recording_sid,
            recording_url=recording_url,
        )

    # Generate TwiML for the next question
    try:
        if test_id and test_id in evaluator_service.active_tests:
            test_data = evaluator_service.active_tests[test_id]
            test_case = test_data.get("test_case", {})
            questions = test_case.get("config", {}).get("questions", [])
            current_index = test_data.get("current_question_index", 1)

            response = VoiceResponse()

            # Check if there are more questions
            if current_index < len(questions):
                next_question = questions[current_index]
                question_text = ""

                if isinstance(next_question, dict):
                    question_text = next_question.get("text", "")
                else:
                    question_text = next_question

                if question_text:
                    # Get TwiML parameters for next question
                    twiml_params = generate_question_twiml_content(
                        test_id=test_id,
                        call_sid=call_sid,
                        question_text=question_text,
                        question_index=current_index + 1,
                        request=request,
                    )

                    # Create response with question elements
                    response = VoiceResponse()
                    response.say(twiml_params["question_text"])
                    response.record(
                        action=twiml_params["action_url"],
                        timeout=15,
                        playBeep=False,
                        transcribe=True,
                        transcribeCallback=twiml_params["transcribe_callback"],
                    )

                    return HTMLResponse(
                        content=str(response), media_type="application/xml"
                    )

            # If no more questions, end the call
            response.say("Thank you for your responses. This concludes our evaluation.")
            response.hangup()

            return HTMLResponse(content=str(response), media_type="application/xml")
        else:
            # Default response if test not found
            response = VoiceResponse()
            response.say("Recording received, but no active test found.")
            response.hangup()

            return HTMLResponse(content=str(response), media_type="application/xml")
    except Exception as e:
        logger.error(f"DEBUG: Error generating next question: {str(e)}")
        response = VoiceResponse()
        response.say("An error occurred. This concludes our evaluation.")
        response.hangup()

        return HTMLResponse(content=str(response), media_type="application/xml")


@router.post("/transcription")
async def transcription_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Handle transcription webhook from Twilio.
    This webhook is called when a transcription is completed.
    Enhanced to properly update reports with late-arriving transcriptions.
    """
    form_data = await request.form()
    logger.info(f"Transcription webhook received: {dict(form_data)}")

    transcription_sid = form_data.get("TranscriptionSid")
    transcription_text = form_data.get("TranscriptionText")
    recording_sid = form_data.get("RecordingSid")
    call_sid = form_data.get("CallSid")
    test_id = get_test_id_from_request(request, form_data, call_sid)

    logger.info(
        f"Transcription completed - SID: {transcription_sid}, text: {transcription_text}, test_id: {test_id}"
    )

    # Save the transcription
    if test_id and transcription_text:
        # Create a background task to handle this to avoid blocking the webhook response
        background_tasks.add_task(
            process_transcription,
            test_id=test_id,
            call_sid=call_sid,
            transcription_sid=transcription_sid,
            transcription_text=transcription_text,
            recording_sid=recording_sid,
        )

    # Return a success response (Twilio doesn't do anything with this)
    return JSONResponse(content={"status": "received"})


async def process_transcription(
    test_id: str,
    call_sid: str,
    transcription_sid: str,
    transcription_text: str,
    recording_sid: str,
):
    """
    Process a transcription in the background.
    This allows for more complex processing without blocking the webhook response.

    Args:
        test_id: Test ID
        call_sid: Call SID
        transcription_sid: Transcription SID
        transcription_text: The transcribed text
        recording_sid: Associated recording SID
    """
    logger.info(f"Processing transcription for test {test_id}: {transcription_text}")

    try:
        if test_id in evaluator_service.active_tests:
            # First check if this transcription is already recorded
            conversation = evaluator_service.active_tests[test_id].get(
                "conversation", []
            )
            already_exists = False

            for turn in conversation:
                if (
                    turn.get("speaker") == "agent"
                    and turn.get("text") == transcription_text
                ):
                    already_exists = True
                    break

            if not already_exists:
                # Record agent's response in the conversation
                turn_data = evaluator_service.record_conversation_turn(
                    test_id=test_id,
                    call_sid=call_sid,
                    speaker="agent",
                    text=transcription_text,
                )

                # Try to associate this with the recording if one exists
                # Find the audio URL for this recording
                for turn in conversation:
                    # If we find a turn with the matching recording SID or an audio URL that
                    # includes the recording SID in the path, update the text
                    if turn.get("recording_sid") == recording_sid or (
                        turn.get("audio_url") and recording_sid in turn.get("audio_url")
                    ):
                        turn["text"] = transcription_text
                        break

            # Save to S3
            s3_key = s3_service.save_transcription(
                transcription=transcription_text,
                test_id=test_id,
                call_sid=call_sid,
                turn_number=evaluator_service.active_tests[test_id].get(
                    "current_question_index", 1
                )
                - 1,
                speaker="agent",
            )

            # Save to DynamoDB
            dynamodb_service.save_test(test_id, evaluator_service.active_tests[test_id])

            logger.info(f"Saved transcription for test {test_id}")

            # Update the report if it already exists
            if "report_id" in evaluator_service.active_tests[test_id]:
                report_id = evaluator_service.active_tests[test_id]["report_id"]
                await update_report_with_new_data(
                    test_id,
                    report_id,
                    {
                        "conversation": evaluator_service.active_tests[test_id].get(
                            "conversation", []
                        )
                    },
                )
        else:
            logger.error(f"Test {test_id} not found for transcription")

            # Try to load from DynamoDB
            test_data = dynamodb_service.get_test(test_id)
            if test_data:
                logger.info(f"Loaded test {test_id} from DynamoDB for transcription")
                evaluator_service.active_tests[test_id] = test_data

                # Try again with the loaded data
                await process_transcription(
                    test_id,
                    call_sid,
                    transcription_sid,
                    transcription_text,
                    recording_sid,
                )
    except Exception as e:
        logger.error(f"Error processing transcription: {str(e)}")
        import traceback

        logger.error(f"Traceback: {traceback.format_exc()}")


async def process_recording(
    test_id: str, call_sid: str, recording_sid: str, recording_url: str
):
    """
    Process a recording from Twilio.
    This function is called as a background task.
    """
    logger.info(
        f"Processing recording - SID: {recording_sid}, URL: {recording_url}, test_id: {test_id}"
    )

    try:
        # Format the URL properly if needed - sometimes Twilio sends URLs without .mp3 extension
        if not recording_url.endswith(".mp3"):
            recording_url = f"{recording_url}.mp3"

        # Make sure we have the auth credentials
        account_sid = config.get_parameter("/twilio/account_sid")
        auth_token = config.get_parameter("/twilio/auth_token")

        # Ensure credentials are available
        if not account_sid or not auth_token:
            logger.error("Missing Twilio credentials for downloading recording")
            return

        # Create an authenticated session for downloading
        import requests
        from requests.auth import HTTPBasicAuth

        # Download the recording with proper authentication
        response = requests.get(
            recording_url,
            auth=HTTPBasicAuth(account_sid, auth_token),
            timeout=30,  # Add timeout to prevent hanging
        )

        if response.status_code != 200:
            logger.error(
                f"Failed to download recording: HTTP {response.status_code} - {response.text[:100]}"
            )
            return

        # Create a unique filename for this recording
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        recording_filename = f"{timestamp}_{recording_sid}.mp3"

        # Save to S3
        s3_key = f"tests/{test_id}/calls/{call_sid}/recordings/{recording_filename}"

        # Upload the recording data directly to S3
        s3_service.s3_client.put_object(
            Bucket=s3_service.bucket_name,
            Key=s3_key,
            Body=response.content,
            ContentType="audio/mp3",
        )

        # Generate the S3 URL
        s3_url = f"s3://{s3_service.bucket_name}/{s3_key}"
        logger.info(f"Saved recording to S3: {s3_url}")

        # Find the most recent agent turn in the conversation
        if test_id in evaluator_service.active_tests:
            conversation = evaluator_service.active_tests[test_id].get(
                "conversation", []
            )

            # Find the matching agent turn - usually the most recent one
            updated = False
            for i in range(len(conversation) - 1, -1, -1):
                if conversation[i].get("speaker") == "agent":
                    # Update the audio URL
                    conversation[i]["audio_url"] = s3_url
                    updated = True
                    logger.info(f"Updated agent turn {i} with recording URL")
                    break

            if not updated:
                # If we didn't find a matching turn, add a new one
                logger.info("No matching agent turn found, creating a new one")
                evaluator_service.record_conversation_turn(
                    test_id=test_id,
                    call_sid=call_sid,
                    speaker="agent",
                    text="[Audio recording, no transcription yet]",
                    audio_url=s3_url,
                )

            # Save updated conversation
            evaluator_service.active_tests[test_id]["conversation"] = conversation
            dynamodb_service.save_test(test_id, evaluator_service.active_tests[test_id])

            # Try to update existing report if one exists
            if "report_id" in evaluator_service.active_tests[test_id]:
                await update_report_with_new_data(
                    test_id,
                    evaluator_service.active_tests[test_id]["report_id"],
                    {"conversation": conversation},
                )
    except Exception as e:
        logger.error(f"Error processing recording: {str(e)}")
        import traceback

        logger.error(f"Traceback: {traceback.format_exc()}")


@router.post("/call-status")
async def call_status(request: Request, background_tasks: BackgroundTasks):
    """
    Handle call status webhook from Twilio.
    This webhook is called when a call status changes.
    """
    form_data = await request.form()
    logger.error(f"DEBUG: Call status webhook received - Form data: {dict(form_data)}")

    call_sid = form_data.get("CallSid")
    call_status = form_data.get("CallStatus")
    test_id = get_test_id_from_request(request, form_data, call_sid)

    logger.error(
        f"DEBUG: Call status update - SID: {call_sid}, status: {call_status}, test_id: {test_id}"
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


async def update_report_with_new_data(
    test_id: str, report_id: str, new_data: dict
) -> bool:
    """
    Update an existing report with new data.
    This allows for appending transcriptions and recordings that arrive after the report is generated.

    Args:
        test_id: The test ID
        report_id: The report ID
        new_data: New data to incorporate into the report

    Returns:
        True if successful, False otherwise
    """
    try:
        logger.info(f"Updating report {report_id} with new data")

        # Get the existing report
        from ..services.reporting import reporting_service

        existing_report = reporting_service.get_report(report_id)
        if not existing_report:
            logger.error(f"Report {report_id} not found")
            return False

        # Update the report with new data
        updated = False

        # Handle conversation updates
        if "conversation" in new_data:
            # Find the questions_evaluated section that contains the conversation
            if "questions_evaluated" in existing_report:
                for q_eval in existing_report["questions_evaluated"]:
                    if "conversation" in q_eval:
                        # Compare and update conversation turns
                        existing_turns = {
                            (turn.get("speaker"), turn.get("text")): turn
                            for turn in q_eval["conversation"]
                        }

                        for new_turn in new_data["conversation"]:
                            turn_key = (new_turn.get("speaker"), new_turn.get("text"))

                            # If turn exists but doesn't have audio_url, update it
                            if (
                                turn_key in existing_turns
                                and not existing_turns[turn_key].get("audio_url")
                                and new_turn.get("audio_url")
                            ):
                                # Find the index of this turn in the original list
                                for i, turn in enumerate(q_eval["conversation"]):
                                    if (
                                        turn.get("speaker") == turn_key[0]
                                        and turn.get("text") == turn_key[1]
                                    ):
                                        q_eval["conversation"][i]["audio_url"] = (
                                            new_turn.get("audio_url")
                                        )
                                        updated = True
                                        break

                            # If turn doesn't exist, add it
                            elif turn_key not in existing_turns:
                                q_eval["conversation"].append(new_turn)
                                updated = True

        # Save the updated report if changes were made
        if updated:
            s3_service.save_report(existing_report, report_id)

            # Clear the report from cache to force a refresh
            if (
                hasattr(reporting_service, "cached_reports")
                and report_id in reporting_service.cached_reports
            ):
                del reporting_service.cached_reports[report_id]

            logger.info(f"Report {report_id} updated successfully")
            return True
        else:
            logger.info(f"No changes needed for report {report_id}")
            return False

    except Exception as e:
        logger.error(f"Error updating report {report_id}: {str(e)}")
        import traceback

        logger.error(f"Traceback: {traceback.format_exc()}")
        return False


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

            # Get conversation
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
                logger.error(f"No conversation data for test {test_id}")
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
            logger.error(f"Test {test_id} not found in memory for completed call")

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
                logger.error(f"Test {test_id} not found in DynamoDB for completed call")
    except Exception as e:
        logger.error(f"Error processing completed call: {str(e)}")
        import traceback

        logger.error(f"Traceback: {traceback.format_exc()}")
