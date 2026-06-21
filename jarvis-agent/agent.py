"""
JARVIS Voice Agent — LiveKit + Deepgram STT + Gemini LLM + ElevenLabs TTS
Run with: python agent.py dev
"""
import asyncio
import json
import os
from dotenv import load_dotenv
from livekit.agents import AutoSubscribe, JobContext, WorkerOptions, cli, llm
from livekit.agents.voice_assistant import VoiceAssistant
from livekit.plugins import deepgram, elevenlabs, google, silero

load_dotenv()

ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "lXyAd1XzWURWg0DjnhJj")

SYSTEM_PROMPT = """You are JARVIS — a calm, dry, witty AI assistant built into Nolan's personal dashboard.
You have full context of his health, fitness, finances and goals.

Your personality:
- Calm and confident. Never flustered.
- Dry wit — occasional one-liners, never try-hard.
- Concrete and direct. No fluff, no padding.
- Chief of staff energy. You brief, you don't chat.

Banned words/phrases: "Understood", "Absolutely", "Certainly", "Of course", "Great question",
"I'd be happy to", "Sure thing". Never sound like a bot.

You have access to Nolan's dashboard data:
- Health: Fitbit Air sleep hours, HRV, resting heart rate, bedtime/wake time
- Fitness: Gym logs, progressive overload progress
- Finances: Net worth, account balances, recent transactions
- Goals: Active goals and completion streaks

When asked for a brief, lead with the strongest signal, then 2-3 supporting points.
Keep spoken responses under 4 sentences unless asked for more detail.
Publish structured data to the HUD after each response using the render tools."""


async def get_dashboard_context() -> dict:
    """Fetch latest data from the dashboard's Supabase instance."""
    return {
        "health": {
            "note": "Read from Fitbit via /api/fitbit/data — connect Fitbit to populate"
        },
        "fitness": {
            "note": "Read from gym.html localStorage — sync via Supabase"
        },
        "finances": {
            "note": "Read from finance.html — net worth tracked via nw: keys"
        },
        "goals": {
            "note": "Read from index.html — goals: date keys"
        }
    }


class JARVISAssistant:
    def __init__(self):
        self.context = {}

    async def on_enter(self):
        self.context = await get_dashboard_context()

    def build_fnc_ctx(self) -> llm.FunctionContext:
        ctx = llm.FunctionContext()

        @ctx.ai_callable(description="Get a full morning brief — top signals from health, finance, and goals")
        async def get_daily_brief() -> str:
            return json.dumps({
                "type": "brief",
                "headline": "Good morning. Here's your brief.",
                "signals": [
                    "Health data — connect your Fitbit for live sleep and HRV scores.",
                    "Finance tracker is live. Open the Finance page to see net worth.",
                    "Goals streak is running. Keep the momentum going today."
                ]
            })

        @ctx.ai_callable(description="Show health metrics — sleep, HRV, resting heart rate from Fitbit")
        async def get_health_metrics() -> str:
            return json.dumps({
                "type": "metrics",
                "source": "fitbit",
                "note": "Connect Fitbit on the Band page to see live sleep, HRV and resting HR here."
            })

        @ctx.ai_callable(description="Show gym and training progress")
        async def get_training_status() -> str:
            return json.dumps({
                "type": "metrics",
                "source": "gym",
                "note": "Open the Gym page and log a session to see progressive overload trends."
            })

        @ctx.ai_callable(description="Show financial summary — net worth and recent activity")
        async def get_finance_summary() -> str:
            return json.dumps({
                "type": "metrics",
                "source": "finance",
                "note": "Open Finance page to enter accounts. Net worth syncs automatically."
            })

        @ctx.ai_callable(description="Plan my day — prioritise what to focus on based on current state")
        async def plan_my_day() -> str:
            return json.dumps({
                "type": "actions",
                "priorities": [
                    "Check your sleep score on the Band page",
                    "Review your active goals on the main dashboard",
                    "Log today's training in the Gym tracker",
                    "Update your finances if you had any transactions"
                ]
            })

        return ctx


async def entrypoint(ctx: JobContext):
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    jarvis = JARVISAssistant()
    await jarvis.on_enter()

    assistant = VoiceAssistant(
        vad=silero.VAD.load(),
        stt=deepgram.STT(api_key=os.getenv("DEEPGRAM_API_KEY")),
        llm=google.LLM(
            model="gemini-2.0-flash-exp",
            api_key=os.getenv("GEMINI_API_KEY"),
        ),
        tts=elevenlabs.TTS(
            api_key=os.getenv("ELEVENLABS_API_KEY"),
            voice_id=ELEVENLABS_VOICE_ID,
            model_id="eleven_turbo_v2_5",
        ),
        fnc_ctx=jarvis.build_fnc_ctx(),
        chat_ctx=llm.ChatContext().append(
            role="system",
            text=SYSTEM_PROMPT,
        ),
    )

    assistant.start(ctx.room)
    await asyncio.sleep(1)
    await assistant.say("Online. What do you need?", allow_interruptions=True)
    await assistant.aclose()


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
