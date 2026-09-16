# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
# SPDX-License-Identifier: MPL-2.0

from collections import Counter
from datetime import datetime, UTC
import re
import time

from arduino.app_bricks.llm import LargeLanguageModel
from arduino.app_bricks.video_objectdetection import VideoObjectDetection
from arduino.app_bricks.web_ui import WebUI
from arduino.app_peripherals.camera import Camera
from arduino.app_utils import App

DETECTION_CONFIDENCE = 0.5
EVENT_DEBOUNCE_SECONDS = 5
AI_COACHING_INTERVAL_SECONDS = 60

camera = Camera()
ui = WebUI()

detection_stream = VideoObjectDetection(
    camera,
    confidence=DETECTION_CONFIDENCE,
    debounce_sec=0.0
)

llm = LargeLanguageModel()

behavior_history = []
last_coaching_time = time.monotonic()
last_event_time = {}
is_llm_running = False


def aggregate_observations(events, duration_seconds):
    counts = Counter(
        event["label"]
        for event in events
    )

    summary_lines = []

    for label, count in counts.items():
        summary_lines.append(
            f"- {label}: {count} times"
        )

    summary_text = "\n".join(summary_lines)

    return (
        f"Observation period: approximately "
        f"{int(duration_seconds)} seconds\n\n"
        f"Observed activities:\n"
        f"{summary_text}"
    )


def create_coaching_prompt(aggregated_summary):
    return f"""
You are an AI behavioral coach.

Analyze ONLY the observed activities below.

{aggregated_summary}

Your task:
1. Identify one meaningful behavioral pattern.
2. Give one practical wellness recommendation based on that pattern.

Return ONLY these two lines:

Analysis: [one short sentence]
Recommendation: [one short sentence]

Rules:
- Do not repeat the observation data.
- Do not describe the task.
- Do not mention these instructions.
- Do not add headings other than Analysis and Recommendation.
- Do not ask questions.
- Do not invent activities.
- Do not diagnose medical conditions.
- Do not prescribe medication.
- Keep each line under 15 words.
"""


def format_llm_response(raw_text):
    if not raw_text:
        return ""

    text = str(raw_text).strip()
    text = re.sub(r"[*_`#]", "", text) # Remove markdown formatting
    text = text.replace("\r\n", "\n") # Normalize line endings
    text = re.sub(r"\n{2,}", "\n", text) # Remove duplicated blank lines
    lines = [
        line.strip()
        for line in text.split("\n")
        if line.strip()
    ]
    analysis = None
    recommendation = None

    for line in lines:
        lower = line.lower()
        if lower.startswith("analysis:"):
            analysis = line[len("analysis:"):].strip()

        elif lower.startswith("recommendation:"):
            recommendation = line[len("recommendation:"):].strip()

    # If the model followed the requested format
    if analysis and recommendation:
        return (
            f"Analysis: {analysis}\n"
            f"Recommendation: {recommendation}"
        )

    # Try to extract old formats
    pattern = None
    coaching = None

    for line in lines:
        lower = line.lower()
        if lower.startswith("pattern:"):
            pattern = line[len("pattern:"):].strip()

        elif lower.startswith("coaching:"):
            coaching = line[len("coaching:"):].strip()

    if pattern and coaching:
        return (
            f"Analysis: {pattern}\n"
            f"Recommendation: {coaching}"
        )

    # Last fallback
    return text


def run_coaching_sync(events, duration_seconds):
    global is_llm_running
    global last_coaching_time

    if is_llm_running:
        print("[LLM process] LLM is already running..")
        return

    is_llm_running = True

    try:
        print()
        print("[LLM process] ====== STARTING AI BEHAVIORAL COACH ======")

        aggregated_summary = aggregate_observations(events,duration_seconds)

        print()
        print("[LLM process] Aggregated observations:")
        print(aggregated_summary)
        prompt = create_coaching_prompt(aggregated_summary)
        print()
        response = llm.chat(prompt)
        clean_response = format_llm_response(response)
        print("[LLM process] AI Coach response:")
        print(clean_response)

        if clean_response:
            ui.send_message(
                "llm_response",
                {
                    "text": clean_response,
                    "timestamp": datetime.now(UTC).isoformat()
                }
            )
            behavior_history.clear() # Clear observations list after successful response
            last_coaching_time = time.monotonic() # Reset timer

            print(f"[LLM process] Next choaching prompt in {AI_COACHING_INTERVAL_SECONDS} seconds.")

    except Exception as e:
        print(f"[LLM process] LLM coaching failed: {e}")

    finally:
        is_llm_running = False


def on_detection(detections):
    global last_coaching_time

    if is_llm_running:
        return

    current_time = time.monotonic()
    timestamp = datetime.now(UTC).isoformat()

    # Process detections
    for key, values in detections.items():
        if not values:
            continue

        confidence = values[0]["confidence"]

        if confidence < DETECTION_CONFIDENCE:
            continue

        # Send detections to Web UI
        ui.send_message(
            "detection",
            {
                "content": key,
                "confidence": confidence,
                "timestamp": timestamp
            }
        )

        # Debounce object detections
        previous_time = last_event_time.get(key)
        if (
            previous_time is not None
            and current_time - previous_time
            < EVENT_DEBOUNCE_SECONDS
        ):
            continue

        last_event_time[key] = current_time
        behavior_history.append(
            {
                "timestamp": timestamp,
                "label": key
            }
        )

        print(f"[Debug] Detected object recorded {timestamp} | {key}")

    elapsed_seconds = (current_time - last_coaching_time) # Check LLM prompting timer

    if elapsed_seconds < AI_COACHING_INTERVAL_SECONDS:
        return

    # Prompt LLM if observations exist
    if behavior_history:
        events_to_analyze = list(behavior_history)
        run_coaching_sync(events_to_analyze,elapsed_seconds)

    else:
        last_coaching_time = current_time # If no observations

detection_stream.on_detect_all(on_detection)

App.run()
