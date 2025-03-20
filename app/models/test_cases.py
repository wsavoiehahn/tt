# app/models/test_cases.py
from typing import List, Optional, Dict
from pydantic import BaseModel, Field
from uuid import UUID, uuid4
from datetime import datetime


class FAQ(BaseModel):
    question: str
    answer: str


class KnowledgeBase(BaseModel):
    faqs: List[Dict[str, str]]
    ivr_script: Dict[str, str]


class TestCaseConfig(BaseModel):
    persona_name: str = "Tech-Savvy Customer"
    behavior_name: str = "Frustrated"
    question: str = "How can I find my member ID?"
    special_instructions: Optional[str] = None
    max_turns: int = 4  # Default max conversation turns after main question
    faq_question: Optional[str] = ""  # Specific FAQ question to evaluate against
    expected_answer: Optional[str] = ""  # Expected answer for the FAQ question


class TestCase(BaseModel):
    id: UUID = Field(default_factory=uuid4, example=str(uuid4()))
    name: str
    description: Optional[str] = None
    config: TestCaseConfig
    created_at: datetime = Field(default_factory=datetime.now)
