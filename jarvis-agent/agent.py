"""
JARVIS Voice Agent — LiveKit Agents 1.x + Deepgram STT + Gemini LLM + ElevenLabs TTS

Architecture principles (from AIOS seminar):
  FOUR C's: Context → Connections → Capabilities → Cadence
  SEVEN TIER-1 DOMAINS: Health, Fitness, Finance, Goals, Calendar, Comms, Knowledge
  BORING IS BEAUTIFUL: deterministic heuristics beat agentic AI for repeatable decisions
  MENTOR NOT VENDING MACHINE: push back with a leverage question, don't just answer
  PROOF OF CONCEPT FIRST: stubs until proven used, then upgrade in place

Run with: python agent.py start
"""

import asyncio
import json
import os
import urllib.request
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from livekit.agents import Agent, AgentSession, AutoSubscribe, JobContext, WorkerOptions, cli, llm
from livekit.plugins import deepgram, elevenlabs, google, silero

load_dotenv()

ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "lXyAd1XzWURWg0DjnhJj")
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "https://dashboard-one-mauve-25.vercel.app")

SESSION_LOG = Path(__file__).parent / "session_log.md"

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
  CAPABILITIES: 8 skill-functions — brief, health, fitness, finance, goals, plan, audit, level up
  CADENCE:     You run on-demand; skills improve each session via the feedback loop

SEVEN TIER-1 DOMAINS:
  1. Health     → Fitbit (CONNECTED — partial)
  2. Fitness    → Gym tracker (browser-only, not yet cloud-synced)
  3. Finance    → Finance page (browser-only, not yet cloud-synced)
  4. Goals      → Dashboard goals board (browser-only)
  5. Calendar   → NOT CONNECTED (future: Google Calendar)
  6. Comms      → NOT CONNECTED (future: email/messages)
  7. Knowledge  → NOT CONNECTED (future: notes, documents)

MENTOR MINDSET:
  - After every plan or brief, ask ONE leverage question:
    "Where's the biggest time-sink in that — what could AI handle instead?"
  - Push back on vague requests: "What does success look like today, specifically?"
  - Don't just answer — make him sharper.

BORING IS BEAUTIFUL:
  Readiness score uses deterministic maths, not LLM inference.
  Priority gating (sleep hours → training decision) is a simple if/else.
  Only use your LLM brain for interpretation and nuance, not arithmetic.
"""


# ── CONNECTIONS ──────────────────────────────────────────────────────────────

async def fetch_fitbit_data() -> dict:
    try:
        url = f"{DASHBOARD_URL}/api/fitbit/data"
        req = urllib.request.Request(url, headers={"User-Agent": "JARVIS-Agent/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"connected": False, "error": str(e)}


def format_health_summary(d: dict) -> str:
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


# ── SESSION LOG ───────────────────────────────────────────────────────────────

def read_session_log() -> str:
    if not SESSION_LOG.exists():
        return "(no prior sessions logged)"
    lines = SESSION_LOG.read_text().strip().split("\n")
    recent = lines[-30:] if len(lines) > 30 else lines
    return "\n".join(recent)


def append_session_log(entry: dict) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"\n## Session {ts}",
        f"- Fitbit connected: {entry.get('fitbit_connected', 'unknown')}",
        f"- Skills called: {', '.join(entry.get('skills_called', [])) or 'none'}",
        f"- Errors: {entry.get('errors', 'none')}",
        f"- Notes: {entry.get('notes', '')}",
    ]
    with open(SESSION_LOG, "a") as f:
        f.write("\n".join(lines) + "\n")


# ── SKILL TOOLS (livekit-agents 1.x uses @llm.function_tool) ────────────────

@llm.function_tool
async def get_daily_brief() -> str:
    """Run the daily brief — pull the strongest signal from health, finance, and goals,
    then surface one priority for today. Call this when asked for a brief, summary,
    situation report, or what to focus on."""
    health = await fetch_fitbit_data()
    health_line = format_health_summary(health)
    date = today_label()

    signals = [health_line]
    signals.append("Finance: open the Finance page if you haven't logged accounts yet.")
    signals.append("Goals: your streaks are live on the main dashboard.")

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
        "signals": signals,
        "priority": priority,
    })


@llm.function_tool
async def get_health_metrics() -> str:
    """Fetch live Fitbit health metrics — sleep hours, HRV, and resting heart rate —
    and interpret them as a readiness signal. Call when asked about sleep, recovery,
    HRV, heart rate, or health."""
    data = await fetch_fitbit_data()

    if not data.get("connected"):
        return json.dumps({
            "type": "metrics",
            "connected": False,
            "spoken": "Fitbit isn't linked. Go to the Band page and hit Connect.",
        })

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
        "connected": True,
        "sleepHours": data.get("sleepHours"),
        "hrv": data.get("hrv"),
        "rhr": data.get("rhr"),
        "readinessScore": score,
        "readinessLabel": readiness,
        "interpretation": note_str,
    })


@llm.function_tool
async def get_training_status() -> str:
    """Report on gym training status. Call when asked about training, gym, workouts,
    or fitness progress."""
    return json.dumps({
        "type": "metrics",
        "source": "gym",
        "connected": False,
        "spoken": "Gym data lives in your browser for now. Open the Gym page to log a session.",
    })


@llm.function_tool
async def get_finance_summary() -> str:
    """Summarise current financial position. Call when asked about money, finances,
    net worth, savings, or accounts."""
    return json.dumps({
        "type": "metrics",
        "source": "finance",
        "connected": False,
        "spoken": "Finance data syncs from your browser. Open the Finance page and add your accounts.",
    })


@llm.function_tool
async def get_goals_status() -> str:
    """Check active goals and streak status. Call when asked about goals, streaks,
    habits, or how they're doing."""
    return json.dumps({
        "type": "goals",
        "connected": False,
        "spoken": "Your goals are tracked on the main dashboard. Head there to see your streaks.",
    })


@llm.function_tool
async def plan_my_day() -> str:
    """Build today's priority stack based on health readiness. Call when asked to
    plan the day, what to focus on, or today's priorities."""
    health = await fetch_fitbit_data()
    priorities = []

    if health.get("connected") and health.get("sleepHours"):
        hrs = float(health["sleepHours"])
        if hrs < 6.5:
            priorities.append("Recovery day — skip heavy training. Walk, stretch, sleep early.")
        elif hrs >= 7.5:
            priorities.append("Sleep was solid. Push hard in the gym today.")
        else:
            priorities.append("Moderate readiness — keep training but don't max out.")
    else:
        priorities.append("Connect Fitbit on the Band page so I can give you a readiness call.")

    priorities.append("Check your goals board — tick off anything completed yesterday.")
    priorities.append("Log your training session in the Gym tracker after you're done.")
    priorities.append("If you had transactions today, update the Finance page.")

    leverage_q = (
        "One question: what's the most manual, repetitive thing on that list? "
        "That's probably the smart intern task — want me to think through automating it?"
    )

    return json.dumps({
        "type": "actions",
        "date": today_label(),
        "priorities": priorities,
        "leverage_question": leverage_q,
    })


@llm.function_tool
async def run_system_audit() -> str:
    """Run a full JARVIS audit — score the Four C's and all seven life domains,
    surface ranked gaps and the highest-leverage next step. Call when asked for
    an audit, system check, four C's score, or what's missing."""
    fitbit = await fetch_fitbit_data()
    fitbit_connected = fitbit.get("connected", False)

    four_cs = {
        "context": 18,
        "connections": 20 if fitbit_connected else 8,
        "capabilities": 16,
        "cadence": 3,
    }

    domains = {
        "health":    8 if fitbit_connected else 2,
        "fitness":   2,
        "finance":   2,
        "goals":     2,
        "calendar":  0,
        "comms":     0,
        "knowledge": 0,
    }

    gaps = []
    if not fitbit_connected:
        gaps.append({"domain": "Health", "fix": "Go to /fitband.html and click Connect Fitbit."})
    gaps.append({"domain": "Fitness", "fix": "Enable Supabase sync in gym.html."})
    gaps.append({"domain": "Finance", "fix": "Enable Supabase sync in finance.html."})
    gaps.append({"domain": "Cadence", "fix": "Set up a daily 7am brief once the agent is stable."})

    overall = int(sum(four_cs.values()) * 0.6 + sum(domains.values()) * 0.4)
    grade = "D" if overall < 30 else "C" if overall < 50 else "B" if overall < 70 else "A"

    return json.dumps({
        "type": "audit",
        "four_cs": four_cs,
        "domains": domains,
        "overall_score": overall,
        "grade": grade,
        "top_gap": gaps[0]["fix"] if gaps else "System looks well connected.",
    })


@llm.function_tool
async def level_up() -> str:
    """Run the Level Up interview — ask five questions to find the next thing worth
    automating. Call after an audit or when asked what to automate next."""
    return json.dumps({
        "type": "level_up",
        "questions": [
            "Walk me through this past week — what did you do three or more times?",
            "What felt manual, boring, or copy-paste?",
            "Smart intern test: anything you did yourself because explaining it would take longer?",
            "If you had to do twice as much next week, what would break first?",
            "What's the one thing that, if it ran on autopilot, would give you the most back?",
        ],
    })


# ── ENTRYPOINT ────────────────────────────────────────────────────────────────

async def entrypoint(ctx: JobContext):
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    prior_log = read_session_log()
    fitbit_status = await fetch_fitbit_data()
    fitbit_ok = fitbit_status.get("connected", False)

    connection_note = (
        "Fitbit is connected and returning data."
        if fitbit_ok
        else "Fitbit is NOT connected this session."
    )

    augmented_prompt = SYSTEM_PROMPT + f"""

PRIOR SESSION LOG:
{prior_log}

CONNECTION STATUS THIS SESSION:
{connection_note}
"""

    agent = Agent(
        instructions=augmented_prompt,
        tools=[
            get_daily_brief,
            get_health_metrics,
            get_training_status,
            get_finance_summary,
            get_goals_status,
            plan_my_day,
            run_system_audit,
            level_up,
        ],
    )

    session = AgentSession(
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
    )

    await session.start(agent=agent, room=ctx.room)
    await session.generate_reply(instructions="Say exactly: Online. What do you need?")

    append_session_log({
        "fitbit_connected": fitbit_ok,
        "skills_called": [],
        "errors": "none",
        "notes": f"Fitbit {'live' if fitbit_ok else 'offline'}.",
    })


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
