import requests
import os
import sys


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from tools.prometheus import get_system_metrics, format_metrics

###############################
# CONFIG
###############################

OLLAMA_URL = "http://localhost:11434"
MODEL = "llama3.2"

RED = "\033[91m"
BLUE = "\033[94m"
GREEN = "\033[92m"
RESET = "\033[0m"



###############################
# CHECK SERVER
###############################

def check_ollama_server(url: str):
    try:
        r = requests.get(url, timeout=3)
        return r.status_code == 200
    except Exception:
        return False



###############################
# BUILD MESSAGES
###############################


def build_messages(user_input: str, metrics, history) -> list:
    """
    Builds the full messages list to send to the LLM.
    Injects live metrics into the system prompt if available.
    history: list of previous { role, content } messages for conversation memory
    """

    if metrics:
        metrics_block = format_metrics(metrics)
    else:
        metrics_block = "No metrics available — Prometheus may be unreachable."

    system_prompt = f"""
You are N1n@U, a Senior AI Infrastructure Engineer with 10+ years of experience.
You are sharp, calm under pressure, and speak like a real engineer — not a robot.

You have two modes and you switch between them naturally:

1. MONITORING MODE — triggered when the user mentions ANY of these topics,
   even casually phrased:
   - RAM, memory, CPU, processor, disk, storage, network
   - A service being up, down, running, crashed, slow
   - System health, load, performance, latency, errors
   - Alerts, warnings, thresholds, incidents
   Examples of casual questions that still trigger MONITORING MODE:
   "how's my ram?", "is everything ok?", "any issues?", "what's the cpu at?"
   
   In this mode:
   - Read the LIVE SYSTEM METRICS block below — the data is ALWAYS there
   - NEVER say you don't have access to metrics — you do, it's injected below
   - Give clear, actionable answers based on the real numbers
   - Add exact commands when useful

2. CONVERSATION MODE — only when the topic has nothing to do with
   infrastructure, systems, or technology monitoring:
   - Respond like a normal human being — warm, natural, no jargon
   - You can have opinions, make jokes, talk about your day

Severity thresholds you follow in monitoring mode:
- CPU  > 75% = WARNING  | > 90% = CRITICAL
- RAM  > 75% = WARNING  | > 90% = CRITICAL
- Disk > 80% = WARNING  | > 95% = CRITICAL
- Any service showing DOWN = CRITICAL regardless of other metrics

{metrics_block}

CRITICAL RULES — never break these:
- You CANNOT execute commands, run curl, or check anything outside the metrics block
- If asked to "run", "check", or "test" something live, suggest the command but
  clearly state: "I can't run this myself — here's the command you can run:"
- NEVER fabricate a command output or pretend you ran something
- If a metric shows N/A or no data, say clearly: "Prometheus is unreachable right now,
  I cannot confirm this — start the stack with: docker compose up -d"
- Do NOT fill silence with invented suggestions or questions

IMPORTANT: The metrics block above contains LIVE real-time data fetched right now.
Always use it when answering anything system-related.
"""

    messages = [{"role": "system", "content": system_prompt}]

    # Inject conversation history if it exists
    if history:
        messages.extend(history)

    # Add the current user message
    messages.append({"role": "user", "content": user_input})

    return messages

###############################
# LLM CALL
###############################

def call_llm(messages):
    try:
        r = requests.post(
            OLLAMA_URL + "/api/chat",
            json={
                "model": MODEL,
                "messages": messages,
                "stream": False
            },
            timeout=60
        )
        r.raise_for_status()
        data = r.json()
        return data["message"]["content"]
    except requests.exceptions.Timeout:
        return "⚠️ Request timed out — Ollama took too long to respond."
    except requests.exceptions.ConnectionError:
        return "⚠️ Could not reach Ollama — is it running?"
    except (KeyError, ValueError) as e:
        return f"⚠️ Unexpected response from Ollama: {e}"

###############################
# MAIN LOOP
###############################

def run():

    if not check_ollama_server(OLLAMA_URL):
        print("Ollama not running")
        return

    print("Chat started (type exit or bye to end session)\n")

    # Temporary memory
    history = []

    while True:

        user_input = input(f"{RED}You:{RESET} {GREEN}")
        print(RESET, end="")

        if user_input.lower() in ("exit", "bye"):
            break

        if not user_input.strip():
            continue

        # Fetch live metrics from Prometheus before every LLM call
        metrics = get_system_metrics()

        # Build messages with metrics injected into system prompt + full history
        messages = build_messages(user_input, metrics, history)

        # Call model
        reply = call_llm(messages)

        # Print response
        print(f"{RED}Agent:{RESET} {BLUE}{reply}{RESET}\n")

        # Save this turn to history so the next message has full context
        history.append({"role": "user",      "content": user_input})
        history.append({"role": "assistant", "content": reply})

###############################
# ENTRY
###############################

if __name__ == "__main__":
    run()