# app/services/realtime_service.py
import json
import base64
import asyncio
import logging
import websockets
from typing import Dict, Any, Optional, List, Tuple, Callable
from datetime import datetime

from ..config import config
from ..models.test_cases import TestCase

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

    async def start_session(
        self,
        call_sid: str,
        test_id: str,
        persona: Dict[str, Any],
        behavior: Dict[str, Any],
        knowledge_base: Dict[str, Any],
        twilio_ws,
        transcription_callback: Optional[Callable] = None,
    ):
        """
        Start a real-time session with OpenAI.

        Args:
            call_sid: Twilio call SID
            test_id: Test case ID
            persona: Persona configuration
            behavior: Behavior configuration
            knowledge_base: Knowledge base data
            twilio_ws: Twilio websocket connection
            transcription_callback: Callback function for transcription events
        """
        logger.info(f"Starting real-time session for call {call_sid}, test {test_id}")

        # Create system message from persona, behavior, and knowledge base
        system_message = self._create_system_prompt(persona, behavior, knowledge_base)

        try:
            # Connect to OpenAI real-time API
            openai_ws = await websockets.connect(
                f"{self.realtime_url}?model={self.realtime_model}",
                extra_headers=self.headers,
            )

            # Store the session
            self.active_sessions[call_sid] = {
                "openai_ws": openai_ws,
                "test_id": test_id,
                "start_time": datetime.now(),
                "conversation": [],
                "twilio_ws": twilio_ws,
                "transcription_callback": transcription_callback,
                "stream_sid": None,
                "audio_buffer": bytearray(),
                "current_transcript": "",
                "last_response_time": None,
                "speaking": False,
                "waiting_for_response": False,
            }

            # Initialize the session
            await self._initialize_session(openai_ws, system_message)

            # Start processing tasks
            incoming_task = asyncio.create_task(
                self._process_incoming_audio(twilio_ws, openai_ws, call_sid)
            )
            outgoing_task = asyncio.create_task(
                self._process_outgoing_audio(
                    openai_ws, twilio_ws, call_sid, transcription_callback
                )
            )

            # Wait for both tasks to complete
            await asyncio.gather(incoming_task, outgoing_task)

        except Exception as e:
            logger.error(f"Error in real-time session for call {call_sid}: {str(e)}")
            import traceback

            logger.error(f"Traceback: {traceback.format_exc()}")
            # Cleanup session
            await self.end_session(call_sid)
            raise

    async def _initialize_session(self, openai_ws, system_message: str):
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
                },
            }

            # Send session configuration
            await openai_ws.send(json.dumps(session_update))
            logger.info("Sent session configuration to OpenAI")

            # Ready to receive audio
            logger.info("Realtime session initialized successfully")
        except Exception as e:
            logger.error(f"Error initializing session: {str(e)}")
            raise

    async def _process_incoming_audio(self, twilio_ws, openai_ws, call_sid: str):
        """
        Process incoming audio from Twilio and send to OpenAI.

        Args:
            twilio_ws: Twilio websocket connection
            openai_ws: OpenAI websocket connection
            call_sid: Twilio call SID
        """
        try:
            # Keep track of media timestamps for calculating response times
            latest_media_timestamp = 0

            async for message in twilio_ws.iter_text():
                # Parse Twilio media message
                data = json.loads(message)

                if data.get("event") == "media" and call_sid in self.active_sessions:
                    session_data = self.active_sessions[call_sid]

                    # Check if OpenAI connection is still open
                    if not openai_ws.open:
                        logger.warning(f"OpenAI WebSocket closed for call {call_sid}")
                        break

                    # Update timestamp
                    latest_media_timestamp = int(data["media"]["timestamp"])
                    session_data["latest_media_timestamp"] = latest_media_timestamp

                    # Extract audio payload
                    audio_payload = data["media"]["payload"]

                    try:
                        # If OpenAI is not speaking, forward the audio
                        if not session_data.get("speaking", False):
                            # Send audio to OpenAI
                            audio_append = {
                                "type": "input_audio_buffer.append",
                                "audio": audio_payload,
                            }
                            await openai_ws.send(json.dumps(audio_append))
                    except Exception as audio_error:
                        logger.error(
                            f"Error sending audio to OpenAI: {str(audio_error)}"
                        )

                elif data.get("event") == "start":
                    # Log the start of the stream
                    stream_sid = data["start"]["streamSid"]
                    logger.info(f"Incoming Twilio stream started: {stream_sid}")

                    # Store stream_sid in session data
                    if call_sid in self.active_sessions:
                        self.active_sessions[call_sid]["stream_sid"] = stream_sid
                        self.active_sessions[call_sid]["latest_media_timestamp"] = 0

                elif data.get("event") == "stop":
                    # Log the end of the stream
                    logger.info(f"Incoming Twilio stream stopped for call {call_sid}")

                    # Close the OpenAI connection
                    await self.end_session(call_sid)
                    break

        except Exception as e:
            logger.error(
                f"Error processing incoming audio for call {call_sid}: {str(e)}"
            )
            import traceback

            logger.error(f"Traceback: {traceback.format_exc()}")
            # Cleanup session
            await self.end_session(call_sid)

    async def _process_outgoing_audio(
        self, openai_ws, twilio_ws, call_sid: str, transcription_callback=None
    ):
        """
        Process outgoing audio from OpenAI and send to Twilio.

        Args:
            openai_ws: OpenAI websocket connection
            twilio_ws: Twilio websocket connection
            call_sid: Twilio call SID
            transcription_callback: Callback function for transcription events
        """
        try:
            if call_sid not in self.active_sessions:
                logger.error(f"Session data not found for call {call_sid}")
                return

            session_data = self.active_sessions[call_sid]
            current_transcript = ""
            current_audio_buffer = bytearray()
            response_start_time = None

            # Use the stored system message to initiate the first response
            await openai_ws.send(
                json.dumps(
                    {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "message",
                            "role": "system",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": "Provide a brief greeting to start the conversation.",
                                }
                            ],
                        },
                    }
                )
            )

            # Start response generation
            await openai_ws.send(json.dumps({"type": "response.create"}))

            async for openai_message in openai_ws:
                if call_sid not in self.active_sessions:
                    logger.warning(f"Session data no longer exists for call {call_sid}")
                    break

                response = json.loads(openai_message)

                # Handle audio data
                if (
                    response.get("type") == "response.audio.delta"
                    and "delta" in response
                ):
                    # Mark that OpenAI is speaking
                    session_data["speaking"] = True

                    # If this is the first chunk, record start time
                    if response_start_time is None:
                        response_start_time = datetime.now()
                        session_data["last_response_time"] = response_start_time

                    try:
                        # Decode, encode, and send audio data to Twilio
                        audio_payload = base64.b64encode(
                            base64.b64decode(response["delta"])
                        ).decode("utf-8")

                        stream_sid = session_data.get("stream_sid")
                        if stream_sid:
                            audio_delta = {
                                "event": "media",
                                "streamSid": stream_sid,
                                "media": {"payload": audio_payload},
                            }
                            await twilio_ws.send_text(json.dumps(audio_delta))

                        # Accumulate audio data for saving later
                        current_audio_buffer.extend(base64.b64decode(response["delta"]))
                    except Exception as audio_error:
                        logger.error(
                            f"Error sending audio to Twilio: {str(audio_error)}"
                        )

                # Handle speech recognition events
                elif response.get("type") == "input_audio_buffer.speech_started":
                    logger.info(f"Speech started detected for call {call_sid}")
                    session_data["waiting_for_response"] = False

                # Handle transcription events
                elif response.get("type") == "input_audio_buffer.transcription":
                    # Update current transcript
                    if response.get("is_final", False):
                        current_transcript = response.get("text", "")
                        session_data["current_transcript"] = current_transcript

                        logger.info(
                            f"Transcription for call {call_sid}: {current_transcript}"
                        )

                        # Store the transcription
                        if transcription_callback:
                            await transcription_callback(
                                call_sid, "user", current_transcript
                            )

                        # Add to conversation history
                        session_data["conversation"].append(
                            {
                                "speaker": "user",
                                "text": current_transcript,
                                "timestamp": datetime.now().isoformat(),
                            }
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
                    if response_start_time:
                        response_time = (
                            datetime.now() - response_start_time
                        ).total_seconds()

                    logger.info(f"Response for call {call_sid}: {response_text}")

                    # Store the response
                    session_data["conversation"].append(
                        {
                            "speaker": "assistant",
                            "text": response_text,
                            "timestamp": datetime.now().isoformat(),
                            "response_time": response_time,
                        }
                    )

                    # Call transcription callback
                    if transcription_callback and current_audio_buffer:
                        await transcription_callback(
                            call_sid,
                            "assistant",
                            response_text,
                            bytes(current_audio_buffer),
                        )

                    # Reset for next turn
                    current_transcript = ""
                    current_audio_buffer = bytearray()
                    response_start_time = None

                    # Send mark for question completion
                    mark_message = {
                        "event": "mark",
                        "streamSid": session_data.get("stream_sid"),
                        "mark": {"name": "question_complete"},
                    }
                    try:
                        await twilio_ws.send_text(json.dumps(mark_message))
                    except Exception as mark_error:
                        logger.error(f"Error sending mark to Twilio: {str(mark_error)}")

        except Exception as e:
            logger.error(
                f"Error processing outgoing audio for call {call_sid}: {str(e)}"
            )
            import traceback

            logger.error(f"Traceback: {traceback.format_exc()}")

    def _create_system_prompt(
        self,
        persona: Dict[str, Any],
        behavior: Dict[str, Any],
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
        persona_traits = ", ".join(persona.get("traits", []))
        behavior_chars = ", ".join(behavior.get("characteristics", []))

        # Format FAQ knowledge base
        faq_section = ""
        for faq_dict in knowledge_base.get("faqs", []):
            for q, a in faq_dict.items():
                faq_section += f"Q: {q}\nA: {a}\n\n"

        # Create system prompt
        system_prompt = f"""
        You are a call center agent for Sendero Health Plans.
        
        You should act according to this persona: {persona.get('name')}
        Traits: {persona_traits}
        
        The caller will exhibit the following behavior: {behavior.get('name')}
        Characteristics: {behavior_chars}
        
        Use natural, conversational language appropriate for a call center agent.
        Be helpful, concise, and accurate in your responses.
        Keep your responses brief and to the point, focusing on answering the caller's questions directly.
        
        Here is the knowledge base you should use to answer questions:
        
        {faq_section}
        
        IVR Script:
        {knowledge_base.get('ivr_script', {}).get('welcome_message', '')}
        
        Important guidelines:
        1. Be empathetic and patient with callers
        2. Provide accurate information based on the knowledge base
        3. If you don't know an answer, admit it and offer to help in other ways
        4. Speak in a natural, conversational tone
        5. Keep responses brief and focused
        6. Wait for the caller to finish speaking before responding
        """

        return system_prompt

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
        except Exception as e:
            logger.error(f"Error processing audio chunk: {str(e)}")


# Create a singleton instance
realtime_service = RealtimeService()
