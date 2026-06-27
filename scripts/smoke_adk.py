"""Phase-0 ADK smoke gate: one LlmAgent answers one prompt through a Runner.
Validates the ADK install, the env/auth mapping, and the model id."""
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()
# Env mapping: ADK reads GOOGLE_API_KEY + GOOGLE_GENAI_USE_VERTEXAI; we keep GEMINI_API_KEY.
os.environ.setdefault("GOOGLE_API_KEY", os.getenv("GEMINI_API_KEY", ""))
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")

from google.adk.agents import LlmAgent
from google.adk.runners import InMemoryRunner
from google.genai import types

MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
APP = "adk_smoke"


async def main():
    agent = LlmAgent(
        name="smoke_agent",
        model=MODEL,
        instruction="You are a connectivity probe. Reply with exactly the single word: PONG",
    )
    runner = InMemoryRunner(agent=agent, app_name=APP)
    session = await runner.session_service.create_session(app_name=APP, user_id="u1")
    content = types.Content(role="user", parts=[types.Part(text="ping")])

    final_text = None
    async for event in runner.run_async(user_id="u1", session_id=session.id, new_message=content):
        if event.is_final_response() and event.content and event.content.parts:
            final_text = event.content.parts[0].text

    print("MODEL:", MODEL)
    print("FINAL RESPONSE:", repr(final_text))
    assert final_text and "PONG" in final_text.upper(), "smoke gate FAILED: no PONG"
    print("SMOKE GATE: PASS")


if __name__ == "__main__":
    asyncio.run(main())
