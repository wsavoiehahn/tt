# app/services/realtime_service.py
import json
import base64
import asyncio
import logging
import websockets
from typing import Dict, Any, Optional, List, Tuple
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
        transcription_callback=None,
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
            async with websockets.connect(
                f"{self.realtime_url}?model={self.realtime_model}",
                extra_headers=self.headers,
            ) as openai_ws:
                # Store the session
                self.active_sessions[call_sid] = {
                    "openai_ws": openai_ws,
                    "test_id": test_id,
                    "start_time": datetime.now(),
                    "conversation": [],
                }

                # Initialize the session
                await self._initialize_session(openai_ws, system_message)

                # Start processing tasks
                await asyncio.gather(
                    self._process_incoming_audio(twilio_ws, openai_ws, call_sid),
                    self._process_outgoing_audio(
                        openai_ws, twilio_ws, call_sid, transcription_callback
                    ),
                )

        except Exception as e:
            logger.error(f"Error in real-time session for call {call_sid}: {str(e)}")
            # Cleanup session
            if call_sid in self.active_sessions:
                del self.active_sessions[call_sid]
            raise

    async def _initialize_session(self, openai_ws, system_message: str):
        """
        Initialize the OpenAI session with system message and configuration.

        Args:
            openai_ws: OpenAI websocket connection
            system_message: System message with persona, behavior and knowledge base
        """
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

        # Send initial greeting message
        initial_message = {
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": "Greet the caller and ask how you can help them.",
                    }
                ],
            },
        }
        await openai_ws.send(json.dumps(initial_message))

        # Start response generation
        await openai_ws.send(json.dumps({"type": "response.create"}))

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

                if data.get("event") == "media" and openai_ws.open:
                    # Update timestamp
                    latest_media_timestamp = int(data["media"]["timestamp"])

                    # Send audio to OpenAI
                    audio_append = {
                        "type": "input_audio_buffer.append",
                        "audio": data["media"]["payload"],
                    }
                    await openai_ws.send(json.dumps(audio_append))

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

                    # Clean up the session
                    if call_sid in self.active_sessions:
                        del self.active_sessions[call_sid]

                    # Close the OpenAI connection
                    if openai_ws.open:
                        await openai_ws.close()

                    break

        except Exception as e:
            logger.error(
                f"Error processing incoming audio for call {call_sid}: {str(e)}"
            )
            # Cleanup session
            if call_sid in self.active_sessions:
                del self.active_sessions[call_sid]

            # Close the OpenAI connection
            if openai_ws.open:
                await openai_ws.close()

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
            current_transcript = ""
            current_audio_buffer = b""
            response_start_time = None

            async for openai_message in openai_ws:
                response = json.loads(openai_message)

                # Handle audio data
                if (
                    response.get("type") == "response.audio.delta"
                    and "delta" in response
                ):
                    # If this is the first chunk, record start time
                    if response_start_time is None:
                        response_start_time = datetime.now()

                    # Decode, encode, and send audio data to Twilio
                    audio_payload = base64.b64encode(
                        base64.b64decode(response["delta"])
                    ).decode("utf-8")
                    audio_delta = {
                        "event": "media",
                        "streamSid": self.active_sessions[call_sid].get("stream_sid"),
                        "media": {"payload": audio_payload},
                    }
                    await twilio_ws.send_json(audio_delta)

                    # Accumulate audio data for saving later
                    current_audio_buffer += base64.b64decode(response["delta"])

                # Handle speech recognition events
                elif response.get("type") == "input_audio_buffer.speech_started":
                    logger.info(f"Speech started detected for call {call_sid}")

                    # Handle interruption if needed
                    if self.active_sessions[call_sid].get("current_item_id"):
                        # TODO: Implement interruption handling
                        pass

                # Handle transcription events
                elif response.get("type") == "input_audio_buffer.transcription":
                    # Update current transcript
                    if response.get("is_final", False):
                        current_transcript = response.get("text", "")

                        # Store the transcription
                        if transcription_callback:
                            await transcription_callback(
                                call_sid, "user", current_transcript
                            )

                        # Add to conversation history
                        if call_sid in self.active_sessions:
                            self.active_sessions[call_sid]["conversation"].append(
                                {
                                    "speaker": "user",
                                    "text": current_transcript,
                                    "timestamp": datetime.now().isoformat(),
                                }
                            )

                # Handle response done events
                elif response.get("type") == "response.done":
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

                    # Store the response
                    if call_sid in self.active_sessions:
                        self.active_sessions[call_sid]["conversation"].append(
                            {
                                "speaker": "assistant",
                                "text": response_text,
                                "timestamp": datetime.now().isoformat(),
                                "response_time": response_time,
                            }
                        )

                    # Call transcription callback
                    if transcription_callback:
                        await transcription_callback(
                            call_sid, "assistant", response_text, current_audio_buffer
                        )

                    # Reset for next turn
                    current_transcript = ""
                    current_audio_buffer = b""
                    response_start_time = None

        except Exception as e:
            logger.error(
                f"Error processing outgoing audio for call {call_sid}: {str(e)}"
            )

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
        
        Here is the knowledge base you should use to answer questions:
        
        {faq_section}
        
        IVR Script:
        {knowledge_base.get('ivr_script', {}).get('welcome_message', '')}
        
        Important guidelines:
        1. Be empathetic and patient with callers
        2. Provide accurate information based on the knowledge base
        3. If you don't know an answer, admit it and offer to help in other ways
        4. Speak in a natural, conversational tone
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
                await openai_ws.close()

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


# Create a singleton instance
realtime_service = RealtimeService()
