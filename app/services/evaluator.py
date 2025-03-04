# app/services/evaluator.py
import asyncio
import json
import logging
import time
import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
import tempfile
import os

from ..models.personas import Persona, Behavior
from ..models.test_cases import TestCase, TestQuestion
from ..models.reports import (
    ConversationTurn,
    EvaluationMetrics,
    QuestionEvaluation,
    TestCaseReport,
)
from ..config import config
from .openai_service import openai_service
from .twilio_service import twilio_service
from .s3_service import s3_service

logger = logging.getLogger(__name__)


class EvaluatorService:
    """Service for executing test cases and evaluating AI call center responses."""

    def __init__(self):
        self.active_tests = {}
        self.knowledge_base = config.load_knowledge_base()
        self.personas_data = config.load_personas()

    def get_persona(self, persona_name: str) -> Optional[Persona]:
        """Get a persona by name."""
        for persona in self.personas_data.get("personas", []):
            if persona.get("name") == persona_name:
                return Persona(
                    name=persona.get("name", ""), traits=persona.get("traits", [])
                )
        return None

    def get_behavior(self, behavior_name: str) -> Optional[Behavior]:
        """Get a behavior by name."""
        for behavior in self.personas_data.get("behaviors", []):
            if behavior.get("name") == behavior_name:
                return Behavior(
                    name=behavior.get("name", ""),
                    characteristics=behavior.get("characteristics", []),
                )
        return None

    async def execute_test_case(self, test_case: TestCase) -> TestCaseReport:
        """
        Execute a test case and generate a report.

        Args:
            test_case: Test case to execute

        Returns:
            TestCaseReport containing evaluation results
        """
        logger.info(f"Executing test case: {test_case.name}")

        start_time = time.time()
        test_id = str(test_case.id)

        # Initialize test tracking
        self.active_tests[test_id] = {
            "test_case": test_case.dict(),
            "status": "started",
            "start_time": start_time,
            "current_question_index": 0,
            "questions_evaluated": [],
        }

        # Save test case configuration
        s3_service.save_test_case(test_case.dict(), test_id)

        # Get persona and behavior
        persona = self.get_persona(test_case.config.persona_name)
        behavior = self.get_behavior(test_case.config.behavior_name)

        if not persona or not behavior:
            logger.error(
                f"Invalid persona or behavior: {test_case.config.persona_name}, {test_case.config.behavior_name}"
            )
            return TestCaseReport(
                test_case_id=test_case.id,
                test_case_name=test_case.name,
                persona_name=test_case.config.persona_name,
                behavior_name=test_case.config.behavior_name,
                questions_evaluated=[],
                overall_metrics=EvaluationMetrics(
                    accuracy=0.0,
                    empathy=0.0,
                    response_time=0.0,
                    successful=False,
                    error_message="Invalid persona or behavior",
                ),
                execution_time=time.time() - start_time,
                special_instructions=test_case.config.special_instructions,
            )

        # Process each question
        questions_evaluated = []
        for i, question in enumerate(test_case.config.questions):
            try:
                # Update current question index
                self.active_tests[test_id]["current_question_index"] = i

                # Evaluate the question
                question_eval = await self.evaluate_question(
                    test_id=test_id,
                    question=question,
                    persona=persona,
                    behavior=behavior,
                    max_turns=test_case.config.max_turns,
                    special_instructions=test_case.config.special_instructions,
                )

                questions_evaluated.append(question_eval)
                self.active_tests[test_id]["questions_evaluated"].append(
                    question_eval.dict()
                )

            except Exception as e:
                logger.error(f"Error evaluating question {i}: {str(e)}")
                # Add a failed question evaluation
                questions_evaluated.append(
                    QuestionEvaluation(
                        question=question.text,
                        conversation=[],
                        metrics=EvaluationMetrics(
                            accuracy=0.0,
                            empathy=0.0,
                            response_time=0.0,
                            successful=False,
                            error_message=f"Failed to evaluate: {str(e)}",
                        ),
                    )
                )

        # Calculate overall metrics
        overall_metrics = self._calculate_overall_metrics(questions_evaluated)

        # Mark test as completed
        execution_time = time.time() - start_time
        self.active_tests[test_id]["status"] = "completed"
        self.active_tests[test_id]["end_time"] = time.time()
        self.active_tests[test_id]["execution_time"] = execution_time

        # Generate report
        report = TestCaseReport(
            test_case_id=test_case.id,
            test_case_name=test_case.name,
            persona_name=test_case.config.persona_name,
            behavior_name=test_case.config.behavior_name,
            questions_evaluated=questions_evaluated,
            overall_metrics=overall_metrics,
            execution_time=execution_time,
            special_instructions=test_case.config.special_instructions,
        )

        # Save report
        report_id = str(report.id)
        s3_service.save_report(report.dict(), report_id)

        # Clean up
        if test_id in self.active_tests:
            del self.active_tests[test_id]

        return report

    async def evaluate_question(
        self,
        test_id: str,
        question: TestQuestion,
        persona: Persona,
        behavior: Behavior,
        max_turns: int = 4,
        special_instructions: Optional[str] = None,
    ) -> QuestionEvaluation:
        """
        Evaluate a single question.

        Args:
            test_id: Test case ID
            question: Question to evaluate
            persona: Persona to use
            behavior: Behavior to simulate
            max_turns: Maximum conversation turns
            special_instructions: Special test instructions

        Returns:
            QuestionEvaluation containing results
        """
        logger.info(f"Evaluating question: {question.text}")

        # Initialize call to AI service
        call_result = twilio_service.initiate_call(test_id)

        if "error" in call_result:
            logger.error(f"Failed to initiate call: {call_result['error']}")
            return QuestionEvaluation(
                question=question.text,
                conversation=[],
                metrics=EvaluationMetrics(
                    accuracy=0.0,
                    empathy=0.0,
                    response_time=0.0,
                    successful=False,
                    error_message=f"Failed to initiate call: {call_result['error']}",
                ),
            )

        call_sid = call_result["call_sid"]

        # Wait for call to be answered
        await self._wait_for_call_status(call_sid, ["in-progress"])

        # Prepare conversation turns
        conversation = []

        # Prepare the question to ask (with special instructions if needed)
        question_to_ask = question.text
        if special_instructions:
            question_to_ask = self._apply_special_instructions(
                question_to_ask, special_instructions
            )

        # Ask the question
        start_time = time.time()

        # Get audio stream from OpenAI for the question
        # In a real implementation, we'd generate audio for the evaluator's question
        # and send it to the Twilio call
        question_audio = self._simulate_question_audio(question_to_ask)

        # Save question audio
        question_audio_url = s3_service.save_audio(
            question_audio, test_id, call_sid, 0, "evaluator"
        )

        # Add question to conversation
        conversation.append(
            ConversationTurn(
                speaker="evaluator",
                text=question_to_ask,
                timestamp=datetime.now(),
                audio_url=question_audio_url,
            )
        )

        # Process the AI's response
        agent_response_text, agent_response_audio = await self._capture_agent_response(
            call_sid
        )

        # Calculate response time
        response_time = time.time() - start_time

        # Save agent response audio
        agent_audio_url = s3_service.save_audio(
            agent_response_audio, test_id, call_sid, 1, "agent"
        )

        # Add agent response to conversation
        conversation.append(
            ConversationTurn(
                speaker="agent",
                text=agent_response_text,
                timestamp=datetime.now(),
                audio_url=agent_audio_url,
            )
        )

        # Handle follow-up questions if needed
        turn_count = 1
        if question.follow_ups and turn_count < max_turns:
            for follow_up in question.follow_ups:
                # Increment turn count
                turn_count += 1

                # Ask follow-up question
                follow_up_audio = self._simulate_question_audio(follow_up)

                # Save follow-up question audio
                follow_up_audio_url = s3_service.save_audio(
                    follow_up_audio, test_id, call_sid, turn_count * 2, "evaluator"
                )

                # Add follow-up to conversation
                conversation.append(
                    ConversationTurn(
                        speaker="evaluator",
                        text=follow_up,
                        timestamp=datetime.now(),
                        audio_url=follow_up_audio_url,
                    )
                )

                # Process the AI's response to follow-up
                agent_follow_up_text, agent_follow_up_audio = (
                    await self._capture_agent_response(call_sid)
                )

                # Save agent follow-up response audio
                agent_follow_up_url = s3_service.save_audio(
                    agent_follow_up_audio,
                    test_id,
                    call_sid,
                    turn_count * 2 + 1,
                    "agent",
                )

                # Add agent follow-up response to conversation
                conversation.append(
                    ConversationTurn(
                        speaker="agent",
                        text=agent_follow_up_text,
                        timestamp=datetime.now(),
                        audio_url=agent_follow_up_url,
                    )
                )

                # Break if we've reached max turns
                if turn_count >= max_turns:
                    break

        # End the call
        twilio_service.end_call(call_sid)

        # Evaluate the conversation
        metrics = await openai_service.evaluate_conversation(
            question=question.text,
            expected_topic=question.expected_topic,
            conversation=[turn.dict() for turn in conversation],
            knowledge_base=self.knowledge_base,
        )

        # Override response time with our measurement
        metrics.response_time = response_time

        return QuestionEvaluation(
            question=question.text, conversation=conversation, metrics=metrics
        )

    def _simulate_question_audio(self, question_text: str) -> bytes:
        """
        Simulate audio for a question (for testing purposes).

        In a real implementation, this would use OpenAI's text-to-speech API
        or another TTS service to generate audio.

        Args:
            question_text: Text of the question

        Returns:
            Simulated audio data
        """
        # This is a placeholder - in a real implementation, we'd generate actual audio
        # using OpenAI's TTS API or another service

        # Create a temporary WAV file with silent audio
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            # Create a silent WAV file (this is just for illustration)
            # In a real implementation, we'd use a proper TTS service
            tmp.write(
                b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00D\xac\x00\x00\x88X\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
            )
            tmp_path = tmp.name

        # Read the file back
        with open(tmp_path, "rb") as f:
            audio_data = f.read()

        # Clean up
        try:
            os.unlink(tmp_path)
        except:
            pass

        return audio_data

    async def _capture_agent_response(self, call_sid: str) -> Tuple[str, bytes]:
        """
        Capture the AI agent's response.

        In a real implementation, this would receive audio from the Twilio call,
        and use OpenAI's Whisper API to transcribe it.

        Args:
            call_sid: Call SID

        Returns:
            Tuple of (transcribed_text, audio_data)
        """
        # This is a placeholder - in a real implementation, we'd capture actual audio
        # from the Twilio call and transcribe it using Whisper

        # Simulate a delay for the agent's response
        await asyncio.sleep(2)

        # Simulate a response text
        response_text = (
            "Thank you for calling Sendero Health Plans. How can I assist you today?"
        )

        # Simulate response audio (silent WAV)
        response_audio = self._simulate_question_audio(response_text)

        return response_text, response_audio

    async def _wait_for_call_status(
        self, call_sid: str, target_statuses: List[str], timeout: int = 30
    ) -> bool:
        """
        Wait for a call to reach a specific status.

        Args:
            call_sid: Call SID
            target_statuses: List of target statuses
            timeout: Timeout in seconds

        Returns:
            True if the status was reached, False if timed out
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            call_status = twilio_service.get_call_status(call_sid)
            status = call_status.get("status")

            if status in target_statuses:
                return True

            # Wait a bit before checking again
            await asyncio.sleep(1)

        return False

    def _apply_special_instructions(self, question: str, instructions: str) -> str:
        """
        Apply special instructions to a question.

        Args:
            question: Original question
            instructions: Special instructions

        Returns:
            Modified question with instructions applied
        """
        if "language" in instructions.lower():
            # Language switching test
            if "spanish" in instructions.lower():
                return f"(Speaking in Spanish) {question}"
            elif "vietnamese" in instructions.lower():
                return f"(Speaking in Vietnamese) {question}"
        elif "urgent" in instructions.lower():
            # Urgent request test
            return f"URGENT: {question}"

        # Default: just return the original question
        return question

    def _calculate_overall_metrics(
        self, questions_evaluated: List[QuestionEvaluation]
    ) -> EvaluationMetrics:
        """
        Calculate overall metrics across all evaluated questions.

        Args:
            questions_evaluated: List of question evaluations

        Returns:
            Overall metrics
        """
        successful_evaluations = [
            q.metrics for q in questions_evaluated if q.metrics.successful
        ]

        if not successful_evaluations:
            return EvaluationMetrics(
                accuracy=0.0,
                empathy=0.0,
                response_time=0.0,
                successful=False,
                error_message="No successful evaluations",
            )

        accuracy = sum(m.accuracy for m in successful_evaluations) / len(
            successful_evaluations
        )
        empathy = sum(m.empathy for m in successful_evaluations) / len(
            successful_evaluations
        )
        response_time = sum(m.response_time for m in successful_evaluations) / len(
            successful_evaluations
        )

        return EvaluationMetrics(
            accuracy=accuracy,
            empathy=empathy,
            response_time=response_time,
            successful=True,
        )


# Create a singleton instance
evaluator_service = EvaluatorService()
