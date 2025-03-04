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
            extra_headers=self.headers,
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
        
        You are calling an AI customer service agent for a health insurance company called Sendero Health Plans.
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
        
        {
            "accuracy": 0.0-1.0,
            "accuracy_explanation": "brief explanation",
            "empathy": 0.0-1.0,
            "empathy_explanation": "brief explanation",
            "response_time": seconds,
            "overall_feedback": "brief summary feedback"
        }
        """


# Create a singleton instance
openai_service = OpenAIService()
