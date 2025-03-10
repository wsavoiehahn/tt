# app/services/openai_service.py
import json
import time
import asyncio
import websockets
import logging
from typing import Dict, Any, List, Optional, Tuple
import requests
from pydantic import BaseModel

from ..config import config
from ..models.personas import Persona, Behavior
from ..models.reports import EvaluationMetrics

logger = logging.getLogger(__name__)


class OpenAIService:
    """Service for interacting with OpenAI APIs for conversation and evaluation."""

    def __init__(self):
        self.api_key = config.get_parameter("/ai-evaluator/openai_api_key")
        self.realtime_url = "wss://api.openai.com/v1/realtime"
        self.api_url = "https://api.openai.com/v1/chat/completions"
        self.realtime_model = "gpt-4o-realtime-preview-2024-12-17"
        self.evaluation_model = "gpt-4o-2024-05-13"
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "OpenAI-Beta": "realtime=v1",
        }

    async def transcribe_audio(self, audio_data: bytes) -> str:
        """
        Transcribe audio using OpenAI Whisper API.
        """
        transcription_url = "https://api.openai.com/v1/audio/transcriptions"
        files = {
            "file": ("audio.wav", audio_data, "audio/wav"),
            "model": (None, "whisper-1"),
        }

        headers = {"Authorization": f"Bearer {self.api_key}"}

        response = requests.post(transcription_url, headers=headers, files=files)

        if response.status_code != 200:
            logger.error(f"Error in transcription: {response.text}")
            raise Exception(f"Transcription failed with status {response.status_code}")

        result = response.json()
        return result.get("text", "")

    async def realtime_conversation(
        self,
        audio_stream,
        persona: Persona,
        behavior: Behavior,
        question: str,
        knowledge_base: Dict[str, Any],
    ) -> Tuple[str, bytes]:
        """
        Conduct a real-time conversation with the OpenAI model.

        Args:
            audio_stream: Audio stream from Twilio call
            persona: The persona to use
            behavior: The behavior to simulate
            question: The question to ask
            knowledge_base: The knowledge base for reference

        Returns:
            Tuple of (transcribed_response, audio_response)
        """
        system_prompt = self._create_system_prompt(
            persona, behavior, question, knowledge_base
        )

        async with websockets.connect(
            f"{self.realtime_url}?model={self.realtime_model}",
            additional_headers=self.headers,
        ) as websocket:
            # Send initial system message
            await websocket.send(
                json.dumps({"role": "system", "content": system_prompt})
            )

            # Send the audio stream chunks
            # In a real implementation, this would stream audio in real-time
            for chunk in audio_stream:
                await websocket.send(chunk)

            # Signal end of audio input
            await websocket.send(json.dumps({"type": "end"}))

            # Collect response
            response_audio = b""
            response_text = ""

            while True:
                response = await websocket.recv()
                data = json.loads(response)

                if data.get("type") == "message":
                    response_text += data.get("content", "")
                elif data.get("type") == "audio":
                    response_audio += data.get("audio", b"")
                elif data.get("type") == "end":
                    break

            return response_text, response_audio

    async def evaluate_conversation(
        self,
        question: str,
        expected_topic: Optional[str],
        conversation: List[Dict[str, str]],
        knowledge_base: Dict[str, Any],
    ) -> EvaluationMetrics:
        """
        Evaluate a conversation using OpenAI.

        Args:
            question: The original question asked
            expected_topic: The expected topic from the knowledge base
            conversation: List of conversation turns with speaker and text
            knowledge_base: The knowledge base for reference

        Returns:
            EvaluationMetrics with accuracy and empathy scores
        """
        # Construct the prompt for evaluation
        prompt = self._create_evaluation_prompt(
            question, expected_topic, conversation, knowledge_base
        )

        response = requests.post(
            self.api_url,
            headers=self.headers,
            json={
                "model": self.evaluation_model,
                "messages": [
                    {
                        "role": "system",
                        "content": "You are an expert evaluator of customer service AI agents.",
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
            },
        )

        if response.status_code != 200:
            logger.error(f"Error in evaluation: {response.text}")
            return EvaluationMetrics(
                accuracy=0.0,
                empathy=0.0,
                response_time=0.0,
                successful=False,
                error_message=f"Evaluation failed with status {response.status_code}",
            )

        result = response.json()
        content = result["choices"][0]["message"]["content"]
        try:
            evaluation = json.loads(content)

            return EvaluationMetrics(
                accuracy=float(evaluation.get("accuracy", 0.0)),
                empathy=float(evaluation.get("empathy", 0.0)),
                response_time=float(evaluation.get("response_time", 0.0)),
                successful=True,
            )
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Error parsing evaluation result: {str(e)}")
            return EvaluationMetrics(
                accuracy=0.0,
                empathy=0.0,
                response_time=0.0,
                successful=False,
                error_message=f"Failed to parse evaluation result: {str(e)}",
            )

    def _create_system_prompt(
        self,
        persona: Persona,
        behavior: Behavior,
        question: str,
        knowledge_base: Dict[str, Any],
    ) -> str:
        """Create a system prompt based on persona, behavior, and question."""
        persona_traits = ", ".join(persona.traits)
        behavior_chars = ", ".join(behavior.characteristics)

        faq_section = ""
        for faq_dict in knowledge_base.get("faqs", []):
            for q, a in faq_dict.items():
                faq_section += f"Q: {q}\nA: {a}\n\n"

        return f"""
        You are simulating a customer with the following persona: {persona.name}
        Traits: {persona_traits}
        
        You are currently exhibiting the following behavior: {behavior.name}
        Characteristics: {behavior_chars}
        
        You are calling an AI customer service agent.
        You need to ask about the following question: "{question}"
        
        Use natural, conversational language appropriate for your persona and behavior.
        Respond to the agent's questions and provide information as needed, but stay in character.
        
        Here is the relevant knowledge base that the agent should have access to:
        
        {faq_section}
        
        IVR Script:
        {knowledge_base.get('ivr_script', {}).get('welcome_message', '')}
        """

    def _create_evaluation_prompt(
        self,
        question: str,
        expected_topic: Optional[str],
        conversation: List[Dict[str, str]],
        knowledge_base: Dict[str, Any],
    ) -> str:
        """Create an evaluation prompt based on the conversation and knowledge base."""
        conversation_text = ""
        for turn in conversation:
            speaker = turn["speaker"]
            text = turn["text"]
            conversation_text += f"{speaker}: {text}\n\n"

        # Extract relevant information from knowledge base
        relevant_answer = ""
        if expected_topic:
            for faq_dict in knowledge_base.get("faqs", []):
                for q, a in faq_dict.items():
                    if expected_topic.lower() in q.lower():
                        relevant_answer = a
                        break

        return f"""
        Please evaluate this customer service conversation between an AI agent and a customer.
        
        Original customer question: "{question}"
        Expected topic: "{expected_topic if expected_topic else 'Not specified'}"
        
        Conversation transcript:
        {conversation_text}
        
        Relevant information from knowledge base:
        {relevant_answer if relevant_answer else "Not specified"}
        
        Evaluate the conversation on the following metrics:
        
        1. Accuracy (0-1 scale): 
           - Did the agent provide correct information based on the knowledge base?
           - Did they address the customer's question completely?
           - Did they avoid providing incorrect information?
        
        2. Empathy (0-1 scale):
           - Did the agent acknowledge the customer's feelings and situation?
           - Did they use appropriate tone and language for the customer's behavior?
           - Did they show understanding and patience?
        
        3. Response time:
           - Estimate the average response time in seconds (based on conversation flow)
        
        Provide your evaluation in JSON format with ratings and brief explanations:
        
        {{
            "accuracy": 0.0-1.0,
            "accuracy_explanation": "brief explanation",
            "empathy": 0.0-1.0,
            "empathy_explanation": "brief explanation",
            "response_time": seconds,
            "overall_feedback": "brief summary feedback"
        }}
        """

    async def text_to_speech(self, text: str, voice: str = "nova") -> bytes:
        """
        Convert text to speech using OpenAI's TTS API with enhanced error handling.

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

            # Log key details (first 5 chars for debugging)
            logger.error(f"TTS: Using API key (first 5): {self.api_key[:5]}")
            logger.error(
                f"TTS: Converting text to speech: '{text[:50]}...' using voice: {voice}"
            )

            # Ensure text isn't empty
            if not text or len(text.strip()) == 0:
                logger.error(f"TTS ERROR: Empty text provided for TTS")
                return self._generate_fallback_audio("Error: Empty text provided")

            data = {
                "model": "tts-1",
                "input": text,
                "voice": voice,
                "response_format": "mp3",
            }

            logger.error(f"TTS: Sending request to OpenAI TTS API")
            start_time = time.time()
            response = requests.post(url, headers=headers, json=data, timeout=30)
            duration = time.time() - start_time
            logger.error(
                f"TTS: Received response in {duration:.2f} seconds with status: {response.status_code}"
            )

            if response.status_code != 200:
                logger.error(
                    f"TTS ERROR: Non-200 response from OpenAI: {response.status_code}"
                )
                logger.error(f"TTS ERROR: Response content: {response.text}")
                return self._generate_fallback_audio(
                    f"Error: TTS API returned status {response.status_code}"
                )

            # Get content length
            content_length = len(response.content)
            logger.error(f"TTS: Received audio content of size: {content_length} bytes")

            # Validate we got actual audio data
            if content_length < 100:  # Suspiciously small for audio
                logger.error(
                    f"TTS WARNING: Received suspiciously small audio data: {content_length} bytes"
                )
                logger.error(
                    f"TTS WARNING: First 50 bytes: {response.content[:50].hex()}"
                )

                # Check if this looks like JSON error instead of audio
                try:
                    if response.content.startswith(b"{"):
                        error_json = response.json()
                        logger.error(
                            f"TTS ERROR: Received JSON error instead of audio: {error_json}"
                        )
                        return self._generate_fallback_audio(
                            "Error: Received JSON error instead of audio"
                        )
                except:
                    pass

            # Verify this is actually an MP3 (should start with ID3 or with MP3 sync word 0xFF 0xFB)
            if not (
                response.content.startswith(b"ID3")
                or (len(response.content) > 2 and response.content[:2] == b"\xff\xfb")
            ):
                logger.error(
                    f"TTS WARNING: Response does not appear to be valid MP3 data. First 10 bytes: {response.content[:10].hex()}"
                )

            # Log success
            logger.error(f"TTS: Successfully received audio data. Converting to WAV.")

            # Convert MP3 to WAV for Twilio's mulaw format
            mp3_audio = response.content

            # Use the audio conversion utility from utils.audio
            try:
                from ..utils.audio import convert_mp3_to_wav

                wav_audio = convert_mp3_to_wav(mp3_audio)
                logger.error(f"TTS: Converted to WAV of size: {len(wav_audio)} bytes")

                # Verify WAV header
                if (
                    len(wav_audio) > 12
                    and wav_audio[:4] == b"RIFF"
                    and wav_audio[8:12] == b"WAVE"
                ):
                    logger.error(f"TTS: WAV file appears valid (has RIFF/WAVE header)")
                else:
                    logger.error(
                        f"TTS WARNING: Converted WAV may be invalid. First 12 bytes: {wav_audio[:12].hex()}"
                    )

                return wav_audio
            except Exception as conv_error:
                logger.error(
                    f"TTS ERROR: Failed to convert MP3 to WAV: {str(conv_error)}"
                )
                import traceback

                logger.error(f"TTS CONVERSION ERROR: {traceback.format_exc()}")

                # Return the MP3 audio as fallback (might work with some Twilio configurations)
                logger.error(f"TTS: Returning original MP3 audio as fallback")
                return mp3_audio

        except Exception as e:
            logger.error(f"TTS ERROR: Failed to generate speech: {str(e)}")
            import traceback

            logger.error(f"TTS ERROR TRACEBACK: {traceback.format_exc()}")
            return self._generate_fallback_audio(f"Error generating speech: {str(e)}")

    def _generate_fallback_audio(
        self, error_message: str = "Audio generation failed"
    ) -> bytes:
        """Generate a simple audio fallback when TTS fails."""
        logger.error(
            f"TTS FALLBACK: Generating fallback audio for message: '{error_message}'"
        )

        try:
            import struct
            import math

            # Generate 1 second of a simple tone (8kHz, mono, 8-bit)
            rate = 8000
            duration = 2.0  # 2 seconds
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

            logger.error(
                f"TTS FALLBACK: Generated fallback audio of size {len(audio_data)} bytes"
            )
            return bytes(audio_data)
        except Exception as e:
            logger.error(
                f"TTS FALLBACK ERROR: Failed to generate fallback audio: {str(e)}"
            )
            # Return the smallest valid WAV file possible
            logger.error(f"TTS FALLBACK: Returning minimal WAV file")
            return b"RIFF\x24\x00\x00\x00WAVE\x10\x00\x00\x00\x01\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01\x00\x08\x00data\x00\x00\x00\x00"


# Create a singleton instance
openai_service = OpenAIService()
