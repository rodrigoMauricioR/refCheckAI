"""Send trimmed video to Gemini and parse a structured verdict."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types

from src import config
from src.rules.registry import Sport


_PROMPT_TEMPLATE = """You are an experienced {role_name} reviewing a {call_kind}.

Watch the clip carefully. First identify the play type and context, then apply
the relevant rule section.

Temporal decision policy (very important):
- Evaluate the full clip before deciding.
- If there are multiple bounces/contacts, judge the LAST decisive play in the
  point shown in this clip (the play that appears to end the point), not the
  first bounce.
- For tennis line calls, this usually means the final landing/event in the
  visible rally segment, unless the clip clearly shows the point already ended.
- If the clip does not clearly show which play ends the point, return
  "inconclusive".

Line-call interpretation policy:
- Determine where the LAST decisive bounce/landing is relative to the relevant
  line(s): "in" means touches line or lands inside; "out" means fully outside.
- Return that visual fact in "visual_final_call" (IN/OUT/UNCLEAR).
- Do not assume or infer the on-court call.

Possible play types for this sport: {play_types}
Possible contexts for this sport: {contexts}

Sport-specific scope and caveats:
{notes}

Relevant rules ({sport_display_name}):
{rules}

Return ONLY valid JSON matching exactly this schema:

{{
  "sport": "{sport_key}",
  "play_type": "<one of: {play_types}>",
  "context": "<one of: {contexts}>",
  "decision_event": "final_play" | "unclear",
  "visual_final_call": "IN" | "OUT" | "UNCLEAR",
  "verdict": "fair_call" | "bad_call" | "inconclusive",
  "headline": "<8 words max, plain language>",
  "reasoning": "<2-3 sentence explanation grounded in what you saw and which rule section applied>",
  "key_observations": [
    "<short bullet>",
    "<short bullet>",
    "<short bullet>"
  ],
  "rule_cited": "<one short sentence quoting or paraphrasing the relevant rule SECTION you used>",
  "confidence": <number between 0 and 1>
}}

Verdict semantics for this model output:
- Return your independent visual judgment via "visual_final_call".
- The app will compare your visual judgment against the on-court call outside
  this prompt to determine fair_call vs bad_call.
- Use "inconclusive" only when visual evidence is insufficient.

If your confidence is below 0.5, the verdict MUST be "inconclusive".
If the clip is not the kind of call this tool judges (see scope above), return
"inconclusive" with headline indicating that.
If an earlier bounce appears but play continues, do not base the verdict on
that earlier bounce; use the final decisive play visible in the clip.
When the final decisive play is clearly visible, set "decision_event" to
"final_play". If not clear, set it to "unclear" and verdict to "inconclusive".
Do not include any text outside the JSON object.
"""


def _client(api_key_override: str | None = None) -> genai.Client:
    key = api_key_override or config.GEMINI_API_KEY
    if not key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Copy .env.example to .env and paste your key."
        )
    return genai.Client(api_key=key)


def _wait_for_file_active(client: genai.Client, file_obj, timeout_s: int = 60):
    """Gemini needs a moment to process uploaded video before it can be referenced."""
    deadline = time.time() + timeout_s
    while file_obj.state.name == "PROCESSING":
        if time.time() > deadline:
            raise TimeoutError("Gemini took too long to process the uploaded video.")
        time.sleep(1)
        file_obj = client.files.get(name=file_obj.name)
    if file_obj.state.name != "ACTIVE":
        raise RuntimeError(f"Uploaded video ended in state {file_obj.state.name}")
    return file_obj


def _build_prompt(sport: Sport, rules_text: str) -> str:
    return _PROMPT_TEMPLATE.format(
        role_name=sport.role_name,
        call_kind=sport.call_kind,
        play_types=", ".join(sport.play_types),
        contexts=", ".join(sport.contexts),
        notes=sport.notes,
        rules=rules_text,
        sport_display_name=sport.display_name,
        sport_key=sport.key,
    )


def analyze_video(
    clip_path: Path,
    rules_text: str,
    original_call: str,
    sport: Sport,
    api_key_override: str | None = None,
) -> dict[str, Any]:
    """Upload `clip_path` to Gemini and return a parsed verdict dict."""
    client = _client(api_key_override)

    uploaded = client.files.upload(file=str(clip_path))
    uploaded = _wait_for_file_active(client, uploaded)

    prompt = _build_prompt(sport, rules_text)

    response = client.models.generate_content(
        model=config.GEMINI_MODEL,
        contents=[uploaded, prompt],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.2,
        ),
    )

    try:
        client.files.delete(name=uploaded.name)
    except Exception:
        pass

    parsed = _parse_json_response(response.text)
    result = _validate_result(parsed, sport, original_call)

    # If language indicates contradictory visual claims, ask once more with a
    # focused self-consistency check. If still contradictory, degrade to
    # inconclusive instead of returning a likely wrong confident call.
    if _has_reasoning_contradiction(result):
        repair_prompt = (
            prompt
            + "\n\nSelf-check before finalizing:\n"
              "- Your reasoning must be internally consistent.\n"
              "- Do not say the ball is outside while also claiming it touched the line.\n"
              "- If visual evidence is ambiguous, set visual_final_call=UNCLEAR and verdict=inconclusive.\n"
        )
        repaired = client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=[uploaded, repair_prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.0,
            ),
        )
        repaired_result = _validate_result(
            _parse_json_response(repaired.text), sport, original_call
        )
        if _has_reasoning_contradiction(repaired_result):
            repaired_result["decision_event"] = "unclear"
            repaired_result["visual_final_call"] = "UNCLEAR"
            repaired_result["verdict"] = "inconclusive"
            repaired_result["confidence"] = min(float(repaired_result.get("confidence", 0.0)), 0.49)
            repaired_result["headline"] = "Ambiguous line contact"
            repaired_result["reasoning"] = (
                "The visual explanation contained contradictory line-contact cues, "
                "so this review is marked inconclusive."
            )
        result = repaired_result

    return result


def _parse_json_response(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Gemini returned non-JSON: {text[:500]}") from e


def _validate_result(result: dict[str, Any], sport: Sport, original_call: str) -> dict[str, Any]:
    """Normalize and enforce required result contract."""
    if not isinstance(result, dict):
        raise RuntimeError("Model result is not a JSON object.")

    required = [
        "sport",
        "play_type",
        "context",
        "decision_event",
        "visual_final_call",
        "verdict",
        "headline",
        "reasoning",
        "rule_cited",
        "confidence",
    ]
    missing = [k for k in required if k not in result]
    if missing:
        raise RuntimeError(f"Model response missing required fields: {missing}")

    if result["sport"] != sport.key:
        raise RuntimeError(f"Model returned wrong sport '{result['sport']}' for '{sport.key}'.")

    if result["play_type"] not in sport.play_types:
        raise RuntimeError(f"Invalid play_type: {result['play_type']}")
    if result["context"] not in sport.contexts:
        raise RuntimeError(f"Invalid context: {result['context']}")
    if result["decision_event"] not in {"final_play", "unclear"}:
        raise RuntimeError(f"Invalid decision_event: {result['decision_event']}")
    if result["visual_final_call"] not in {"IN", "OUT", "UNCLEAR"}:
        raise RuntimeError(f"Invalid visual_final_call: {result['visual_final_call']}")
    if result["verdict"] not in {"fair_call", "bad_call", "inconclusive"}:
        raise RuntimeError(f"Invalid verdict: {result['verdict']}")

    try:
        conf = float(result["confidence"])
    except Exception as e:
        raise RuntimeError("Model confidence is not numeric.") from e
    conf = max(0.0, min(1.0, conf))
    result["confidence"] = conf

    if (
        conf < 0.5
        or result["decision_event"] == "unclear"
        or result["visual_final_call"] == "UNCLEAR"
    ):
        result["verdict"] = "inconclusive"
    else:
        normalized_original = original_call.upper()
        result["verdict"] = (
            "fair_call"
            if result["visual_final_call"] == normalized_original
            else "bad_call"
        )

    return result


def _has_reasoning_contradiction(result: dict[str, Any]) -> bool:
    """Heuristic contradiction detector for line-contact language."""
    reasoning = str(result.get("reasoning", "")).lower()
    obs = " ".join(str(x).lower() for x in result.get("key_observations", []) if isinstance(x, str))
    text = f"{reasoning} {obs}"

    has_outside_claim = bool(re.search(r"\b(outside|out\b|beyond the line)\b", text))
    has_touch_line_claim = bool(
        re.search(r"\b(touch(?:ed|es)? (?:the )?line|clipped (?:the )?line|on the line)\b", text)
    )
    return has_outside_claim and has_touch_line_claim
