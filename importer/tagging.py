"""
Tag questions with a topic and sub-topic using the Claude API.

How it works
------------
For each question we send Claude the question text (plus the start of its
mark scheme, which often names the concept being tested) and ask it to pick
from the fixed lists in topics.py.

"Structured outputs" (output_config.format) give Claude a JSON schema it must
follow. The topic and sub-topic fields are enums - lists of allowed values -
so Claude can't invent a topic that isn't on the spec. It also returns a
confidence level; anything below "high" is flagged for you to review rather
than trusted.

Cost control
------------
- effort "low": this is a simple classification, so Claude doesn't need to
  think for long.
- The system prompt (instructions + topic list) is identical for every
  question on the same paper, so it's marked for prompt caching.
"""
import json
from dataclasses import dataclass

import anthropic

from importer.topics import FULL_TITLES, topics_for

MODEL = "claude-opus-5-5"


@dataclass
class TagResult:
    topic: str | None
    subtopic: str | None
    confidence: str          # 'high', 'medium', 'low' or 'error'
    reason: str


SUBJECT_NAMES = {
    "geography": "AQA A-level Geography (7037)",
    "computer-science": "OCR A Level Computer Science (H446)",
}


def system_prompt(subject, paper):
    """Instructions plus the topic list for one paper. Identical for every
    question on that paper, which is what lets it be cached."""
    lines = []
    for topic, subtopics in topics_for(subject, paper).items():
        lines.append(f"- {FULL_TITLES.get(topic, topic)}")
        lines += [f"    - {s}" for s in subtopics]
    return (
        f"You classify exam questions from {SUBJECT_NAMES[subject]}, paper {paper}, "
        "into specification topics.\n\n"
        "Choose the single topic and sub-topic that the question mainly assesses. "
        "The topic must be the parent of the sub-topic. Use the question text first; "
        "the mark scheme excerpt shows what the examiners reward. If a section heading "
        "is given, the topic is that heading.\n\n"
        "Set confidence to:\n"
        "- high: the sub-topic is clearly right.\n"
        "- medium: the question spans several sub-topics or two are similarly likely.\n"
        "- low: the text is unclear or garbled, or nothing fits well.\n"
        "Do not guess with high confidence. A wrong 'high' is worse than an honest 'low'.\n\n"
        "Topics and sub-topics:\n" + "\n".join(lines)
    )


def output_schema(subject, paper, heading=None):
    """JSON schema with the allowed values listed as enums."""
    topics = topics_for(subject, paper)
    allowed_topics = [heading] if heading in topics else list(topics)
    subtopics = [s for t in allowed_topics for s in topics[t]]
    return {
        "type": "json_schema",
        "schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "enum": allowed_topics},
                "subtopic": {"type": "string", "enum": subtopics},
                "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                "reason": {"type": "string", "description": "One short sentence."},
            },
            "required": ["topic", "subtopic", "confidence", "reason"],
            "additionalProperties": False,
        },
    }


def tag_question(client, subject, paper, number, marks, text, ms_text="", heading=None):
    """Ask Claude for a topic. Never raises for API problems - returns an 'error' result."""
    user = (
        f"Question {number} ({marks if marks is not None else '?'} marks)\n"
        + (f"Section heading: {heading}\n" if heading else "")
        + f"\nQuestion text:\n{text[:4000]}\n"
        + (f"\nMark scheme excerpt:\n{ms_text[:1500]}\n" if ms_text else "")
    )
    try:
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=[{
                "type": "text",
                "text": system_prompt(subject, paper),
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": user}],
            output_config={"effort": "low", "format": output_schema(subject, paper, heading)},
            # If a safety classifier declines the request, retry it on
            # Anthropic's recommended fallback model instead of failing.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.AuthenticationError:
        raise  # a bad API key affects every question - stop the import
    except anthropic.APIStatusError as e:
        return TagResult(None, None, "error", f"API error {e.status_code}: {e.message}")
    except anthropic.APIConnectionError:
        return TagResult(None, None, "error", "couldn't reach the Claude API")

    if response.stop_reason == "refusal":
        return TagResult(None, None, "error", "Claude declined to classify this question")
    if response.stop_reason == "max_tokens":
        return TagResult(None, None, "error", "response was cut off")
    text_block = next((b.text for b in response.content if b.type == "text"), None)
    try:
        data = json.loads(text_block)
    except (TypeError, json.JSONDecodeError):
        return TagResult(None, None, "error", "couldn't read Claude's answer")

    # The schema allows any sub-topic from the paper, so check it belongs to the topic.
    if data["subtopic"] not in topics_for(subject, paper).get(data["topic"], []):
        return TagResult(data["topic"], None, "low", "sub-topic didn't belong to the chosen topic")
    return TagResult(data["topic"], data["subtopic"], data["confidence"], data["reason"])
