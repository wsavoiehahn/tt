# app/utils/conversation.py
import re
import logging
import string
from typing import List, Dict, Any, Optional, Tuple
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)


def calculate_string_similarity(str1: str, str2: str) -> float:
    """
    Calculate the similarity between two strings using SequenceMatcher.

    Args:
        str1: First string
        str2: Second string

    Returns:
        Similarity score between 0.0 and 1.0
    """
    # Normalize strings
    str1 = normalize_text(str1)
    str2 = normalize_text(str2)

    # Calculate similarity
    matcher = SequenceMatcher(None, str1, str2)
    return matcher.ratio()


def normalize_text(text: str) -> str:
    """
    Normalize text by removing punctuation, extra whitespace, and converting to lowercase.

    Args:
        text: Input text

    Returns:
        Normalized text
    """
    # Convert to lowercase
    text = text.lower()

    # Remove punctuation
    translator = str.maketrans("", "", string.punctuation)
    text = text.translate(translator)

    # Remove extra whitespace
    text = " ".join(text.split())

    return text


def extract_question_topic(question: str) -> Optional[str]:
    """
    Extract the main topic from a question.

    Args:
        question: The question text

    Returns:
        The main topic, or None if not found
    """
    # Common patterns for topics in questions
    patterns = [
        r"how (can|do) I (find|get|access|retrieve|view) (my|the) ([a-zA-Z\s]+)",
        r"where (can|do) I (find|get|access|retrieve|view) (my|the) ([a-zA-Z\s]+)",
        r"what (is|are) (my|the) ([a-zA-Z\s]+)",
        r"how (to|do I) ([a-zA-Z\s]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, question, re.IGNORECASE)
        if match:
            # The last group usually contains the topic
            return match.group(match.lastindex)

    # Fall back to keywords
    keywords = [
        "card",
        "ID",
        "member",
        "portal",
        "login",
        "password",
        "reset",
        "payment",
        "bill",
    ]
    for keyword in keywords:
        if keyword.lower() in question.lower():
            return keyword

    return None


def find_relevant_faq(
    question: str, faqs: List[Dict[str, str]]
) -> Optional[Tuple[str, str]]:
    """
    Find the most relevant FAQ for a question.

    Args:
        question: The question text
        faqs: List of FAQs, each a dictionary with question-answer pairs

    Returns:
        Tuple of (question, answer) from the most relevant FAQ, or None if no good match
    """
    best_match = None
    best_score = 0.0

    for faq_dict in faqs:
        for q, a in faq_dict.items():
            similarity = calculate_string_similarity(question, q)
            if similarity > best_score:
                best_score = similarity
                best_match = (q, a)

    # Only return if the match is good enough
    if best_score > 0.6 and best_match:
        return best_match

    return None


def check_question_answered(
    question: str, conversation: List[Dict[str, str]], knowledge_base: Dict[str, Any]
) -> float:
    """
    Check if a question was correctly answered in the conversation.

    Args:
        question: The original question
        conversation: List of conversation turns
        knowledge_base: Knowledge base with FAQs

    Returns:
        Score from 0.0 to 1.0 indicating how well the question was answered
    """
    # Find relevant FAQ in knowledge base
    faqs = knowledge_base.get("faqs", [])
    faq_match = find_relevant_faq(question, faqs)

    if not faq_match:
        # No relevant FAQ found
        return 0.5  # Neutral score

    expected_answer = faq_match[1]

    # Look for agent responses
    agent_responses = [
        turn["text"] for turn in conversation if turn["speaker"] == "agent"
    ]

    if not agent_responses:
        return 0.0  # No agent responses found

    # Calculate the best match between any agent response and the expected answer
    best_match = max(
        calculate_string_similarity(resp, expected_answer) for resp in agent_responses
    )

    return best_match


def detect_empathy_markers(text: str) -> List[str]:
    """
    Detect empathy markers in text.

    Args:
        text: Text to analyze

    Returns:
        List of detected empathy markers
    """
    empathy_markers = []

    # Check for acknowledgment phrases
    acknowledgment_phrases = [
        "I understand",
        "I hear you",
        "I see",
        "I get it",
        "I appreciate",
        "Thanks for",
        "Thank you for",
    ]

    for phrase in acknowledgment_phrases:
        if phrase.lower() in text.lower():
            empathy_markers.append(f"Acknowledgment: '{phrase}'")

    # Check for apology phrases
    apology_phrases = ["I'm sorry", "I apologize", "We apologize"]

    for phrase in apology_phrases:
        if phrase.lower() in text.lower():
            empathy_markers.append(f"Apology: '{phrase}'")

    # Check for personalization
    if re.search(r"your (account|card|ID|information|request)", text, re.IGNORECASE):
        empathy_markers.append("Personalization: Using 'your'")

    # Check for reassurance
    reassurance_phrases = ["don't worry", "we'll help", "I'll help", "we can assist"]

    for phrase in reassurance_phrases:
        if phrase.lower() in text.lower():
            empathy_markers.append(f"Reassurance: '{phrase}'")

    return empathy_markers


def calculate_empathy_score(conversation: List[Dict[str, str]]) -> float:
    """
    Calculate an empathy score based on conversation.

    Args:
        conversation: List of conversation turns

    Returns:
        Empathy score from 0.0 to 1.0
    """
    agent_responses = [
        turn["text"] for turn in conversation if turn["speaker"] == "agent"
    ]

    if not agent_responses:
        return 0.0  # No agent responses found

    # Count empathy markers across all responses
    total_markers = 0
    for response in agent_responses:
        markers = detect_empathy_markers(response)
        total_markers += len(markers)

    # Calculate score (more markers = higher score, up to a maximum)
    max_expected_markers = 2 * len(agent_responses)  # Expect about 2 per response
    score = min(1.0, total_markers / max_expected_markers)

    return score


def generate_follow_up_questions(question: str, agent_response: str) -> List[str]:
    """
    Generate follow-up questions based on initial question and agent response.

    Args:
        question: Initial question
        agent_response: Agent's response

    Returns:
        List of follow-up questions
    """
    # Extract topic from the question
    topic = extract_question_topic(question)

    # Common follow-up patterns
    follow_ups = []

    # General follow-ups
    follow_ups.append("Can you explain that in more detail?")

    # ID Card follow-ups
    if "ID" in question or "card" in question.lower():
        follow_ups.append("How long will it take to receive my ID card?")
        follow_ups.append("What if the information on my ID card is incorrect?")

    # Portal follow-ups
    if "portal" in question.lower() or "login" in question.lower():
        follow_ups.append("What if I forgot my password for the portal?")
        follow_ups.append("Can I access the portal from my mobile device?")

    # Payment follow-ups
    if "payment" in question.lower() or "bill" in question.lower():
        follow_ups.append("What payment methods do you accept?")
        follow_ups.append("How can I set up automatic payments?")

    # Specific follow-ups based on agent response
    if "email" in agent_response.lower():
        follow_ups.append("What if I didn't receive the email?")

    if "website" in agent_response.lower():
        follow_ups.append("Can you give me the exact URL for the website?")

    # Return unique follow-ups (up to 3)
    unique_follow_ups = list(set(follow_ups))
    return unique_follow_ups[:3]
