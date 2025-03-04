# tests/test_evaluator.py
import unittest
import asyncio
import json
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime
from uuid import uuid4

from app.services.evaluator import evaluator_service
from app.models.personas import Persona, Behavior
from app.models.test_cases import TestCase, TestCaseConfig, TestQuestion
from app.models.reports import EvaluationMetrics


class TestEvaluatorService(unittest.TestCase):
    """Test cases for the EvaluatorService."""

    def setUp(self):
        """Set up test fixtures."""
        # Create test data
        self.test_id = str(uuid4())
        self.test_persona = Persona(name="Test Persona", traits=["trait1", "trait2"])
        self.test_behavior = Behavior(
            name="Test Behavior", characteristics=["characteristic1", "characteristic2"]
        )

        self.test_question = TestQuestion(
            text="How can I find my Member ID card?", expected_topic="Member ID Card"
        )

        self.test_config = TestCaseConfig(
            persona_name="Test Persona",
            behavior_name="Test Behavior",
            questions=[self.test_question],
            max_turns=2,
        )

        self.test_case = TestCase(
            id=uuid4(),
            name="Test Case",
            description="A test case",
            config=self.test_config,
        )

        # Mock knowledge base
        self.test_kb = {
            "faqs": [
                {
                    "How can I find my Sendero Health Plan Member ID Card?": "You should have received an email with your digital ID cards on Dec. 24 or 26, 2024."
                }
            ],
            "ivr_script": {
                "welcome_message": "Thank you for calling Sendero Health Plans."
            },
        }

        # Set up mocks
        self._setup_mocks()

    def _setup_mocks(self):
        """Set up mock objects for testing."""
        # Mock the evaluator service dependencies
        evaluator_service.knowledge_base = self.test_kb
        evaluator_service.personas_data = {
            "personas": [self.test_persona.dict()],
            "behaviors": [self.test_behavior.dict()],
        }

        # Mock the active tests dict
        evaluator_service.active_tests = {}

    @patch("app.services.s3_service.s3_service")
    @patch("app.services.twilio_service.twilio_service")
    @patch("app.services.openai_service.openai_service")
    def test_get_persona(self, mock_openai, mock_twilio, mock_s3):
        """Test getting a persona by name."""
        # Test with existing persona
        persona = evaluator_service.get_persona("Test Persona")
        self.assertEqual(persona.name, "Test Persona")
        self.assertEqual(persona.traits, ["trait1", "trait2"])

        # Test with non-existent persona
        persona = evaluator_service.get_persona("Non-existent Persona")
        self.assertIsNone(persona)

    @patch("app.services.s3_service.s3_service")
    @patch("app.services.twilio_service.twilio_service")
    @patch("app.services.openai_service.openai_service")
    def test_get_behavior(self, mock_openai, mock_twilio, mock_s3):
        """Test getting a behavior by name."""
        # Test with existing behavior
        behavior = evaluator_service.get_behavior("Test Behavior")
        self.assertEqual(behavior.name, "Test Behavior")
        self.assertEqual(
            behavior.characteristics, ["characteristic1", "characteristic2"]
        )

        # Test with non-existent behavior
        behavior = evaluator_service.get_behavior("Non-existent Behavior")
        self.assertIsNone(behavior)

    @patch("app.services.s3_service.s3_service")
    @patch("app.services.twilio_service.twilio_service")
    @patch("app.services.openai_service.openai_service")
    def test_calculate_overall_metrics(self, mock_openai, mock_twilio, mock_s3):
        """Test calculating overall metrics."""
        from app.models.reports import (
            QuestionEvaluation,
            ConversationTurn,
            EvaluationMetrics,
        )

        # Create test question evaluations
        question_evals = [
            QuestionEvaluation(
                question="Question 1",
                conversation=[
                    ConversationTurn(
                        speaker="evaluator", text="Hello", timestamp=datetime.now()
                    )
                ],
                metrics=EvaluationMetrics(
                    accuracy=0.8, empathy=0.7, response_time=2.0, successful=True
                ),
            ),
            QuestionEvaluation(
                question="Question 2",
                conversation=[
                    ConversationTurn(
                        speaker="evaluator",
                        text="How are you?",
                        timestamp=datetime.now(),
                    )
                ],
                metrics=EvaluationMetrics(
                    accuracy=0.9, empathy=0.6, response_time=1.5, successful=True
                ),
            ),
        ]

        # Calculate overall metrics
        overall_metrics = evaluator_service._calculate_overall_metrics(question_evals)

        # Check results
        self.assertAlmostEqual(overall_metrics.accuracy, 0.85)  # (0.8 + 0.9) / 2
        self.assertAlmostEqual(overall_metrics.empathy, 0.65)  # (0.7 + 0.6) / 2
        self.assertAlmostEqual(overall_metrics.response_time, 1.75)  # (2.0 + 1.5) / 2
        self.assertTrue(overall_metrics.successful)

    @patch("app.services.s3_service.s3_service")
    @patch("app.services.twilio_service.twilio_service")
    @patch("app.services.openai_service.openai_service")
    def test_apply_special_instructions(self, mock_openai, mock_twilio, mock_s3):
        """Test applying special instructions to a question."""
        # Test language switching
        question = "How can I find my ID card?"

        result = evaluator_service._apply_special_instructions(
            question, "Test language switching to Spanish"
        )
        self.assertEqual(result, "(Speaking in Spanish) How can I find my ID card?")

        # Test urgent flag
        result = evaluator_service._apply_special_instructions(
            question, "Test urgent request"
        )
        self.assertEqual(result, "URGENT: How can I find my ID card?")

        # Test with no special instructions
        result = evaluator_service._apply_special_instructions(
            question, "Regular test case"
        )
        self.assertEqual(result, "How can I find my ID card?")

    @patch("app.services.s3_service.s3_service")
    @patch("app.services.twilio_service.twilio_service")
    @patch("app.services.openai_service.openai_service")
    @patch("app.services.evaluator.EvaluatorService._capture_agent_response")
    @patch("app.services.evaluator.EvaluatorService._wait_for_call_status")
    async def test_evaluate_question(
        self, mock_wait, mock_capture, mock_openai, mock_twilio, mock_s3
    ):
        """Test evaluating a question."""
        # Configure mocks
        mock_wait.return_value = True
        mock_capture.return_value = (
            "Thank you for calling Sendero Health Plans.",
            b"audio_data",
        )

        mock_twilio.initiate_call.return_value = {
            "call_sid": "test_call_sid",
            "status": "queued",
        }

        mock_s3.save_audio.return_value = "s3://bucket/audio.wav"

        mock_openai.evaluate_conversation = AsyncMock()
        mock_openai.evaluate_conversation.return_value = EvaluationMetrics(
            accuracy=0.9, empathy=0.8, response_time=1.5, successful=True
        )

        # Test evaluating a question
        question_eval = await evaluator_service.evaluate_question(
            test_id=self.test_id,
            question=self.test_question,
            persona=self.test_persona,
            behavior=self.test_behavior,
            max_turns=2,
        )

        # Verify results
        self.assertEqual(question_eval.question, "How can I find my Member ID card?")
        self.assertEqual(len(question_eval.conversation), 2)  # Question and response
        self.assertEqual(question_eval.conversation[0].speaker, "evaluator")
        self.assertEqual(question_eval.conversation[1].speaker, "agent")
        self.assertEqual(question_eval.metrics.accuracy, 0.9)
        self.assertEqual(question_eval.metrics.empathy, 0.8)

        # Verify call was initiated
        mock_twilio.initiate_call.assert_called_once_with(self.test_id)

        # Verify wait for call status
        mock_wait.assert_called_once()

        # Verify conversation evaluation
        mock_openai.evaluate_conversation.assert_called_once()


# Run tests if this file is executed directly
if __name__ == "__main__":
    unittest.main()
