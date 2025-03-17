# app/models/test_cases.py
from typing import List, Optional, Dict, Any
from pydantic import BaseModel
from uuid import UUID, uuid4
from datetime import datetime


class FAQ(BaseModel):
    question: str
    answer: str


class KnowledgeBase(BaseModel):
    faqs: List[Dict[str, str]]
    ivr_script: Dict[str, str]


class TestCaseConfig(BaseModel):
    persona_name: str = "Tech-Savvy"
    behavior_name: str = "frustrated"
    questions: List[str] = ["How can I find my member ID?"]
    special_instructions: Optional[str] = None
    max_turns: int = 4  # Default max conversation turns after main question


class TestCase(BaseModel):
    id: UUID = uuid4()
    name: str
    description: Optional[str] = None
    config: TestCaseConfig
    created_at: datetime = datetime.now()


class TestSuite(BaseModel):
    id: UUID = uuid4()
    name: str
    description: Optional[str] = None
    test_cases: List[TestCase]
    created_at: datetime = datetime.now()
