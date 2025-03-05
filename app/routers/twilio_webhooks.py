# app/routers/twilio_webhooks.py - Complete Implementation

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

from ..services.twilio_service import twilio_service
from ..services.evaluator import evaluator_service
from ..services.s3_service import s3_service
from ..services.openai_service import openai_service
from ..config import config
from ..utils.audio import convert_mp3_to_wav, trim_silence

router = APIRouter(
    prefix="/webhooks",
    tags=["webhooks"],
    responses={404: {"description": "Not found"}},
)

logger = logging.getLogger(__name__)

# Store active WebSocket connections
active_websockets = {}


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

    # Extract test_id from query parameters - this is crucial
    test_id = request.query_params.get("test_id")

    # If not in query params, try form data
    if not test_id:
        test_id = form_data.get("test_id")

    logger.error(
        f"DEBUG: Call started webhook received - CallSid: {call_sid}, test_id: {test_id}"
    )
    logger.error(f"DEBUG: Request URL: {request.url}")
    logger.error(f"DEBUG: Query params: {request.query_params}")
    logger.error(f"DEBUG: Form data keys: {form_data.keys()}")

    # Dump all active tests to the logs for debugging
    logger.error(f"DEBUG: Active tests dump: {evaluator_service.active_tests}")

    # Check if the test is already in memory
    test_in_memory = test_id in evaluator_service.active_tests
    logger.error(f"DEBUG: Test {test_id} found in memory: {test_in_memory}")

    # If not in memory, check DynamoDB
    if not test_in_memory and test_id:
        logger.error(f"DEBUG: Test {test_id} not in memory, checking DynamoDB")
        from ..services.dynamodb_service import dynamodb_service

        test_data = dynamodb_service.get_test(test_id)

        if test_data:
            logger.error(f"DEBUG: Test {test_id} found in DynamoDB")
            # Load the test into memory
            evaluator_service.active_tests[test_id] = test_data
            test_in_memory = True
            logger.error(f"DEBUG: Loaded test {test_id} from DynamoDB into memory")
            logger.error(f"DEBUG: Test data: {test_data}")
        else:
            logger.error(f"DEBUG: Test {test_id} not found in DynamoDB")

    # If still not found, try to find a waiting test
    if not test_id or not test_in_memory:
        logger.error(
            "DEBUG: No test_id provided or test not found, searching for waiting tests in DynamoDB"
        )

        from ..services.dynamodb_service import dynamodb_service

        waiting_tests = dynamodb_service.get_waiting_tests()

        logger.error(f"DEBUG: Found {len(waiting_tests)} waiting tests in DynamoDB")

        if waiting_tests:
            # Use the first waiting test
            waiting_test = waiting_tests[0]
            test_id = waiting_test["test_id"]
            test_data = waiting_test["test_data"]

            # Load into memory
            evaluator_service.active_tests[test_id] = test_data
            test_in_memory = True

            logger.error(
                f"DEBUG: Selected test_id: {test_id} from waiting tests in DynamoDB"
            )
            logger.error(f"DEBUG: Loaded waiting test into memory: {test_id}")

    # Also check Twilio's active_calls to find the test_id as last resort
    if not test_id or not test_in_memory:
        if call_sid in twilio_service.active_calls:
            test_id = twilio_service.active_calls[call_sid].get("test_id")
            logger.error(
                f"DEBUG: Found test_id: {test_id} in twilio_service.active_calls"
            )

            # Try to load from DynamoDB again
            if test_id:
                from ..services.dynamodb_service import dynamodb_service

                test_data = dynamodb_service.get_test(test_id)

                if test_data:
                    evaluator_service.active_tests[test_id] = test_data
                    test_in_memory = True
                    logger.error(
                        f"DEBUG: Loaded test {test_id} from DynamoDB via Twilio active_calls"
                    )

    # Check if there's an active test now
    if test_id and test_id in evaluator_service.active_tests:
        logger.error(f"DEBUG: Using test_id: {test_id} for call {call_sid}")

        # Update test status
        previous_status = evaluator_service.active_tests[test_id].get(
            "status", "unknown"
        )
        evaluator_service.active_tests[test_id]["status"] = "in_progress"
        evaluator_service.active_tests[test_id]["call_sid"] = call_sid
        logger.error(
            f"DEBUG: Updated test {test_id} status from {previous_status} to in_progress"
        )

        # Update status in DynamoDB
        from ..services.dynamodb_service import dynamodb_service

        dynamodb_service.update_test_status(test_id, "in_progress")
        dynamodb_service.save_test(test_id, evaluator_service.active_tests[test_id])
        logger.error(f"DEBUG: Updated test status in DynamoDB to in_progress")

        # Get the callback URL from config for logging
        callback_url = config.get_parameter("/ai-evaluator/twilio_callback_url")
        logger.error(f"DEBUG: Using callback URL: {callback_url}")

        # Generate TwiML for media streaming
        try:
            twiml = twilio_service.generate_stream_twiml(test_id, call_sid)
            logger.error(f"DEBUG: Generated TwiML for media streaming: {twiml}")
            return HTMLResponse(content=twiml, media_type="application/xml")
        except Exception as e:
            logger.error(f"DEBUG: Error generating TwiML: {str(e)}")
            import traceback

            logger.error(f"DEBUG: Traceback: {traceback.format_exc()}")
            return HTMLResponse(
                content="<Response><Say>Error generating TwiML. Goodbye.</Say><Hangup/></Response>",
                media_type="application/xml",
            )
    else:
        # Default response if no active test is found
        logger.error(
            f"DEBUG: No active test found for call {call_sid}, returning goodbye message"
        )
        return HTMLResponse(
            content="<Response><Say>No active test found. Goodbye.</Say><Hangup/></Response>",
            media_type="application/xml",
        )


# @router.websocket("/media-stream")
# async def media_stream(websocket: WebSocket):
#     """
#     Handle WebSocket connection for media streaming.
#     This handler implements an evaluator that asks questions, records responses,
#     and evaluates the call center representative.
#     """
#     await websocket.accept()

#     logger.error("DEBUG: WebSocket connection accepted for media stream")

#     # Extract query parameters
#     query_params = websocket.query_params
#     test_id = query_params.get("test_id")
#     call_sid = query_params.get("call_sid")

#     logger.error(f"DEBUG: WebSocket params - test_id: {test_id}, call_sid: {call_sid}")

#     if not test_id or not call_sid:
#         logger.error("DEBUG: Missing test_id or call_sid in WebSocket connection")
#         await websocket.close(code=1000)
#         return

#     # Check if test exists
#     if test_id not in evaluator_service.active_tests:
#         logger.error(f"DEBUG: Test {test_id} not found in active tests")
#         await websocket.close(code=1000)
#         return

#     logger.error(f"DEBUG: Found active test {test_id} for WebSocket connection")

#     # Store the websocket connection
#     active_websockets[call_sid] = websocket

#     # Get test data
#     test_data = evaluator_service.active_tests[test_id]
#     test_case = test_data.get("test_case", {})
#     logger.error(
#         f"DEBUG: Retrieved test case data: {test_case.get('name')}. Questions: {test_case.get('config', {}).get('questions', [])}"
#     )

#     # Initialize conversation data
#     stream_sid = None
#     current_question_index = 0
#     conversation = []
#     agent_speaking = False
#     last_agent_speech_time = None
#     question_answered = False
#     question_started_time = None
#     introduction_complete = False

#     try:
#         # Process the media stream
#         async for message in websocket.iter_text():
#             try:
#                 data = json.loads(message)
#                 event_type = data.get("event")
#                 logger.error(f"DEBUG: Received WebSocket message: {event_type}")

#                 # Handle Twilio events
#                 if event_type == "start":
#                     stream_sid = data["start"]["streamSid"]
#                     logger.error(f"DEBUG: Media stream started: {stream_sid}")

#                     # Start the introduction sequence
#                     await asyncio.sleep(1)

#                     # Say the introduction
#                     intro_text = "Starting evaluation call."
#                     logger.error(f"DEBUG: Sending introduction: {intro_text}")

#                     try:
#                         # Send introduction audio
#                         intro_audio_data = await generate_audio_from_text(intro_text)
#                         await send_audio_chunks(websocket, stream_sid, intro_audio_data)
#                         logger.error("DEBUG: Introduction audio sent successfully")

#                         # Wait for the introduction to be spoken
#                         await asyncio.sleep(2)

#                         # Check if special instructions should be included
#                         special_instructions = test_case.get("config", {}).get(
#                             "special_instructions"
#                         )
#                         if special_instructions:
#                             logger.error(
#                                 f"DEBUG: Including special instructions: {special_instructions}"
#                             )
#                             special_text = (
#                                 f"Special instructions: {special_instructions}"
#                             )

#                             # Send special instructions audio
#                             special_audio_data = await generate_audio_from_text(
#                                 special_text
#                             )
#                             await send_audio_chunks(
#                                 websocket, stream_sid, special_audio_data
#                             )
#                             logger.error(
#                                 "DEBUG: Special instructions audio sent successfully"
#                             )

#                             # Record the special instructions
#                             evaluator_service.record_conversation_turn(
#                                 test_id=test_id,
#                                 call_sid=call_sid,
#                                 speaker="evaluator",
#                                 text=special_text,
#                             )
#                             conversation.append(
#                                 {
#                                     "speaker": "evaluator",
#                                     "text": special_text,
#                                     "timestamp": datetime.now().isoformat(),
#                                 }
#                             )

#                             # Wait for special instructions to be spoken
#                             await asyncio.sleep(2)

#                         # Mark introduction as complete
#                         introduction_complete = True
#                         logger.error("DEBUG: Introduction sequence completed")

#                         # Now send the first question
#                         questions = test_case.get("config", {}).get("questions", [])
#                         logger.error(
#                             f"DEBUG: Test has {len(questions)} questions: {questions}"
#                         )

#                         if questions and current_question_index < len(questions):
#                             first_question = questions[current_question_index]
#                             if isinstance(first_question, dict):
#                                 first_question = first_question.get("text", "")

#                             logger.error(
#                                 f"DEBUG: First question to ask: {first_question}"
#                             )

#                             # Record the question
#                             evaluator_service.record_conversation_turn(
#                                 test_id=test_id,
#                                 call_sid=call_sid,
#                                 speaker="evaluator",
#                                 text=first_question,
#                             )
#                             conversation.append(
#                                 {
#                                     "speaker": "evaluator",
#                                     "text": first_question,
#                                     "timestamp": datetime.now().isoformat(),
#                                 }
#                             )

#                             # Send question audio
#                             question_audio_data = await generate_audio_from_text(
#                                 first_question
#                             )
#                             await send_audio_chunks(
#                                 websocket, stream_sid, question_audio_data
#                             )
#                             logger.error(
#                                 "DEBUG: First question audio sent successfully"
#                             )

#                             current_question_index += 1
#                             question_answered = False
#                             question_started_time = time.time()
#                         else:
#                             logger.error(
#                                 f"DEBUG: No questions found in test case or questions array is empty"
#                             )
#                     except Exception as audio_error:
#                         logger.error(
#                             f"DEBUG: Error sending introduction or first question: {str(audio_error)}"
#                         )
#                         import traceback

#                         logger.error(f"DEBUG: Traceback: {traceback.format_exc()}")

#                 # Handle media event (incoming audio from agent)
#                 elif event_type == "media" and stream_sid:
#                     # Check if we're getting media from the agent
#                     if not agent_speaking:
#                         agent_speaking = True
#                         last_agent_speech_time = time.time()
#                         logger.error(f"DEBUG: Agent started speaking")
#                     else:
#                         # Update the last speech time
#                         last_agent_speech_time = time.time()

#                 # Handle mark event
#                 elif event_type == "mark":
#                     mark_name = data.get("mark", {}).get("name")
#                     logger.error(f"DEBUG: Received mark: {mark_name}")

#                 # Handle stop event
#                 elif event_type == "stop":
#                     logger.error(f"DEBUG: Media stream stopped: {stream_sid}")
#                     break

#                 # Check for agent silence to determine if answer is complete
#                 current_time = time.time()
#                 if (
#                     agent_speaking
#                     and last_agent_speech_time
#                     and (current_time - last_agent_speech_time) > 2.0
#                 ):
#                     # Agent has been silent for more than 2 seconds, consider the answer complete
#                     agent_speaking = False
#                     logger.error(f"DEBUG: Agent stopped speaking, processing answer")

#                     # In a real implementation, we'd transcribe the complete agent response here
#                     # For now, we'll simulate it with a placeholder
#                     agent_response = f"Simulated agent response for question {current_question_index}"

#                     # Record the agent's response
#                     evaluator_service.record_conversation_turn(
#                         test_id=test_id,
#                         call_sid=call_sid,
#                         speaker="agent",
#                         text=agent_response,
#                     )
#                     conversation.append(
#                         {
#                             "speaker": "agent",
#                             "text": agent_response,
#                             "timestamp": datetime.now().isoformat(),
#                         }
#                     )

#                     logger.error(f"DEBUG: Recorded agent response: {agent_response}")

#                     # Mark question as answered
#                     question_answered = True

#                     # Process next steps
#                     next_question_index = await process_next_steps(
#                         websocket,
#                         test_id,
#                         call_sid,
#                         stream_sid,
#                         current_question_index,
#                         test_case,
#                         conversation,
#                     )
#                     if next_question_index is not None:
#                         current_question_index = next_question_index

#                 # Check if current question has timed out
#                 if (
#                     introduction_complete
#                     and question_started_time
#                     and not question_answered
#                     and (current_time - question_started_time) > 30.0
#                 ):
#                     # Question has timed out after 30 seconds with no response
#                     logger.error(
#                         f"DEBUG: Question timed out after 30 seconds with no response"
#                     )

#                     # Record a timeout
#                     evaluator_service.record_conversation_turn(
#                         test_id=test_id,
#                         call_sid=call_sid,
#                         speaker="agent",
#                         text="[No response - timeout]",
#                     )
#                     conversation.append(
#                         {
#                             "speaker": "agent",
#                             "text": "[No response - timeout]",
#                             "timestamp": datetime.now().isoformat(),
#                         }
#                     )

#                     # Reset timer and mark as answered to move on
#                     question_answered = True
#                     question_started_time = None

#                     # Process next steps
#                     next_question_index = await process_next_steps(
#                         websocket,
#                         test_id,
#                         call_sid,
#                         stream_sid,
#                         current_question_index,
#                         test_case,
#                         conversation,
#                     )
#                     if next_question_index is not None:
#                         current_question_index = next_question_index

#             except json.JSONDecodeError as e:
#                 logger.error(f"DEBUG: Received invalid JSON: {str(e)}")
#             except Exception as e:
#                 logger.error(f"DEBUG: Error processing message: {str(e)}")
#                 import traceback

#                 logger.error(f"DEBUG: Traceback: {traceback.format_exc()}")

#     except WebSocketDisconnect:
#         logger.error(f"DEBUG: WebSocket disconnected")
#     except Exception as e:
#         logger.error(f"DEBUG: Error in media stream: {str(e)}")
#         import traceback

#         logger.error(f"DEBUG: Traceback: {traceback.format_exc()}")
#     finally:
#         # Clean up
#         if call_sid in active_websockets:
#             del active_websockets[call_sid]

#         # Finalize the test
#         if test_id in evaluator_service.active_tests:
#             # Get conversation from evaluator service
#             conversation = evaluator_service.active_tests[test_id].get(
#                 "conversation", conversation
#             )

#             if not conversation:
#                 logger.error(f"DEBUG: No conversation recorded for test {test_id}")

#                 # Add at least one placeholders if none exists
#                 evaluator_service.record_conversation_turn(
#                     test_id=test_id,
#                     call_sid=call_sid,
#                     speaker="evaluator",
#                     text="No questions were successfully asked during the call.",
#                 )

#                 evaluator_service.record_conversation_turn(
#                     test_id=test_id,
#                     call_sid=call_sid,
#                     speaker="agent",
#                     text="No agent responses were recorded.",
#                 )

#                 # Refresh conversation
#                 conversation = evaluator_service.active_tests[test_id].get(
#                     "conversation", []
#                 )

#             # Process the call
#             test_data["status"] = "processing"
#             test_data["end_time"] = time.time()

#             # Generate report
#             await evaluator_service.process_call(test_id, call_sid, conversation)

#             logger.error(f"DEBUG: Test {test_id} completed")


@router.websocket("/media-stream")
async def media_stream(websocket: WebSocket):
    """
    Simplified media stream handler to test basic functionality.
    This version uses direct string encoding for immediate audio feedback.
    """
    try:
        await websocket.accept()
        logging.error("DEBUG: WebSocket connection accepted")

        async def test_openai_tts():
            """Test OpenAI TTS directly and log the results."""
            try:
                logger.error("TTS TEST: Testing OpenAI TTS directly")
                from ..services.openai_service import openai_service

                # Test basic functionality
                test_text = "This is a test of the OpenAI text to speech system."

                # Log API key info (safely)
                api_key = openai_service.api_key
                if api_key:
                    masked_key = (
                        f"{api_key[:5]}...{api_key[-4:]}"
                        if len(api_key) > 10
                        else "***"
                    )
                    logger.error(f"TTS TEST: Using OpenAI API key: {masked_key}")
                else:
                    logger.error("TTS TEST: No OpenAI API key found!")

                # Time the API call
                start_time = time.time()
                audio = await openai_service.text_to_speech(test_text)
                duration = time.time() - start_time

                if audio:
                    logger.error(
                        f"TTS TEST: Successfully generated audio ({len(audio)} bytes) in {duration:.2f} seconds"
                    )
                    logger.error(f"TTS TEST: First 20 bytes: {audio[:20].hex()}")
                    return True
                else:
                    logger.error("TTS TEST: Received empty audio response")
                    return False
            except Exception as e:
                logger.error(f"TTS TEST ERROR: {str(e)}")
                import traceback

                logger.error(f"TTS TEST TRACEBACK: {traceback.format_exc()}")
                return False

        logger.error("DEBUG: WebSocket connection accepted for media stream")

        # Extract query parameters
        query_params = websocket.query_params
        test_id = query_params.get("test_id")
        call_sid = query_params.get("call_sid")

        test_in_memory = test_id in evaluator_service.active_tests

        # If not in memory, try to load from DynamoDB
        if not test_in_memory:
            logger.error(f"DEBUG: Test {test_id} not found in memory, trying DynamoDB")
            from ..services.dynamodb_service import dynamodb_service

            test_data = dynamodb_service.get_test(test_id)

            if test_data:
                logger.error(
                    f"DEBUG: Test {test_id} found in DynamoDB, loading into memory"
                )
                evaluator_service.active_tests[test_id] = test_data
                test_in_memory = True
            else:
                logger.error(f"DEBUG: Test {test_id} not found in DynamoDB")
        logger.error(
            f"DEBUG: WebSocket params - test_id: {test_id}, call_sid: {call_sid}"
        )

        if not test_id or not call_sid:
            logger.error("DEBUG: Missing test_id or call_sid in WebSocket connection")
            await websocket.close(code=1000)
            return

        # Check if test exists
        if test_id not in evaluator_service.active_tests:
            logger.error(f"DEBUG: Test {test_id} not found in active tests")
            await websocket.close(code=1000)
            return

        # Get test data
        test_data = evaluator_service.active_tests[test_id]
        test_case = test_data.get("test_case", {})

        # Log test questions for debugging
        questions = test_case.get("config", {}).get("questions", [])
        for i, q in enumerate(questions):
            if isinstance(q, dict):
                logger.error(f"DEBUG: Question {i+1}: {q.get('text', 'No text')}")
            else:
                logger.error(f"DEBUG: Question {i+1}: {q}")

        # Initialize variables
        stream_sid = None
        messages_processed = 0
        introduction_sent = False
        question_sent = False

        try:
            # Run diagnostic tests
            await diagnose_audio_issues()
            await test_openai_tts()

            # Process the media stream
            async for message in websocket.iter_text():
                try:
                    data = json.loads(message)
                    event_type = data.get("event")
                    messages_processed += 1

                    logger.error(
                        f"DEBUG: Received WebSocket message #{messages_processed}: {event_type}"
                    )

                    # Handle Twilio stream start
                    if event_type == "start" and not introduction_sent:
                        stream_sid = data["start"]["streamSid"]
                        logger.error(f"DEBUG: Stream started with SID: {stream_sid}")

                        # Send intro text directly using encoder
                        intro_text = "Starting evaluation call. Testing one two three."
                        logger.error(f"DEBUG: Sending intro text: {intro_text}")

                        # Use simple audio encoding directly in the handler
                        audio_bytes = intro_text.encode("utf-8")
                        encoded_audio = base64.b64encode(audio_bytes).decode("utf-8")

                        # Send as media
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "event": "media",
                                    "streamSid": stream_sid,
                                    "media": {"payload": encoded_audio},
                                }
                            )
                        )
                        logger.error("DEBUG: Sent introduction directly")

                        # Wait a moment
                        await asyncio.sleep(2)

                        # Mark introduction as sent
                        introduction_sent = True

                    # Once introduction is sent, proceed to first question
                    if introduction_sent and stream_sid and not question_sent:
                        # Get the first question
                        first_question = None
                        if questions:
                            first_question = questions[0]
                            if isinstance(first_question, dict):
                                first_question = first_question.get(
                                    "text", "No question text found."
                                )
                        else:
                            first_question = "No questions found in test."

                        logger.error(f"DEBUG: Sending first question: {first_question}")

                        # Use simple audio encoding directly
                        question_bytes = first_question.encode("utf-8")
                        encoded_question = base64.b64encode(question_bytes).decode(
                            "utf-8"
                        )

                        # Send as media
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "event": "media",
                                    "streamSid": stream_sid,
                                    "media": {"payload": encoded_question},
                                }
                            )
                        )
                        logger.error("DEBUG: Sent first question directly")

                        # Mark question as sent
                        question_sent = True

                        # Record the question
                        evaluator_service.record_conversation_turn(
                            test_id=test_id,
                            call_sid=call_sid,
                            speaker="evaluator",
                            text=first_question,
                        )

                        # Keep connection open to allow response
                        await asyncio.sleep(5)  # Wait for potential response

                        # Send goodbye
                        goodbye_text = "This concludes our test. Thank you."
                        goodbye_bytes = goodbye_text.encode("utf-8")
                        encoded_goodbye = base64.b64encode(goodbye_bytes).decode(
                            "utf-8"
                        )

                        await websocket.send_text(
                            json.dumps(
                                {
                                    "event": "media",
                                    "streamSid": stream_sid,
                                    "media": {"payload": encoded_goodbye},
                                }
                            )
                        )
                        logger.error("DEBUG: Sent goodbye directly")

                        # Wait briefly before ending
                        await asyncio.sleep(2)

                        # End the call
                        twilio_service.end_call(call_sid)
                        logger.error("DEBUG: Call ended")

                    # Handle stop event
                    if event_type == "stop":
                        logger.error(f"DEBUG: Stream stopped.")
                        break

                except json.JSONDecodeError as e:
                    logger.error(f"DEBUG: Invalid JSON: {str(e)}")
                except Exception as e:
                    logger.error(f"DEBUG: Error processing message: {str(e)}")
                    import traceback

                    logger.error(f"DEBUG: Traceback: {traceback.format_exc()}")

        except WebSocketDisconnect:
            logger.error("DEBUG: WebSocket disconnected")
        except Exception as e:
            logger.error(f"DEBUG: General error: {str(e)}")
            import traceback

            logger.error(f"DEBUG: Traceback: {traceback.format_exc()}")
        finally:
            # Cleanup
            if call_sid in active_websockets:
                del active_websockets[call_sid]

            # Log summary
            logger.error(
                f"DEBUG: Media stream ended. Total messages processed: {messages_processed}"
            )
            logger.error(
                f"DEBUG: Introduction sent: {introduction_sent}, Question sent: {question_sent}"
            )

            # Finalize test
            if test_id in evaluator_service.active_tests:
                test_data["status"] = "completed"
                test_data["end_time"] = time.time()

                # Add a simple report entry if needed
                conversation = evaluator_service.active_tests[test_id].get(
                    "conversation", []
                )
                if not conversation:
                    evaluator_service.record_conversation_turn(
                        test_id=test_id,
                        call_sid=call_sid,
                        speaker="evaluator",
                        text="System message: Test completed with simplified handler.",
                    )

                # Generate report
                conversation = evaluator_service.active_tests[test_id].get(
                    "conversation", []
                )
                await evaluator_service.process_call(test_id, call_sid, conversation)

                logger.error(f"DEBUG: Test {test_id} completed")
    except WebSocketDisconnect:
        print(f"WebSocket disconnected")
    except Exception as e:
        print(f"WebSocket error: {str(e)}")
        import traceback

        print(f"WebSocket traceback: {traceback.format_exc()}")


async def diagnose_audio_issues():
    """
    Run a series of diagnostic tests to identify audio generation and transmission issues.
    This should be called early in your media_stream function.
    """
    logger.error("===== AUDIO DIAGNOSTICS STARTING =====")

    # Test 1: Check if OpenAI is accessible
    try:
        from ..services.openai_service import openai_service

        api_key = openai_service.api_key
        logger.error(
            f"TEST 1: OpenAI API key exists: {bool(api_key)} (first 5 chars: {api_key[:5] if api_key else 'None'})"
        )
    except Exception as e:
        logger.error(f"TEST 1 FAILED: Error accessing OpenAI service: {str(e)}")

    # Test 2: Generate a simple test audio
    try:
        test_text = "This is a test message."
        logger.error(f"TEST 2: Attempting to generate audio for: '{test_text}'")

        from ..services.openai_service import openai_service

        start_time = time.time()
        audio_data = await openai_service.text_to_speech(test_text)
        duration = time.time() - start_time

        logger.error(
            f"TEST 2 SUCCESS: Generated audio of size {len(audio_data)} bytes in {duration:.2f} seconds"
        )

        # Verify audio data structure
        logger.error(f"TEST 2 DATA: Audio starts with bytes: {audio_data[:20].hex()}")
    except Exception as e:
        logger.error(f"TEST 2 FAILED: Error generating test audio: {str(e)}")
        import traceback

        logger.error(f"TEST 2 TRACEBACK: {traceback.format_exc()}")

    # Test 3: Generate a longer text
    try:
        longer_text = "This is a longer test message that would be similar to a test question. Can you hear this audio properly?"
        logger.error(
            f"TEST 3: Attempting to generate audio for longer text: '{longer_text}'"
        )

        from ..services.openai_service import openai_service

        start_time = time.time()
        audio_data = await openai_service.text_to_speech(longer_text)
        duration = time.time() - start_time

        logger.error(
            f"TEST 3 SUCCESS: Generated audio of size {len(audio_data)} bytes in {duration:.2f} seconds"
        )
    except Exception as e:
        logger.error(f"TEST 3 FAILED: Error generating longer audio: {str(e)}")

    # Test 4: Test fallback audio generation
    try:
        logger.error("TEST 4: Testing fallback audio generation")

        import struct
        import math

        # Generate 1 second of a simple tone
        rate = 8000
        duration = 1.0
        samples = int(rate * duration)
        audio_data = bytearray()

        # Add a WAV header
        audio_data.extend(b"RIFF")
        audio_data.extend(struct.pack("<I", 36 + samples))
        audio_data.extend(b"WAVE")
        audio_data.extend(b"fmt ")
        audio_data.extend(struct.pack("<I", 16))
        audio_data.extend(struct.pack("<H", 1))
        audio_data.extend(struct.pack("<H", 1))
        audio_data.extend(struct.pack("<I", rate))
        audio_data.extend(struct.pack("<I", rate))
        audio_data.extend(struct.pack("<H", 1))
        audio_data.extend(struct.pack("<H", 8))
        audio_data.extend(b"data")
        audio_data.extend(struct.pack("<I", samples))

        # Generate simple tone
        for i in range(samples):
            value = int(127 + 127 * math.sin(2 * math.pi * 440 * i / rate))
            audio_data.append(value & 0xFF)

        logger.error(
            f"TEST 4 SUCCESS: Generated fallback audio of size {len(audio_data)} bytes"
        )
        logger.error(
            f"TEST 4 DATA: Fallback audio starts with bytes: {bytes(audio_data)[:20].hex()}"
        )
    except Exception as e:
        logger.error(f"TEST 4 FAILED: Error generating fallback audio: {str(e)}")

    logger.error("===== AUDIO DIAGNOSTICS COMPLETE =====")


async def generate_audio_from_text(text):
    """Generate audio from text using OpenAI's TTS or a simpler approach."""
    try:
        # Use OpenAI's TTS API if available
        from ..services.openai_service import openai_service

        audio = await openai_service.text_to_speech(text)
        logger.error(f"DEBUG: Successfully generated OpenAI audio for: {text[:50]}...")
        return audio
    except Exception as e:
        logger.error(f"DEBUG: Error using OpenAI TTS: {str(e)}")

        # Fall back to simpler audio generation for testing
        # This is just for testing - in production, use a proper TTS service
        import struct
        import math

        # Generate 1 second of a simple tone (8kHz, mono, 8-bit)
        rate = 8000
        duration = len(text) * 0.1  # roughly scale duration with text length
        samples = int(rate * duration)
        audio_data = bytearray()

        # Add a WAV header
        audio_data.extend(b"RIFF")
        audio_data.extend(struct.pack("<I", 36 + samples))
        audio_data.extend(b"WAVE")
        audio_data.extend(b"fmt ")
        audio_data.extend(struct.pack("<I", 16))  # Size of fmt chunk
        audio_data.extend(struct.pack("<H", 1))  # PCM format
        audio_data.extend(struct.pack("<H", 1))  # Mono
        audio_data.extend(struct.pack("<I", rate))  # Sample rate
        audio_data.extend(struct.pack("<I", rate))  # Byte rate
        audio_data.extend(struct.pack("<H", 1))  # Block align
        audio_data.extend(struct.pack("<H", 8))  # Bits per sample
        audio_data.extend(b"data")
        audio_data.extend(struct.pack("<I", samples))

        # Generate simple tone at 440Hz
        for i in range(samples):
            value = int(127 + 127 * math.sin(2 * math.pi * 440 * i / rate))
            audio_data.append(value & 0xFF)

        logger.error(f"DEBUG: Generated fallback audio for: {text[:50]}...")
        return bytes(audio_data)


async def send_audio_chunks(websocket, stream_sid, audio_data):
    """Send audio in chunks through the websocket with detailed logging."""
    logger.error(
        f"AUDIO SEND: Starting to send audio of size {len(audio_data)} bytes through websocket"
    )

    # Log audio format details
    audio_format = "unknown"
    if (
        len(audio_data) > 12
        and audio_data[:4] == b"RIFF"
        and audio_data[8:12] == b"WAVE"
    ):
        audio_format = "WAV"
    elif len(audio_data) > 2 and audio_data[:2] == b"\xFF\xFB":
        audio_format = "MP3"
    logger.error(f"AUDIO SEND: Detected format appears to be {audio_format}")

    # Split the audio data into smaller chunks for reliable transmission
    chunk_size = 320  # Smaller chunks for more reliable transmission
    total_chunks = (len(audio_data) + chunk_size - 1) // chunk_size
    chunks_sent = 0

    logger.error(
        f"AUDIO SEND: Will send {total_chunks} chunks of {chunk_size} bytes each"
    )

    try:
        for i in range(0, len(audio_data), chunk_size):
            chunk = audio_data[i : i + chunk_size]
            chunks_sent += 1

            # Encode the chunk for Twilio
            audio_payload = base64.b64encode(chunk).decode("utf-8")

            # Create the media message
            media_message = {
                "event": "media",
                "streamSid": stream_sid,
                "media": {"payload": audio_payload},
            }

            # Send the chunk and log every 10th chunk
            await websocket.send_text(json.dumps(media_message))
            if chunks_sent % 10 == 0 or chunks_sent == total_chunks:
                logger.error(f"AUDIO SEND: Sent chunk {chunks_sent}/{total_chunks}")

            # Small delay to prevent flooding
            await asyncio.sleep(0.01)

        logger.error(f"AUDIO SEND: Successfully sent all {chunks_sent} audio chunks")

        # Add a small delay before sending the mark
        await asyncio.sleep(0.2)

        # Send a mark to indicate the audio is complete
        mark_message = {
            "event": "mark",
            "streamSid": stream_sid,
            "mark": {"name": "audio_complete"},
        }
        await websocket.send_text(json.dumps(mark_message))
        logger.error(f"AUDIO SEND: Sent audio_complete mark")

        return True
    except Exception as e:
        logger.error(f"AUDIO SEND ERROR: Failed to send audio: {str(e)}")
        import traceback

        logger.error(f"AUDIO SEND TRACEBACK: {traceback.format_exc()}")
        return False


async def process_next_steps(
    websocket,
    test_id,
    call_sid,
    stream_sid,
    current_question_index,
    test_case,
    conversation,
):
    """Process the next steps in the conversation based on the test configuration."""
    # Check if we should ask the next question or end the call
    max_questions = len(test_case.get("config", {}).get("questions", []))
    max_turns = test_case.get("config", {}).get("max_turns", 8)

    logger.error(
        f"DEBUG: Processing next steps. Question {current_question_index}/{max_questions}, Turn {len(conversation)}/{max_turns}"
    )

    if current_question_index < max_questions and len(conversation) < max_turns:
        # Ask the next question
        questions = test_case.get("config", {}).get("questions", [])
        next_question = questions[current_question_index]
        if isinstance(next_question, dict):
            next_question = next_question.get("text", "")

        logger.error(f"DEBUG: Asking next question: {next_question}")

        # Record the question
        evaluator_service.record_conversation_turn(
            test_id=test_id, call_sid=call_sid, speaker="evaluator", text=next_question
        )
        conversation.append(
            {
                "speaker": "evaluator",
                "text": next_question,
                "timestamp": datetime.now().isoformat(),
            }
        )

        # Add a small pause before the next question
        await asyncio.sleep(1)

        try:
            # Generate and send the question audio
            question_audio_data = await generate_audio_from_text(next_question)
            await send_audio_chunks(websocket, stream_sid, question_audio_data)
            logger.error(f"DEBUG: Next question audio sent successfully")
        except Exception as e:
            logger.error(f"DEBUG: Error sending next question audio: {str(e)}")
            import traceback

            logger.error(f"DEBUG: Traceback: {traceback.format_exc()}")

        # Return the updated question index
        return current_question_index + 1
    else:
        # All questions asked or max turns reached, end the call
        logger.error(f"DEBUG: All questions asked or max turns reached, ending call")

        # Say goodbye
        goodbye_text = "Thank you for your time. This concludes our evaluation."
        evaluator_service.record_conversation_turn(
            test_id=test_id, call_sid=call_sid, speaker="evaluator", text=goodbye_text
        )
        conversation.append(
            {
                "speaker": "evaluator",
                "text": goodbye_text,
                "timestamp": datetime.now().isoformat(),
            }
        )

        try:
            # Generate and send the goodbye audio
            goodbye_audio_data = await generate_audio_from_text(goodbye_text)
            await send_audio_chunks(websocket, stream_sid, goodbye_audio_data)
            logger.error(f"DEBUG: Goodbye audio sent successfully")
        except Exception as e:
            logger.error(f"DEBUG: Error sending goodbye audio: {str(e)}")

        # Wait for goodbye to complete
        await asyncio.sleep(3)

        # End the call
        twilio_service.end_call(call_sid)
        logger.error(f"DEBUG: Call ended")

        return current_question_index


async def speak_text(websocket, text, stream_sid):
    """Convert text to speech and send it through the websocket."""
    try:
        logger.error(f"DEBUG: Converting text to speech: {text}")

        # Use OpenAI to generate audio (or use a simpler approach for testing)
        audio_data = await generate_audio_from_text(text)

        # Split the audio data into chunks and send through websocket
        chunk_size = 1024  # Adjust as needed
        for i in range(0, len(audio_data), chunk_size):
            chunk = audio_data[i : i + chunk_size]

            # Encode the chunk for Twilio
            audio_payload = base64.b64encode(chunk).decode("utf-8")

            # Create the media message
            media_message = {
                "event": "media",
                "streamSid": stream_sid,
                "media": {"payload": audio_payload},
            }

            # Send the chunk
            await websocket.send_text(json.dumps(media_message))

            # Small delay to prevent flooding
            await asyncio.sleep(0.01)

        logger.error(f"DEBUG: Finished sending audio for text")

        # Send a mark to indicate the speech is complete
        mark_message = {
            "event": "mark",
            "streamSid": stream_sid,
            "mark": {"name": "speech_complete"},
        }
        await websocket.send_text(json.dumps(mark_message))

    except Exception as e:
        logger.error(f"DEBUG: Error in speak_text: {str(e)}")
        import traceback

        logger.error(f"DEBUG: Traceback: {traceback.format_exc()}")


async def generate_audio_from_text(text):
    """Generate audio from text using OpenAI's TTS or a simpler approach."""
    try:
        # Use OpenAI's TTS (text-to-speech) API
        audio = await openai_service.text_to_speech(text)
        return audio
    except Exception as e:
        logger.error(f"DEBUG: Error generating audio from text: {str(e)}")

        # Fallback: Create a simple audio placeholder
        # This is just a placeholder - in production, use a proper TTS service
        import struct
        import math

        # Generate 1 second of silence (8kHz, mono, 8-bit)
        rate = 8000
        duration = 1.0  # seconds
        samples = int(rate * duration)
        audio_data = bytearray()

        # Add a WAV header
        audio_data.extend(b"RIFF")
        audio_data.extend(struct.pack("<I", 36 + samples))
        audio_data.extend(b"WAVE")
        audio_data.extend(b"fmt ")
        audio_data.extend(struct.pack("<I", 16))  # Size of fmt chunk
        audio_data.extend(struct.pack("<H", 1))  # PCM format
        audio_data.extend(struct.pack("<H", 1))  # Mono
        audio_data.extend(struct.pack("<I", rate))  # Sample rate
        audio_data.extend(struct.pack("<I", rate))  # Byte rate
        audio_data.extend(struct.pack("<H", 1))  # Block align
        audio_data.extend(struct.pack("<H", 8))  # Bits per sample
        audio_data.extend(b"data")
        audio_data.extend(struct.pack("<I", samples))

        # Generate simple tone at 440Hz
        for i in range(samples):
            value = int(127 + 127 * math.sin(2 * math.pi * 440 * i / rate))
            audio_data.append(value & 0xFF)

        return bytes(audio_data)


@router.post("/call-status")
async def call_status(request: Request, background_tasks: BackgroundTasks):
    """
    Handle call status webhook from Twilio.
    This webhook is called when a call status changes.
    """
    # Parse the request form data
    form_data = await request.form()
    call_sid = form_data.get("CallSid")
    call_status = form_data.get("CallStatus")
    test_id = form_data.get("test_id")

    logger.error(
        f"DEBUG: Call status update: {call_sid}, status: {call_status}, test: {test_id}"
    )

    # Find the test if not provided
    if not test_id:
        for tid, test_data in evaluator_service.active_tests.items():
            if test_data.get("call_sid") == call_sid:
                test_id = tid
                break

    # If the call is completed or failed, process the test
    if (
        call_status in ["completed", "failed", "no-answer", "busy", "canceled"]
        and test_id
    ):
        # Run in background to avoid blocking the webhook response
        background_tasks.add_task(
            process_completed_call, test_id, call_sid, call_status
        )

    return JSONResponse(content={"status": "success"})


async def process_completed_call(test_id: str, call_sid: str, call_status: str):
    """
    Process a completed call.
    This function is called as a background task when a call is completed.
    It generates a report based on the conversation.
    """
    logger.error(
        f"DEBUG: Processing completed call: {call_sid} for test {test_id}, status: {call_status}"
    )

    try:
        # Wait a bit for any final processing
        await asyncio.sleep(2)

        if test_id in evaluator_service.active_tests:
            # Update test status
            if call_status == "completed":
                evaluator_service.active_tests[test_id]["status"] = "processing"
            else:
                evaluator_service.active_tests[test_id]["status"] = "failed"
                evaluator_service.active_tests[test_id][
                    "error"
                ] = f"Call ended with status: {call_status}"

            evaluator_service.active_tests[test_id]["end_time"] = time.time()

            # Get the conversation
            conversation = evaluator_service.active_tests[test_id].get(
                "conversation", []
            )

            # Generate report
            if conversation:
                await evaluator_service.generate_report_from_conversation(
                    test_id, conversation
                )
                logger.error(
                    f"DEBUG: Generated report for call: {call_sid}, test: {test_id}"
                )
            else:
                logger.error(f"DEBUG: No conversation data for test {test_id}")
                # Create a minimal report with error
                await evaluator_service.generate_empty_report(
                    test_id,
                    f"No conversation data. Call ended with status: {call_status}",
                )
        else:
            logger.error(f"DEBUG: Test {test_id} not found in active tests")

    except Exception as e:
        logger.error(f"DEBUG: Error processing completed call: {str(e)}")
        import traceback

        logger.error(f"DEBUG: Traceback: {traceback.format_exc()}")


# Add this method to openai_service.py
async def text_to_speech(self, text: str, voice: str = "nova") -> bytes:
    """
    Convert text to speech using OpenAI's TTS API.

    Args:
        text: The text to convert to speech
        voice: The voice to use (alloy, echo, fable, onyx, nova, shimmer)

    Returns:
        Audio data as bytes
    """
    try:
        import requests

        url = "https://api.openai.com/v1/audio/speech"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        data = {"model": "tts-1", "input": text, "voice": voice}

        response = requests.post(url, headers=headers, json=data)

        if response.status_code != 200:
            logger.error(f"Error in text_to_speech: {response.text}")
            raise Exception(f"Error in text_to_speech: {response.status_code}")

        return response.content
    except Exception as e:
        logger.error(f"Error in text_to_speech: {str(e)}")
        raise
