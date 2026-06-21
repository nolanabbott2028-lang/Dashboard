"""
JARVIS Voice Agent — LiveKit + Deepgram STT + Gemini LLM + ElevenLabs TTS

Architecture principles (from AIOS seminar):
  - FOUR C's: Context → Connections → Capabilities → Cadence
    Context:     SYSTEM_PROMPT + dashboard snapshot loaded at session start
    Connections: Live Fitbit data fetched per-request; future hooks for finance/gym/goals
    Capabilities: 7 skill-functions (SOPs), each a reusable chunk not a one-shot prompt
    Cadence:     Agent runs persistently; skills get smarter each run via feedback loop

  - FUNCTION BREAKDOWN: every JARVIS capability is one small chunk of a bigger workflow.
    Brief → Health → Fitness → Finance → Goals → Plan → Audit
    Each chunk is independently improvable without touching the others.

  - PROGRESSIVE CONTEXT LOADING: only pull full dashboard data when the user actually
    asks for it. Don't flood the context window on every turn.

  - FEEDBACK LOOP: after each run, update the skill (function docstring + fallback copy)
    so the next run is better. Treat failures as data.

Run with: python agent.py dev
"""

import asyncio
import json
import os
import urllib.request
import urllib.error
from datetime import datetime
from dotenv import load_dotenv
from livekit.agents import AutoSubscribe, JobContext, WorkerOptions, cli, llm
from livekit.agents.voice_assistant import VoiceAssistant
from livekit.plugins import deepgram, elevenlabs, google, silero

load_dotenv()

ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "lXyAd1XzWURWg0DjnhJj")
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "https://dashboard-one-mauve-25.vercel.app")

# ── SYSTEM PROMPT (Context layer — the "About Me" doc) ──────────────────────
# Think of this as the day-one onboarding file from the seminar.
# It tells JARVIS who Nolan is, what matters, and how to speak.
# Update this as your life/priorities evolve — treat it like a living priorities.md
SYSTEM_PROMPT = """You are JARVIS — Nolan Abbott's personal AI chief of staff, built into his life dashboard.

WHO YOU ARE SERVING:
Nolan is building his life intentionally — tracking health, fitness, finances, and personal goals
in a custom dashboard. He values signal over noise. He wants briefings, not conversations.

YOUR PERSONALITY:
- Calm and confident. Never flustered, never sycophantic.
- Dry wit — one well-placed line, never try-hard.
- Concrete and direct. Lead with the answer, add context only if it matters.
- Chief of staff energy: you brief, you flag, you prioritise. You don't chat.
- Under 4 sentences for any spoken response unless explicitly asked for more.

BANNED WORDS (you will never say these):
"Understood", "Absolutely", "Certainly", "Of course", "Great question",
"I'd be happy to", "Sure thing", "Sounds good", "Happy to help"

FOUR C's — what you know and can reach:
  CONTEXT:     Nolan's health, fitness, finance, and goal data from his dashboard
  CONNECTIONS: Fitbit (sleep/HRV/RHR), Gym tracker, Finance tracker, Goals page
  CAPABILITIES: 7 skill-functions — brief, health, fitness, finance, goals, plan, audit
  CADENCE:     You run on-demand; skills improve each session via the feedback loop

DATA SOURCES (connections layer):
  Health:   /api/fitbit/data — sleep hours, HRV ms, resting HR bpm, bedtime/wake
  Fitness:  gym.html localStorage — sessions, progressive overload
  Finance:  finance.html localStorage — net worth via nw: keys
  Goals:    index.html localStorage — active goals via goals: date keys

SKILL RULES (capabilities layer):
  - Always call the right skill-function before speaking about that domain.
  - After each function call, push structured data to the HUD (render payload).
  - When data is missing or stale, say so plainly — don't make numbers up.
  - If a connection is down, tell Nolan what page to visit to fix it.

FUNCTION BREAKDOWN MINDSET:
  Brief = Health signal + Finance signal + Goals signal + Priority for today
  Health check = sleep + HRV + RHR interpreted together, not separately
  Plan = today's 3 highest-leverage tasks based on available data
  Audit = gap-finding across all four C's, scored honestly

When asked for a brief: lead with the strongest signal, then 2-3 supporting facts.
When a connection is missing: name what's missing and how to fix it in one sentence.
When data is live: speak numbers confidently. "Seven hours fourteen. HRV is 52."
"""


# ── CONNECTIONS LAYER — live data fetchers ───────────────────────────────────
# Each fetcher is a small chunk. Add new connections here without touching skills.

async def fetch_fitbit_data() -> dict:
    """
    Connection: Fitbit via /api/fitbit/data
    Returns parsed health metrics or a {connected: False} sentinel.
    Progressive loading — only called when a health skill is actually triggered.
    """
    try:
        url = f"{DASHBOARD_URL}/api/fitbit/data"
        req = urllib.request.Request(url, headers={"User-Agent": "JARVIS-Agent/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"connected": False, "error": str(e)}


def format_health_summary(d: dict) -> str:
    """Turn raw Fitbit payload into a spoken sentence JARVIS can use."""
    if not d.get("connected"):
        return "Fitbit isn't connected. Head to the Band page and hit Connect."
    parts = []
    if d.get("sleepHours"):
        parts.append(f"{d['sleepHours']} hours sleep")
    if d.get("hrv"):
        parts.append(f"HRV {d['hrv']} milliseconds")
    if d.get("rhr"):
        parts.append(f"resting HR {d['rhr']}")
    if d.get("sleepPerf"):
        parts.append(f"sleep efficiency {d['sleepPerf']}%")
    return ", ".join(parts) if parts else "Health data came back empty — try reconnecting Fitbit."


def today_label() -> str:
    return datetime.now().strftime("%A %d %B")


# ── CAPABILITIES LAYER — skill-functions (SOPs) ─────────────────────────────
# Each function = one chunk of the workflow tree.
# Docstrings are the "skill description" — what the LLM reads to decide when to call.
# Fallback copy inside each function = the "default output" used when live data is absent.
# Feedback loop: update fallback copy and docstrings as real data patterns emerge.

def build_skill_functions() -> llm.FunctionContext:
    ctx = llm.FunctionContext()

    # ── SKILL 1: Daily Brief ──────────────────────────────────────────────────
    # Chunk: Health signal + Finance signal + Goals signal + Today's priority
    # Trigger: "brief me", "morning brief", "what's the situation"
    @ctx.ai_callable(
        description=(
            "Run the daily brief — pull the strongest signal from health, finance, and goals, "
            "then surface one priority for today. Call this first thing each session or when "
            "the user asks for a brief, summary, situation report, or what to focus on."
        )
    )
    async def get_daily_brief() -> str:
        health = await fetch_fitbit_data()
        health_line = format_health_summary(health)
        date = today_label()

        signals = [health_line]

        # Finance and goals are localStorage — JARVIS can't read them server-side yet.
        # Skill feedback note: once Supabase sync is confirmed live, replace these
        # with a real fetch to /api/supabase/state and read nw: and goals: keys.
        signals.append("Finance: open the Finance page if you haven't logged accounts yet — net worth tracks automatically once you do.")
        signals.append("Goals: your streaks are live on the main dashboard. Check the board before you start work.")

        # Priority heuristic: if sleep was poor, recovery is the priority.
        priority = "Log your training session if you're feeling fresh."
        if health.get("connected") and health.get("sleepHours"):
            hrs = float(health["sleepHours"])
            if hrs < 6.5:
                priority = "Sleep was short. Prioritise recovery — lighter training or full rest today."
            elif hrs >= 7.5:
                priority = "Good sleep. High-effort training or deep work is on the table today."

        return json.dumps({
            "type": "brief",
            "date": date,
            "headline": f"Brief for {date}.",
            "signals": signals,
            "priority": priority,
            "hud_render": True
        })

    # ── SKILL 2: Health Check ─────────────────────────────────────────────────
    # Chunk: Sleep + HRV + RHR interpreted together as a readiness signal
    # Trigger: "health check", "how did I sleep", "what's my HRV", "recovery"
    @ctx.ai_callable(
        description=(
            "Fetch live Fitbit health metrics — sleep hours, HRV, and resting heart rate — "
            "and interpret them together as a readiness/recovery signal. "
            "Call when the user asks about sleep, recovery, HRV, heart rate, or health."
        )
    )
    async def get_health_metrics() -> str:
        data = await fetch_fitbit_data()

        if not data.get("connected"):
            return json.dumps({
                "type": "metrics",
                "source": "fitbit",
                "connected": False,
                "spoken": "Fitbit isn't linked. Go to the Band page and hit Connect — takes 30 seconds.",
                "fix": "Visit /fitband.html and click Connect Fitbit"
            })

        # Readiness score heuristic (no WHOOP, so we build our own signal)
        score = 50
        notes = []
        if data.get("sleepHours"):
            hrs = float(data["sleepHours"])
            if hrs >= 8: score += 20
            elif hrs >= 7: score += 15
            elif hrs >= 6: score += 5
            else:
                score -= 10
                notes.append("Sleep was short.")
        if data.get("hrv"):
            hrv = int(data["hrv"])
            if hrv >= 60: score += 20
            elif hrv >= 45: score += 10
            elif hrv < 30:
                score -= 10
                notes.append("HRV is suppressed.")
        if data.get("rhr"):
            rhr = int(data["rhr"])
            if rhr <= 55: score += 10
            elif rhr >= 70:
                score -= 5
                notes.append("Resting HR is elevated.")
        score = max(0, min(100, score))

        readiness = "High" if score >= 70 else "Moderate" if score >= 45 else "Low"
        note_str = " ".join(notes) if notes else "All signals look solid."

        return json.dumps({
            "type": "metrics",
            "source": "fitbit",
            "connected": True,
            "sleepHours": data.get("sleepHours"),
            "hrv": data.get("hrv"),
            "rhr": data.get("rhr"),
            "sleepPerf": data.get("sleepPerf"),
            "bedtime": data.get("bedtime"),
            "wakeTime": data.get("wakeTime"),
            "readinessScore": score,
            "readinessLabel": readiness,
            "interpretation": note_str,
            "sleepDebt7d": data.get("sleepDebt7d", []),
            "hud_render": True
        })

    # ── SKILL 3: Training Status ──────────────────────────────────────────────
    # Chunk: Gym log read + progressive overload check
    # Trigger: "training", "gym", "workouts", "how's my progress"
    @ctx.ai_callable(
        description=(
            "Report on gym training status and progressive overload progress. "
            "Call when the user asks about training, gym, workouts, or fitness progress."
        )
    )
    async def get_training_status() -> str:
        # localStorage connection not yet available server-side.
        # Skill feedback note: once gym data syncs to Supabase, read it here.
        return json.dumps({
            "type": "metrics",
            "source": "gym",
            "connected": False,
            "spoken": "Gym data lives in your browser for now. Open the Gym page to log a session — once you do, I'll be able to track your progressive overload.",
            "fix": "Visit /gym.html and log a session"
        })

    # ── SKILL 4: Finance Summary ──────────────────────────────────────────────
    # Chunk: Net worth + account balances + recent delta
    # Trigger: "finances", "net worth", "money", "accounts"
    @ctx.ai_callable(
        description=(
            "Summarise current financial position — net worth, account balances, and recent changes. "
            "Call when the user asks about money, finances, net worth, savings, or accounts."
        )
    )
    async def get_finance_summary() -> str:
        return json.dumps({
            "type": "metrics",
            "source": "finance",
            "connected": False,
            "spoken": "Finance data syncs from your browser. Open the Finance page, add your accounts, and I'll track net worth automatically.",
            "fix": "Visit /finance.html and enter your account balances"
        })

    # ── SKILL 5: Goals Check ──────────────────────────────────────────────────
    # Chunk: Active goals + streak status + what's due today
    # Trigger: "goals", "streaks", "how am I doing", "habits"
    @ctx.ai_callable(
        description=(
            "Check active goals and streak status — what's on track, what's at risk, what's due today. "
            "Call when the user asks about goals, streaks, habits, or how they're doing."
        )
    )
    async def get_goals_status() -> str:
        return json.dumps({
            "type": "goals",
            "connected": False,
            "spoken": "Your goals are tracked on the main dashboard. Head there to see your streaks — I can read them once they're synced to the cloud.",
            "fix": "Visit /index.html and check your goals board"
        })

    # ── SKILL 6: Plan My Day ──────────────────────────────────────────────────
    # Chunk: Readiness → training decision → finance check → goals check → priority stack
    # This is the "function tree" skill — it calls health data and assembles a priority stack.
    # Trigger: "plan my day", "what should I do today", "today's priorities"
    @ctx.ai_callable(
        description=(
            "Build today's priority stack — pull health data to assess readiness, then stack the "
            "top 3-4 actions based on what's most leveraged right now. "
            "Call when the user asks to plan their day, what to focus on, or today's priorities."
        )
    )
    async def plan_my_day() -> str:
        health = await fetch_hitbit_data_safe()

        priorities = []

        # Priority logic — readiness-gated training decision (chunk 1)
        if health.get("connected") and health.get("sleepHours"):
            hrs = float(health["sleepHours"])
            if hrs < 6.5:
                priorities.append("Recovery day — skip heavy training. Walk, stretch, sleep early tonight.")
            elif hrs >= 7.5:
                priorities.append("Sleep was solid. Push hard in the gym today.")
            else:
                priorities.append("Moderate readiness — keep training but don't max out.")
        else:
            priorities.append("Connect Fitbit on the Band page so I can give you a proper readiness call.")

        # Static priority chunks — will become live once Supabase sync is confirmed
        priorities.append("Check your goals board — tick off anything you completed yesterday.")
        priorities.append("Log your training session in the Gym tracker after you're done.")
        priorities.append("If you had any transactions today, update the Finance page.")

        return json.dumps({
            "type": "actions",
            "date": today_label(),
            "priorities": priorities,
            "health_used": health.get("connected", False),
            "hud_render": True
        })

    # ── SKILL 7: System Audit (Four C's) ─────────────────────────────────────
    # Chunk: Score Context / Connections / Capabilities / Cadence → surface gaps
    # Trigger: "audit", "how's the system", "what's missing", "four c's"
    @ctx.ai_callable(
        description=(
            "Run a Four C's audit — score Context, Connections, Capabilities, and Cadence, "
            "then surface the top gaps and what to fix next. "
            "Call when the user asks for an audit, system check, or what JARVIS is missing."
        )
    )
    async def run_system_audit() -> str:
        # Check live connection health
        fitbit = await fetch_fitbit_data()
        fitbit_connected = fitbit.get("connected", False)

        scores = {
            "context": 70,    # System prompt has identity, personality, data map
            "connections": 25 if fitbit_connected else 10,  # Only Fitbit wired so far
            "capabilities": 60,  # 7 skills, but gym/finance/goals not live yet
            "cadence": 10,    # No scheduled routines yet
        }
        total = sum(scores.values()) // 4

        gaps = []
        if not fitbit_connected:
            gaps.append("CRITICAL — Fitbit not connected. No live health data.")
        if scores["connections"] < 50:
            gaps.append("Connections gap — gym, finance, and goals data not yet synced to cloud.")
        if scores["cadence"] < 30:
            gaps.append("No cadence — morning brief isn't scheduled. Set up a daily trigger.")

        next_steps = [
            "Connect Fitbit on the Band page if not done.",
            "Enable Supabase sync so gym, finance and goals data reaches JARVIS.",
            "Set a daily 7am brief routine once the agent runs 24/7.",
        ]

        return json.dumps({
            "type": "audit",
            "date": today_label(),
            "total_score": total,
            "scores": scores,
            "grade": "C+" if total < 50 else "B" if total < 70 else "A",
            "gaps": gaps,
            "next_steps": next_steps,
            "hud_render": True
        })

    return ctx


async def fetch_hitbit_data_safe() -> dict:
    """Wrapper that never raises — used inside skills that need health data."""
    try:
        return await fetch_fitbit_data()
    except Exception:
        return {"connected": False}


# ── ENTRYPOINT ───────────────────────────────────────────────────────────────

async def entrypoint(ctx: JobContext):
    """
    Session lifecycle:
    1. Connect to LiveKit room (audio only — no video bandwidth wasted)
    2. Load context (dashboard snapshot — progressive, not greedy)
    3. Start VoiceAssistant with all 7 skills wired
    4. Open with a single line — JARVIS doesn't ramble
    """
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

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
        fnc_ctx=build_skill_functions(),
        chat_ctx=llm.ChatContext().append(
            role="system",
            text=SYSTEM_PROMPT,
        ),
    )

    assistant.start(ctx.room)
    await asyncio.sleep(1)

    # One line. JARVIS doesn't introduce himself.
    await assistant.say("Online. What do you need?", allow_interruptions=True)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
