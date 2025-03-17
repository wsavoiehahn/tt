# app/services/realtime_service.py
import json
import base64
import asyncio
import logging
import websockets
import time
import traceback
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timedelta
import os

from ..config import config
from ..models.test_cases import TestCase
from ..models.personas import Persona, Behavior

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # Ensure detailed logging


class RealtimeService:
    """Enhanced service for handling real-time voice interactions with OpenAI API."""

    def __init__(self):
        self.api_key = os.environ.get("OPENAI_API_KEY")
        self.realtime_url = "wss://api.openai.com/v1/realtime"
        self.realtime_model = "gpt-4o-realtime-preview-2024-12-17"
        self.voice = "coral"
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "OpenAI-Beta": "realtime=v1",
        }
        self.active_sessions = {}
        self.max_conversation_turns = 4

    async def initialize_session(
        self,
        call_sid: str,
        test_id: str,
    ) -> Dict[str, Any]:
        """
        Initialize a real-time session with OpenAI, with comprehensive diagnostics.
        """
        logger.error(
            f"Starting OpenAI session initialization - Call SID: {call_sid}, Test ID: {test_id}"
        )

        # Validate inputs
        if not self.api_key:
            logger.error("CRITICAL: OpenAI API key is missing")
            raise ValueError("OpenAI API key is required")

        logger.error(f"Model: {self.realtime_model}")

        try:
            # Import at function level to avoid circular imports
            from ..services.evaluator import evaluator_service

            # Load test case details
            test_data = evaluator_service.active_tests.get(test_id, {})
            test_case = test_data.get("test_case", {})
            logger.error(f"DEBUG: testcase details: {test_case}")
            # Get persona and behavior from test case
            persona_name = test_case.get("config", {}).get("persona_name", "Unknown")
            behavior_name = test_case.get("config", {}).get("behavior_name", "Unknown")

            # Get persona and behavior objects using evaluator service methods
            persona = evaluator_service.get_persona(persona_name)
            behavior = evaluator_service.get_behavior(behavior_name)

            if not persona or not behavior:
                logger.error(
                    f"Could not find persona '{persona_name}' or behavior '{behavior_name}'"
                )
                raise ValueError(
                    f"Invalid persona or behavior: {persona_name}, {behavior_name}"
                )

            # Get questions and special instructions
            questions = test_case.get("config", {}).get("questions", [])
            special_instructions = test_case.get("config", {}).get(
                "special_instructions"
            )

            knowledge_base = test_case.get("config", {}).get("knowledge_base", {})
            # If still empty, try to load from config
            if not knowledge_base:
                from ..config import config

                knowledge_base = config.load_knowledge_base()

            # Detailed connection attempt logging
            logger.error(f"Connection Details:")
            logger.error(f"Realtime URL: {self.realtime_url}")

            # Establish WebSocket connection with comprehensive error handling
            try:
                openai_ws = await asyncio.wait_for(
                    websockets.connect(
                        f"{self.realtime_url}?model={self.realtime_model}",
                        additional_headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "OpenAI-Beta": "realtime=v1",
                            "Content-Type": "application/json",
                        },
                        ping_interval=20,
                        ping_timeout=30,
                    ),
                    timeout=45,
                )
                logger.error(f"WebSocket connection established successfully")
                logger.error(f"WebSocket URI: {openai_ws.request.path}")
            except Exception as conn_error:
                logger.error(
                    f"CRITICAL WebSocket Connection Failure: {str(conn_error)}"
                )
                logger.error(traceback.format_exc())
                raise

            # Get first question
            first_question = ""
            if questions and len(questions) > 0:
                question_obj = questions[0]
                first_question = (
                    question_obj.get("text", "")
                    if isinstance(question_obj, dict)
                    else str(question_obj)
                )
                logger.error(f"First question: {first_question}")

            # Create system message
            system_message = self._create_system_prompt(
                persona, behavior, first_question, knowledge_base
            )

            # Detailed session configuration
            session_update = {
                "type": "session.update",
                "session": {
                    "turn_detection": {"type": "server_vad", "threshold": 0.8},
                    "input_audio_format": "g711_ulaw",
                    "output_audio_format": "g711_ulaw",
                    "voice": self.voice,
                    "instructions": system_message,
                    "modalities": ["text", "audio"],
                    "temperature": 0.7,
                },
            }

            # Send session configuration
            try:
                await openai_ws.send(json.dumps(session_update))
                logger.error("Session configuration sent successfully")
            except Exception as config_error:
                logger.error(
                    f"CRITICAL: Session configuration error: {str(config_error)}"
                )
                logger.error(traceback.format_exc())
                raise

            # Prepare session data
            session_data = {
                "openai_ws": openai_ws,
                "test_id": test_id,
                "call_sid": call_sid,
                "start_time": datetime.now(),
                "conversation": [],
                "stream_sid": None,
                "audio_buffer": bytearray(),
                "current_transcript": "",
                "last_response_time": None,
                "last_response_audio": None,
                "speaking": False,
                "waiting_for_response": False,
                "system_message": system_message,
                "first_question": first_question,
                "special_instructions": special_instructions,
                "current_turn": 0,
                "max_turns": test_case.get("config", {}).get("max_turns", 4),
                "conversation_complete": False,
            }

            # Store session
            self.active_sessions[call_sid] = session_data

            # Start listener for OpenAI responses
            asyncio.create_task(self._listen_for_openai_responses(call_sid))

            # Start the conversation
            await self.start_conversation(call_sid)

            logger.error(f"OpenAI session fully initialized for call {call_sid}")
            return session_data

        except Exception as comprehensive_error:
            logger.error(
                f"FATAL: Comprehensive initialization error: {str(comprehensive_error)}"
            )
            logger.error(traceback.format_exc())

            # Cleanup attempt
            if call_sid in self.active_sessions:
                await self.end_session(call_sid)

            raise

    async def start_conversation(self, call_sid: str):
        """Start the conversation with the question."""
        if call_sid not in self.active_sessions:
            logger.error(f"No active session found for call {call_sid}")
            return

        session_data = self.active_sessions[call_sid]
        openai_ws = session_data.get("openai_ws")
        first_question = session_data.get("first_question", "")
        test_id = session_data.get("test_id")

        if not openai_ws:
            logger.error(f"WebSocket not open for call {call_sid}")
            return

        try:
            # Initialize turn counter
            session_data["current_turn"] = 0

            # Send the first question as a user message
            logger.error(f"Starting conversation for call {call_sid}")

            # Create an explicit message with the question
            await openai_ws.send(
                json.dumps(
                    {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "message",
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": first_question
                                    or "Hello, I need help with something.",
                                }
                            ],
                        },
                    }
                )
            )

            # Then request a response (only send this ONCE)
            await openai_ws.send(json.dumps({"type": "response.create"}))
            logger.error(f"Sent explicit question and response.create command")

            # Record the start of conversation
            from ..services.evaluator import evaluator_service

            if test_id:
                # Record the first question as a turn from the evaluator
                if first_question:
                    evaluator_service.record_conversation_turn(
                        test_id=test_id,
                        call_sid=call_sid,
                        speaker="evaluator",
                        text=f"Initial question: {first_question}",
                    )

                # Update test status
                if test_id in evaluator_service.active_tests:
                    evaluator_service.active_tests[test_id]["status"] = "in_progress"
                    evaluator_service.active_tests[test_id][
                        "current_question_index"
                    ] = 0
                    logger.error(f"Updated test {test_id} status to in_progress")

                    # Also update in DynamoDB
                    from ..services.dynamodb_service import dynamodb_service

                    dynamodb_service.update_test_status(test_id, "in_progress")

            logger.error(f"Conversation started for call {call_sid}")
        except Exception as e:
            logger.error(f"Error starting conversation: {str(e)}")

    def get_latest_response(self, call_sid: str) -> str:
        """Get the latest response from OpenAI."""
        if call_sid not in self.active_sessions:
            return ""

        session_data = self.active_sessions[call_sid]
        return session_data.get("last_response_text", "")

    def should_continue_conversation(self, call_sid: str) -> bool:
        """Check if the conversation should continue."""
        if call_sid not in self.active_sessions:
            return False

        session_data = self.active_sessions[call_sid]
        current_turn = session_data.get("current_turn", 0)
        max_turns = session_data.get("max_turns", 4)

        return current_turn < max_turns and not session_data.get(
            "conversation_complete", False
        )

    async def _listen_for_openai_responses(self, call_sid: str):
        """
        Enhanced listener for OpenAI responses with improved message type handling.
        """
        logger.error(f"START: OpenAI response listener for call {call_sid}")

        # Track metrics for debugging
        start_time = datetime.now()
        message_count = 0
        response_text_buffer = ""

        try:
            # Validate session exists
            if call_sid not in self.active_sessions:
                logger.error(f"No active session found for call {call_sid}")
                return

            session_data = self.active_sessions[call_sid]
            openai_ws = session_data.get("openai_ws")
            test_id = session_data.get("test_id")

            if openai_ws and openai_ws.close_code:
                logger.error(f"WebSocket not open for call {call_sid}")
                return

            # Use a loop with timeout to avoid blocking indefinitely
            max_wait_time = 60  # seconds
            end_time = datetime.now() + timedelta(seconds=max_wait_time)

            logger.error(f"Starting message loop with {max_wait_time}s timeout")

            while datetime.now() < end_time:
                try:
                    # Set timeout for each message receive
                    time_left = (end_time - datetime.now()).total_seconds()
                    if time_left <= 0:
                        break

                    receive_timeout = min(5.0, time_left)
                    message = await asyncio.wait_for(
                        openai_ws.recv(), timeout=receive_timeout
                    )

                    # Track message received
                    message_count += 1

                    # Parse and process the message
                    try:
                        response = json.loads(message)
                        response_type = response.get("type")

                        logger.debug(
                            f"Received message {message_count} of type: {response_type}"
                        )

                        # Handle different response types
                        if response_type == "response.complete":
                            logger.error("Response generation complete")

                            # If we've collected any text in the buffer, record it now
                            if response_text_buffer and test_id:
                                from ..services.evaluator import evaluator_service

                                evaluator_service.record_conversation_turn(
                                    test_id=test_id,
                                    call_sid=call_sid,
                                    speaker="evaluator",
                                    text=response_text_buffer,
                                )
                                response_text_buffer = ""

                            # Increment turn counter and check if max turns reached
                            session_data["current_turn"] += 1
                            if session_data["current_turn"] >= session_data.get(
                                "max_turns", 4
                            ):
                                logger.error(
                                    f"Reached maximum turns, ending conversation"
                                )
                                session_data["conversation_complete"] = True
                                await self.end_conversation_and_evaluate(call_sid)
                                break

                        elif response_type == "message.create":
                            content = response.get("content", "")
                            logger.error(f"Assistant message: {content}")

                            # Record the message
                            if test_id and content:
                                from ..services.evaluator import evaluator_service

                                evaluator_service.record_conversation_turn(
                                    test_id=test_id,
                                    call_sid=call_sid,
                                    speaker="evaluator",
                                    text=content,
                                )

                        # Handle content part added (this is the text response)
                        elif response_type == "response.content_part.added":
                            if "content_part" in response:
                                content_part = response["content_part"]
                                if isinstance(content_part, str):
                                    content = content_part
                                else:
                                    content = content_part.get("text", "")

                                if content:
                                    logger.error(f"Content part: {content}")
                                    response_text_buffer += content

                        # Handle audio transcript (the text OpenAI is saying)
                        elif response_type == "response.audio_transcript.delta":
                            if "delta" in response:
                                # Check if delta is a string or object
                                delta = response["delta"]
                                logger.info(
                                    f"Delta object: {type(delta)}, Value: {delta}"
                                )
                                if isinstance(delta, str):
                                    transcript = delta
                                else:
                                    transcript = delta.get("text", "")

                                if transcript:
                                    response_text_buffer += transcript

                        elif response_type == "input_audio_buffer.transcription":
                            text = response.get("text", "")
                            if text:
                                logger.error(f"User transcription: {text}")

                                # Record the agent's response
                                if test_id:
                                    from ..services.evaluator import evaluator_service

                                    evaluator_service.record_conversation_turn(
                                        test_id=test_id,
                                        call_sid=call_sid,
                                        speaker="agent",
                                        text=text,
                                    )

                        elif response_type == "response.audio.delta":
                            # Just log that we received audio data
                            if "delta" in response:
                                logger.error(f"Received audio data chunk")

                        elif response_type == "response.done":
                            logger.error("Response marked as done")

                            # If we've collected any text in the buffer, record it now
                            if response_text_buffer and test_id:
                                from ..services.evaluator import evaluator_service

                                evaluator_service.record_conversation_turn(
                                    test_id=test_id,
                                    call_sid=call_sid,
                                    speaker="evaluator",
                                    text=response_text_buffer,
                                )
                                response_text_buffer = ""

                            # Increment turn counter
                            session_data["current_turn"] += 1
                            logger.error(
                                f"Completed turn {session_data['current_turn']}"
                            )

                        elif response_type == "error":
                            error_message = ""
                            if "error" in response and isinstance(
                                response["error"], dict
                            ):
                                error_message = response["error"].get(
                                    "message", "Unknown error"
                                )
                            else:
                                error_message = response.get("message", "Unknown error")

                            logger.error(f"OpenAI error: {error_message}")

                            # Record any accumulated text before ending
                            if response_text_buffer and test_id:
                                from ..services.evaluator import evaluator_service

                                evaluator_service.record_conversation_turn(
                                    test_id=test_id,
                                    call_sid=call_sid,
                                    speaker="evaluator",
                                    text=response_text_buffer
                                    + f" [Error: {error_message}]",
                                )
                                response_text_buffer = ""

                            # End conversation with error
                            session_data["error"] = error_message
                            session_data["conversation_complete"] = True
                            await self.end_conversation_and_evaluate(call_sid)
                            break

                    except json.JSONDecodeError:
                        logger.error(f"Failed to parse JSON message")
                    except Exception as msg_error:
                        logger.error(f"Error processing message: {str(msg_error)}")

                except asyncio.TimeoutError:
                    # Check if we've collected any text to record
                    if response_text_buffer and test_id:
                        logger.error(
                            f"Recording accumulated transcript: {response_text_buffer}"
                        )
                        from ..services.evaluator import evaluator_service

                        evaluator_service.record_conversation_turn(
                            test_id=test_id,
                            call_sid=call_sid,
                            speaker="evaluator",
                            text=response_text_buffer,
                        )
                        response_text_buffer = ""

                    # Check if we've been waiting too long
                    seconds_since_start = (datetime.now() - start_time).total_seconds()
                    if seconds_since_start > 15 and message_count > 10:
                        logger.error(
                            "No messages for 15+ seconds, assuming conversation is complete"
                        )
                        break

                except Exception as loop_error:
                    logger.error(f"Error in message loop: {str(loop_error)}")
                    break

            # Process any remaining text in buffer
            if response_text_buffer and test_id:
                logger.error(f"Recording final transcript: {response_text_buffer}")
                from ..services.evaluator import evaluator_service

                evaluator_service.record_conversation_turn(
                    test_id=test_id,
                    call_sid=call_sid,
                    speaker="evaluator",
                    text=response_text_buffer,
                )

            # Log summary before exiting
            duration = (datetime.now() - start_time).total_seconds()
            logger.error(
                f"OpenAI listener ending after {duration:.1f}s with {message_count} messages"
            )

        except Exception as e:
            logger.error(f"Fatal error in OpenAI listener: {str(e)}")
            import traceback

            logger.error(traceback.format_exc())

        finally:
            duration = (datetime.now() - start_time).total_seconds()
            logger.error(
                f"END: OpenAI response listener for call {call_sid} - Duration: {duration:.1f}s, Messages: {message_count}"
            )

    async def send_message(self, call_sid: str, message: str):
        """
        Send a text message to the OpenAI API.

        Args:
            call_sid: Twilio call SID
            message: Message to send
        """
        if call_sid not in self.active_sessions:
            logger.error(f"Session not found for call {call_sid}")
            return

        session_data = self.active_sessions[call_sid]
        openai_ws = session_data.get("openai_ws")

        if not openai_ws:
            logger.error(f"WebSocket not open for call {call_sid}")
            return

        try:
            # Create a user message
            await openai_ws.send(
                json.dumps(
                    {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "message",
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": message,
                                }
                            ],
                        },
                    }
                )
            )

            # Trigger a response
            await openai_ws.send(json.dumps({"type": "response.create"}))

            logger.info(f"Sent message to OpenAI for call {call_sid}: {message}")
        except Exception as e:
            logger.error(f"Error sending message: {str(e)}")
            import traceback

            logger.error(f"Traceback: {traceback.format_exc()}")

    async def process_audio_chunk(self, call_sid: str, audio_chunk: str):
        """
        Process an audio chunk from Twilio.

        Args:
            call_sid: Twilio call SID
            audio_chunk: Base64-encoded audio chunk
        """
        if call_sid not in self.active_sessions:
            logger.error(f"Session not found for call {call_sid}")
            return

        session_data = self.active_sessions[call_sid]
        openai_ws = session_data.get("openai_ws")

        if not openai_ws:
            logger.error(f"WebSocket not open for call {call_sid}")
            return

        try:
            # If OpenAI is not speaking, forward the audio
            if not session_data.get("speaking", False):
                # Send audio to OpenAI
                audio_append = {
                    "type": "input_audio_buffer.append",
                    "audio": audio_chunk,
                }
                await openai_ws.send(json.dumps(audio_append))
                logger.error(f"Sent audio chunk to OpenAI for call {call_sid}")
        except Exception as e:
            logger.error(f"Error processing audio chunk: {str(e)}")
            import traceback

            logger.error(f"Traceback: {traceback.format_exc()}")

    def _create_system_prompt(
        self,
        persona: Persona,
        behavior: Behavior,
        question: str,
        knowledge_base: Dict[str, Any],
    ) -> str:
        """
        Create a detailed system prompt for the AI evaluator.

        Args:
            persona: Customer persona
            behavior: Customer behavior
            knowledge_base: Available knowledge

        Returns:
            Comprehensive system prompt string
        """
        logger.info(persona)
        persona_traits = ", ".join(persona.traits)
        behavior_chars = ", ".join(behavior.characteristics)

        # Format knowledge base
        faq_section = ""
        for faq_dict in knowledge_base.get("faqs", []):
            for q, a in faq_dict.items():
                faq_section += f"Q: {q}\nA: {a}\n\n"

        system_prompt = f"""
        You are an AI conducting a detailed customer service performance evaluation.

        Evaluation Objectives:
        1. Ask a specific, clear question about the service
        2. Carefully analyze the agent's response
        3. Critically assess:
           - Information accuracy
           - Clarity of explanation
           - Empathy and communication quality

        Customer Persona: {persona.name}
        Persona Traits: {persona_traits}

        Customer Behavior: {behavior.name}
        Behavior Characteristics: {behavior_chars}

        You are calling an AI customer service agent.
        You need to ask about the following question: "{question}"

        Interaction Guidelines:
        - Be conversational and natural
        - Ask follow-up questions if responses are incomplete
        - Provide reasonable opportunity for the agent to explain
        - Simulate a realistic customer interaction
        - Limit conversation to {self.max_conversation_turns} meaningful exchanges

        Available Knowledge Base:
        {faq_section}

        Evaluation Criteria:
        - Understanding of customer's question
        - Providing accurate, relevant information
        - Demonstrating empathy and professionalism
        - Effectively addressing customer needs

        Your primary goal is to objectively assess the agent's customer service skills.
        """

        return system_prompt

    def get_conversation(self, call_sid: str) -> List[Dict[str, Any]]:
        """
        Retrieve conversation history for a specific call.

        Args:
            call_sid: Twilio call SID

        Returns:
            List of conversation turns
        """
        if call_sid in self.active_sessions:
            return self.active_sessions[call_sid].get("conversation", [])
        return []

    async def end_conversation_and_evaluate(self, call_sid: str):
        """
        End a conversation and trigger evaluation.

        Args:
            call_sid: Twilio call SID
        """
        if call_sid not in self.active_sessions:
            logger.error(f"No active session found for call {call_sid}")
            return

        session_data = self.active_sessions[call_sid]
        test_id = session_data.get("test_id")

        if not test_id:
            logger.error(f"No test ID found for call {call_sid}")
            return

        # Mark conversation as complete
        session_data["conversation_complete"] = True
        logger.error(f"Marking conversation as complete for call {call_sid}")

        # Close OpenAI WebSocket
        openai_ws = session_data.get("openai_ws")
        if openai_ws and not openai_ws.close_code:
            try:
                await openai_ws.send(json.dumps({"type": "session.terminate"}))
                await openai_ws.close()
                logger.error(f"Closed OpenAI WebSocket for call {call_sid}")
            except Exception as e:
                logger.error(f"Error closing OpenAI WebSocket: {str(e)}")

        # Gather the complete conversation from the evaluator service
        from ..services.evaluator import evaluator_service

        # Check if test exists
        if test_id not in evaluator_service.active_tests:
            logger.error(f"Test {test_id} not found in active tests")
            return

        # Get conversation from evaluator service
        conversation = []
        if "conversation" in evaluator_service.active_tests[test_id]:
            conversation = evaluator_service.active_tests[test_id]["conversation"]

        if not conversation:
            logger.error(f"No conversation data found for test {test_id}")
            # Try to create an empty report with error message
            try:
                await evaluator_service.generate_empty_report(
                    test_id, "No conversation data recorded"
                )
            except Exception as e:
                logger.error(f"Error creating empty report: {str(e)}")
            return

        # Process the call to generate evaluation report
        try:
            logger.error(
                f"Processing conversation with {len(conversation)} turns for evaluation"
            )
            await evaluator_service.process_call(test_id, call_sid, conversation)
            logger.error(f"Evaluation completed for call {call_sid}")
        except Exception as e:
            logger.error(f"Error during evaluation: {str(e)}")
            logger.error(traceback.format_exc())

            # Try to create an error report
            try:
                await evaluator_service.generate_empty_report(
                    test_id, f"Error during evaluation: {str(e)}"
                )
            except:
                logger.error(
                    f"ERROR DURING ERROR CREATING REPORT Error during evaluation: {str(e)} and "
                )

    async def end_session(self, call_sid: str):
        """
        End a real-time session.

        Args:
            call_sid: Twilio call SID
        """
        logger.error(f"Ending session for call {call_sid}")

        if call_sid in self.active_sessions:
            session_data = self.active_sessions[call_sid]

            # Only trigger evaluation if not already completed
            if not session_data.get("conversation_complete", False):
                logger.error(f"Conversation not marked complete, running evaluation")
                await self.end_conversation_and_evaluate(call_sid)

            # Remove from active sessions after evaluation is done
            del self.active_sessions[call_sid]
            logger.error(f"Removed session data for call {call_sid}")


# Create a singleton instance
realtime_service = RealtimeService()
