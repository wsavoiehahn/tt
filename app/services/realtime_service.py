# app/services/realtime_service.py
import json
import base64
import asyncio
import logging
import websockets
import time
from typing import Dict, Any, Optional, List, Tuple, Callable
from datetime import datetime
import os

from ..config import config
from ..models.test_cases import TestCase
from ..models.personas import Persona, Behavior

logger = logging.getLogger(__name__)


class RealtimeService:
    """Service for handling real-time voice interactions with OpenAI API."""

    def __init__(self):
        self.api_key = config.get_parameter("/openai/api_key")
        self.realtime_url = "wss://api.openai.com/v1/realtime"
        self.realtime_model = "gpt-4o-realtime-preview-2024-12-17"
        self.voice = "coral"  # Options include 'alloy', 'echo', 'fable', 'onyx', 'nova', 'shimmer', 'coral'
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "OpenAI-Beta": "realtime=v1",
        }
        self.active_sessions = {}  # Store active websocket sessions keyed by call_sid
        self.max_conversation_turns = 4  # Default max turns after initial question

    async def initialize_session(
        self,
        call_sid: str,
        test_id: str,
        persona: Persona,
        behavior: Behavior,
        knowledge_base: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Initialize a real-time session with OpenAI.

        Args:
            call_sid: Twilio call SID
            test_id: Test ID
            persona: Persona configuration
            behavior: Behavior configuration
            knowledge_base: Knowledge base data

        Returns:
            Session data dictionary
        """
        logger.info(
            f"Initializing real-time session for call {call_sid}, test {test_id}"
        )

        # Create system message from persona, behavior, and knowledge base
        system_message = self._create_system_prompt(persona, behavior, knowledge_base)

        try:
            # Connect to OpenAI real-time API
            openai_ws = await websockets.connect(
                f"{self.realtime_url}?model={self.realtime_model}",
                extra_headers=self.headers,
            )

            # Load test case details to get the question
            from ..services.evaluator import evaluator_service

            test_data = evaluator_service.active_tests.get(test_id, {})
            test_case = test_data.get("test_case", {})
            questions = test_case.get("config", {}).get("questions", [])
            special_instructions = test_case.get("config", {}).get(
                "special_instructions"
            )

            # Get first question if available
            first_question = ""
            if questions and len(questions) > 0:
                question_obj = questions[0]
                if isinstance(question_obj, dict):
                    first_question = question_obj.get("text", "")
                else:
                    first_question = str(question_obj)

            # Set max turns from test case config if available
            max_turns = test_case.get("config", {}).get("max_turns", 4)

            # Store the session
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
                "max_turns": max_turns,
                "conversation_complete": False,
            }

            self.active_sessions[call_sid] = session_data

            # Initialize the session
            await self._initialize_session_config(openai_ws, system_message)

            # Start listener for OpenAI responses
            asyncio.create_task(self._listen_for_openai_responses(call_sid))

            logger.info(f"OpenAI session initialized for call {call_sid}")
            return session_data

        except Exception as e:
            logger.error(
                f"Error in initializing real-time session for call {call_sid}: {str(e)}"
            )
            import traceback

            logger.error(f"Traceback: {traceback.format_exc()}")
            # Clean up any partial initialization
            await self.end_session(call_sid)
            raise

    async def _initialize_session_config(self, openai_ws, system_message: str):
        """
        Initialize the OpenAI session with system message and configuration.

        Args:
            openai_ws: OpenAI websocket connection
            system_message: System message with persona, behavior and knowledge base
        """
        try:
            # Set up session configuration
            session_update = {
                "type": "session.update",
                "session": {
                    "turn_detection": {"type": "server_vad", "threshold": 0.8},
                    "input_audio_format": "g711_ulaw",  # Twilio's format
                    "output_audio_format": "g711_ulaw",
                    "voice": self.voice,
                    "instructions": system_message,
                    "modalities": ["text", "audio"],
                    "temperature": 0.7,
                    "beam_size": 5,  # Increase beam size for better output
                },
            }

            # Send session configuration
            await openai_ws.send(json.dumps(session_update))
            logger.info("Sent session configuration to OpenAI")

            # Ready to receive audio
            logger.info("Realtime session initialization completed")
        except Exception as e:
            logger.error(f"Error initializing session: {str(e)}")
            raise

    async def start_conversation(self, call_sid: str):
        """
        Start the conversation by having the AI evaluator ask the first question.

        Args:
            call_sid: Twilio call SID
        """
        if call_sid not in self.active_sessions:
            logger.error(f"No active session found for call {call_sid}")
            return False

        session_data = self.active_sessions[call_sid]
        first_question = session_data.get("first_question", "")
        special_instructions = session_data.get("special_instructions", "")

        # Construct the initial prompt for the evaluator to say
        intro_message = (
            "Hello, this is an automated call from the AI evaluation system."
        )

        if special_instructions:
            intro_message += f" Special instructions: {special_instructions}."

        if first_question:
            intro_message += f" Here's my question: {first_question}"
        else:
            intro_message += (
                " I'd like to ask you a few questions about your experience."
            )

        logger.info(
            f"Starting conversation for call {call_sid} with message: {intro_message}"
        )

        # Send the message to OpenAI
        await self.send_message(call_sid, intro_message)

        # Record the first turn
        from ..services.evaluator import evaluator_service

        evaluator_service.record_conversation_turn(
            test_id=session_data["test_id"],
            call_sid=call_sid,
            speaker="evaluator",
            text=intro_message,
        )

        # Increment turn counter
        session_data["current_turn"] += 1

        return True

    async def _listen_for_openai_responses(self, call_sid: str):
        """
        Listen for responses from OpenAI and process them.

        Args:
            call_sid: The call SID
        """
        if call_sid not in self.active_sessions:
            logger.error(f"No active session found for call {call_sid}")
            return

        session_data = self.active_sessions[call_sid]
        openai_ws = session_data.get("openai_ws")

        if not openai_ws:
            logger.error(f"No WebSocket connection found for call {call_sid}")
            return

        try:
            async for message in openai_ws:
                try:
                    response = json.loads(message)
                    logger.debug(f"OpenAI response: {response.get('type')}")

                    # Handle audio data
                    if (
                        response.get("type") == "response.audio.delta"
                        and "delta" in response
                    ):
                        # Mark that OpenAI is speaking
                        session_data["speaking"] = True

                        # If this is the first chunk, record start time
                        if not session_data.get("last_response_time"):
                            session_data["last_response_time"] = datetime.now()

                        # Decode audio and store
                        audio_data = base64.b64decode(response["delta"])
                        session_data["audio_buffer"] += audio_data

                        # Store the latest chunk for immediate streaming
                        session_data["last_response_audio"] = audio_data

                    # Handle speech recognition events
                    elif response.get("type") == "input_audio_buffer.speech_started":
                        logger.info(f"Speech started detected for call {call_sid}")
                        session_data["waiting_for_response"] = False

                    # Handle transcription events
                    elif response.get("type") == "input_audio_buffer.transcription":
                        if response.get("is_final", False):
                            text = response.get("text", "")
                            session_data["current_transcript"] = text

                            logger.info(f"Transcription for call {call_sid}: {text}")

                            # This is the agent's response - record it
                            from ..services.evaluator import evaluator_service

                            evaluator_service.record_conversation_turn(
                                test_id=session_data["test_id"],
                                call_sid=call_sid,
                                speaker="agent",
                                text=text,
                            )

                            # Add to session conversation history
                            session_data["conversation"].append(
                                {
                                    "speaker": "agent",
                                    "text": text,
                                    "timestamp": datetime.now().isoformat(),
                                }
                            )

                            # Increment turn counter for agent's response
                            session_data["current_turn"] += 1

                            # Check if we've reached the max turns
                            if (
                                session_data["current_turn"]
                                >= session_data["max_turns"] * 2
                            ):  # *2 because we count both sides
                                # End the conversation after this
                                session_data["conversation_complete"] = True

                                # Send a closing message
                                await self.send_message(
                                    call_sid,
                                    "Thank you for your responses. That concludes our evaluation for today.",
                                )

                    # Handle response done events
                    elif response.get("type") == "response.done":
                        # Mark that OpenAI is no longer speaking
                        session_data["speaking"] = False
                        session_data["waiting_for_response"] = True

                        # Get the final response text
                        response_text = ""
                        for item in response.get("response", {}).get("output", []):
                            for content in item.get("content", []):
                                if content.get("type") == "text":
                                    response_text += content.get("text", "")

                        # Calculate response time
                        response_time = 0
                        if session_data.get("last_response_time"):
                            response_time = (
                                datetime.now() - session_data["last_response_time"]
                            ).total_seconds()

                        # Store the complete audio buffer
                        audio_buffer = session_data.get("audio_buffer", bytearray())

                        # Reset audio buffer
                        session_data["audio_buffer"] = bytearray()
                        session_data["last_response_time"] = None

                        logger.info(
                            f"AI Evaluator response for call {call_sid}: {response_text}"
                        )

                        # This is the evaluator's response - record it
                        from ..services.evaluator import evaluator_service

                        if audio_buffer:
                            # Save the audio to S3
                            from ..services.s3_service import s3_service

                            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                            audio_filename = f"{timestamp}_evaluator_response.mp3"
                            s3_key = f"tests/{session_data['test_id']}/calls/{call_sid}/recordings/{audio_filename}"

                            # Upload the audio data
                            s3_service.s3_client.put_object(
                                Bucket=s3_service.bucket_name,
                                Key=s3_key,
                                Body=audio_buffer,
                                ContentType="audio/mp3",
                            )

                            s3_url = f"s3://{s3_service.bucket_name}/{s3_key}"

                            # Record with audio URL
                            evaluator_service.record_conversation_turn(
                                test_id=session_data["test_id"],
                                call_sid=call_sid,
                                speaker="evaluator",
                                text=response_text,
                                audio_url=s3_url,
                            )
                        else:
                            # Record without audio URL
                            evaluator_service.record_conversation_turn(
                                test_id=session_data["test_id"],
                                call_sid=call_sid,
                                speaker="evaluator",
                                text=response_text,
                            )

                        # Add to session conversation history
                        session_data["conversation"].append(
                            {
                                "speaker": "evaluator",
                                "text": response_text,
                                "timestamp": datetime.now().isoformat(),
                                "response_time": response_time,
                            }
                        )

                        # Increment turn counter for evaluator's response
                        session_data["current_turn"] += 1

                        # Check if this was a closing message or if we've reached max turns
                        if (
                            session_data.get("conversation_complete")
                            or "concludes our evaluation" in response_text.lower()
                        ):
                            logger.info(
                                f"Conversation complete for call {call_sid} after {session_data['current_turn']} turns"
                            )

                            # End the WebSocket connection
                            await self.end_session(call_sid)

                            # End the Twilio call
                            from ..services.twilio_service import twilio_service

                            twilio_service.end_call(call_sid)

                except json.JSONDecodeError:
                    logger.warning(
                        f"Received non-JSON message from OpenAI: {message[:100]}"
                    )
                except Exception as e:
                    logger.error(f"Error processing OpenAI message: {str(e)}")
                    import traceback

                    logger.error(f"Traceback: {traceback.format_exc()}")
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"OpenAI WebSocket connection closed for call {call_sid}")
        except Exception as e:
            logger.error(
                f"Error in listening for OpenAI responses for call {call_sid}: {str(e)}"
            )
            import traceback

            logger.error(f"Traceback: {traceback.format_exc()}")
        finally:
            # Clean up if needed
            if call_sid in self.active_sessions:
                await self.end_session(call_sid)

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

        if not openai_ws or not openai_ws.open:
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
                logger.debug(f"Sent audio chunk to OpenAI for call {call_sid}")
        except Exception as e:
            logger.error(f"Error processing audio chunk: {str(e)}")
            import traceback

            logger.error(f"Traceback: {traceback.format_exc()}")

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

        if not openai_ws or not openai_ws.open:
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

    async def end_session(self, call_sid: str):
        """
        End a real-time session.

        Args:
            call_sid: Twilio call SID
        """
        if call_sid in self.active_sessions:
            openai_ws = self.active_sessions[call_sid].get("openai_ws")
            if openai_ws and openai_ws.open:
                try:
                    await openai_ws.close()
                    logger.info(f"Closed OpenAI WebSocket for call {call_sid}")
                except Exception as e:
                    logger.error(f"Error closing OpenAI WebSocket: {str(e)}")

            # Remove from active sessions
            del self.active_sessions[call_sid]
            logger.info(f"Ended real-time session for call {call_sid}")

    def get_conversation(self, call_sid: str) -> List[Dict[str, Any]]:
        """
        Get the conversation history for a call.

        Args:
            call_sid: Twilio call SID

        Returns:
            List of conversation turns
        """
        if call_sid in self.active_sessions:
            return self.active_sessions[call_sid].get("conversation", [])

        return []

    def _create_system_prompt(
        self,
        persona: Persona,
        behavior: Behavior,
        knowledge_base: Dict[str, Any],
    ) -> str:
        """
        Create a system prompt based on persona, behavior, and knowledge base.

        Args:
            persona: Persona configuration
            behavior: Behavior configuration
            knowledge_base: Knowledge base data

        Returns:
            System prompt string
        """
        persona_traits = ", ".join(persona.traits)
        behavior_chars = ", ".join(behavior.characteristics)

        # Format FAQ knowledge base
        faq_section = ""
        for faq_dict in knowledge_base.get("faqs", []):
            for q, a in faq_dict.items():
                faq_section += f"Q: {q}\nA: {a}\n\n"

        # Create system prompt for the AI as the evaluator, not the agent
        system_prompt = f"""
        You are an AI evaluator conducting an assessment call to test a call center agent.
        
        You are calling to evaluate an agent's performance.
        
        Your persona is: {persona.name}
        Traits: {persona_traits}
        
        You are exhibiting the following behavior: {behavior.name}
        Characteristics: {behavior_chars}
        
        You should ask questions to the agent and evaluate their responses. Your primary goal is to:
        1. Ask your assigned question clearly
        2. Listen to the agent's response
        3. Ask appropriate follow-up questions (up to {self.max_conversation_turns-1} follow-ups)
        4. Evaluate if the agent provides accurate information according to the knowledge base
        5. Evaluate the agent's empathy and responsiveness
        
        When the conversation is complete, thank the agent and end the call politely.
        
        Here is the knowledge base with correct information the agent should know:
        
        {faq_section}
        
        Important guidelines:
        1. Be conversational and natural
        2. Ask your question clearly and listen to the response
        3. Evaluate both accuracy and empathy in the agent's responses
        4. End the call after getting satisfactory answers or after {self.max_conversation_turns} turns
        5. Keep your responses concise
        """

        return system_prompt


# Create a singleton instance
realtime_service = RealtimeService()
