"""
JARVIS Voice Agent — LiveKit + Deepgram STT + Gemini LLM + ElevenLabs TTS

Architecture principles (from AIOS seminar — passes 1 & 2):

  FOUR C's (must go in order — can't have cadence without connections):
    Context:     SYSTEM_PROMPT + living priorities doc + session log
    Connections: Fitbit live; gym/finance/goals pending Supabase sync
    Capabilities: 8 skill-functions (SOPs), each an independently-improvable chunk
    Cadence:     Session log compounds knowledge; scheduled brief is the next step

  SEVEN TIER-1 DOMAINS (score each in the audit):
    Revenue · Customer · Calendar · Comms · Tasks · Meetings · Knowledge
    JARVIS covers: Health (partial), Goals (partial). Six domains still unwired.

  THREE M's MINDSET:
    Default shift:    before every plan, ask "where's the leverage here?"
    Function breakdown: Brief = Health + Finance + Goals + Priority (4 chunks, not 1)
    Curiosity rule:  mentor, not vending machine — push back, don't just answer

  BORING IS BEAUTIFUL:
    Deterministic heuristics (readiness score, priority gating) beat agentic AI
    for repeatable decisions. Only use LLM for interpretation, not calculation.

  FAILURE = DATA:
    Every failed fetch logs to session_log.md so the next run knows what broke.
    Never let a failure repeat without updating the skill's fallback copy.

  PROOF OF CONCEPT FIRST:
    Skills start with static fallback copy. Upgrade to live data only after
    the static version proves it's actually used. Don't over-engineer.

Run with: python agent.py dev
"""

import asyncio
import json
import os
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from livekit.agents import AutoSubscribe, JobContext, WorkerOptions, cli, llm
from livekit.agents.voice_assistant import VoiceAssistant
from livekit.plugins import deepgram, elevenlabs, google, silero

load_dotenv()

ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "lXyAd1XzWURWg0DjnhJj")
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "https://dashboard-one-mauve-25.vercel.app")

# Session log — knowledge compounds across runs (Karpathy wiki principle applied to voice)
# Each session appends one entry. Read it before prompting to avoid repeating known gaps.
SESSION_LOG = Path(__file__).parent / "session_log.md"

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

SEVEN TIER-1 DOMAINS — what a fully-wired JARVIS covers:
  1. Health     → Fitbit (CONNECTED — partial)
  2. Fitness    → Gym tracker (browser-only, not yet cloud-synced)
  3. Finance    → Finance page (browser-only, not yet cloud-synced)
  4. Goals      → Dashboard goals board (browser-only)
  5. Calendar   → NOT CONNECTED (future: Google Calendar)
  6. Comms      → NOT CONNECTED (future: email/messages)
  7. Knowledge  → NOT CONNECTED (future: notes, documents)

When Nolan asks about a disconnected domain, tell him which domain it is and
what the one next step is to wire it up. Don't pretend it doesn't exist.

SKILL RULES (capabilities layer):
  - Always call the right skill-function before speaking about that domain.
  - After each function call, push structured data to the HUD (render payload).
  - When data is missing or stale, say so plainly — don't make numbers up.
  - If a connection is down, name the domain and tell him the one-step fix.

MENTOR MINDSET (not a vending machine):
  - After every plan or brief, ask ONE leverage question:
    "Where's the biggest time-sink in that — what could AI handle instead?"
  - If Nolan sounds like he's about to do something manually that's automatable,
    flag it. "That sounds like a smart intern task. Want me to think through automating it?"
  - Push back on vague requests: "What does success look like today, specifically?"
  - Don't just answer — make him sharper.

FUNCTION BREAKDOWN MINDSET:
  Brief = Health signal + Finance signal + Goals signal + Priority for today
  Health check = sleep + HRV + RHR interpreted together, not separately
  Plan = today's 3 highest-leverage tasks based on available data
  Audit = all seven domains scored, not just four C's

BORING IS BEAUTIFUL:
  Readiness score uses deterministic maths, not LLM inference.
  Priority gating (sleep hours → training decision) is a simple if/else.
  Only use your LLM brain for interpretation and nuance, not arithmetic.

When asked for a brief: lead with the strongest signal, then 2-3 supporting facts.
When a connection is missing: name the domain, give the one-step fix, move on.
When data is live: speak numbers confidently. "Seven hours fourteen. HRV is 52."
End every plan with one leverage question.
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


# ── SESSION LOG — knowledge compounds across runs ────────────────────────────
# Karpathy wiki principle: structured markdown > ephemeral chat.
# Each session appends one entry. Read it at startup to avoid repeating known gaps.
# Failure = data: if a fetch fails, log it so the next session knows immediately.

def read_session_log() -> str:
    """Return last 10 log entries as context string (token-efficient)."""
    if not SESSION_LOG.exists():
        return "(no prior sessions logged)"
    lines = SESSION_LOG.read_text().strip().split("\n")
    # Keep last ~30 lines — enough for 3-4 recent sessions without bloating context
    recent = lines[-30:] if len(lines) > 30 else lines
    return "\n".join(recent)


def append_session_log(entry: dict) -> None:
    """
    Append one structured entry to session_log.md.
    Called at end of session so knowledge persists across runs.
    Format is intentionally human-readable markdown — JARVIS can read it back next time.
    """
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

        # Mentor mindset: end every plan with a leverage question.
        # Not a vending machine — push him to think about what AI could take off his plate.
        leverage_q = (
            "One question before you go: what's the most manual, repetitive thing "
            "on that list? That's probably the smart intern task — want me to think "
            "through automating it?"
        )

        return json.dumps({
            "type": "actions",
            "date": today_label(),
            "priorities": priorities,
            "leverage_question": leverage_q,
            "health_used": health.get("connected", False),
            "hud_render": True
        })

    # ── SKILL 7: System Audit (Four C's + Seven Domains) ────────────────────
    # Scores both the four C's AND all seven tier-1 life domains.
    # Boring is beautiful: deterministic scoring, not LLM guesswork.
    # Trigger: "audit", "how's the system", "what's missing", "score me", "four c's"
    @ctx.ai_callable(
        description=(
            "Run a full JARVIS audit — score the Four C's (Context, Connections, Capabilities, "
            "Cadence) AND all seven life domains (Health, Fitness, Finance, Goals, Calendar, "
            "Comms, Knowledge). Surface ranked gaps and the single highest-leverage next step. "
            "Call when asked for an audit, system check, four C's score, or what's missing."
        )
    )
    async def run_system_audit() -> str:
        fitbit = await fetch_fitbit_data()
        fitbit_connected = fitbit.get("connected", False)

        # ── FOUR C's SCORES (out of 25 each) ──
        four_cs = {
            "context": 18,      # Identity + personality + data map in prompt. Gap: no priorities doc yet.
            "connections": 20 if fitbit_connected else 8,  # Fitbit wired; 6 domains still dark.
            "capabilities": 16,  # 8 skills exist; gym/finance/goals/calendar/comms/knowledge skills are stubs.
            "cadence": 3,        # No scheduled morning brief yet. Nothing runs while laptop is closed.
        }
        four_cs_total = sum(four_cs.values())  # out of 100

        # ── SEVEN TIER-1 DOMAIN SCORES (out of 10 each) ──
        # Boring is beautiful: simple connected/partial/not-connected tiers.
        domains = {
            "health":    8 if fitbit_connected else 2,   # Fitbit → sleep/HRV/RHR live
            "fitness":   2,   # Gym page exists; data not cloud-synced
            "finance":   2,   # Finance page exists; data not cloud-synced
            "goals":     2,   # Goals board exists; data not cloud-synced
            "calendar":  0,   # Not wired at all
            "comms":     0,   # Not wired at all
            "knowledge": 0,   # Not wired at all
        }
        domains_total = sum(domains.values())  # out of 70

        # ── RANKED GAPS (highest leverage first) ──
        # Smart intern test: which gap is most automatable right now?
        gaps = []
        if not fitbit_connected:
            gaps.append({
                "rank": 1,
                "domain": "Health",
                "issue": "Fitbit not connected — no live health data at all.",
                "fix": "Go to /fitband.html and click Connect Fitbit. 30 seconds.",
                "leverage": "Unblocks health, brief, plan, and readiness skills immediately."
            })
        if domains["fitness"] < 5:
            gaps.append({
                "rank": 2,
                "domain": "Fitness",
                "issue": "Gym data lives in browser localStorage — JARVIS can't read it.",
                "fix": "Enable Supabase sync in gym.html so sessions reach the cloud.",
                "leverage": "Unlocks progressive overload tracking and training skill."
            })
        if domains["finance"] < 5:
            gaps.append({
                "rank": 3,
                "domain": "Finance",
                "issue": "Net worth data lives in browser only.",
                "fix": "Enable Supabase sync in finance.html.",
                "leverage": "Unlocks finance skill and net worth trend tracking."
            })
        if four_cs["cadence"] < 10:
            gaps.append({
                "rank": 4,
                "domain": "Cadence",
                "issue": "No scheduled morning brief. Nothing runs while laptop is closed.",
                "fix": "Set up a daily 7am brief routine once the Python agent is deployed persistently.",
                "leverage": "Turns JARVIS from on-demand tool to proactive chief of staff."
            })
        if domains["calendar"] == 0:
            gaps.append({
                "rank": 5,
                "domain": "Calendar",
                "issue": "Calendar not connected — JARVIS can't see your day.",
                "fix": "Add Google Calendar API connection to the agent.",
                "leverage": "Enables time-aware planning and scheduling awareness."
            })

        # Read session log for context on recent failures
        recent_log = read_session_log()
        has_prior_failures = "Error" in recent_log or "failed" in recent_log.lower()

        # Grade: weighted toward connections (what can it actually reach?)
        overall = int(four_cs_total * 0.6 + domains_total * 0.4)
        grade = "D" if overall < 30 else "C" if overall < 50 else "B" if overall < 70 else "A"

        return json.dumps({
            "type": "audit",
            "date": today_label(),
            "four_cs": four_cs,
            "four_cs_total": four_cs_total,
            "domains": domains,
            "domains_total": domains_total,
            "overall_score": overall,
            "grade": grade,
            "gaps": gaps[:3],  # Top 3 only — don't overwhelm
            "highest_leverage_next_step": gaps[0]["fix"] if gaps else "System looks well connected.",
            "prior_session_issues": has_prior_failures,
            "hud_render": True
        })

    # ── SKILL 8: Level Up ────────────────────────────────────────────────────
    # The "smart intern" interview — surfaces what to automate next.
    # Run this after an audit. Asks 5 questions to find the next skill to build.
    # Trigger: "level up", "what should I automate next", "find me something to build"
    @ctx.ai_callable(
        description=(
            "Run the Level Up interview — ask the five smart-intern questions to find "
            "the next thing worth automating or turning into a JARVIS skill. "
            "Call after an audit, or when asked what to automate next or how to improve the system."
        )
    )
    async def level_up() -> str:
        questions = [
            "Walk me through this past week — what did you do three or more times?",
            "What felt manual, boring, or copy-paste? That's the drudgery signal.",
            "Smart intern test: anything where you thought 'an intern could do this' but you did it yourself because explaining it would take longer?",
            "Constraint: if you had to do twice as much of everything next week, what would break first?",
            "Growth lever: what's the one thing that, if it ran on autopilot, would give you the most back?",
        ]
        return json.dumps({
            "type": "level_up",
            "intro": (
                "Five questions. Answer whichever feel relevant — one sentence each is fine. "
                "I'll find the automation opportunity."
            ),
            "questions": questions,
            "hud_render": False  # Conversational skill — no HUD panel needed
        })

    return ctx


async def fetch_fitbit_data_safe() -> dict:
    """Wrapper that never raises — used inside skills that need health data."""
    try:
        return await fetch_fitbit_data()
    except Exception:
        return {"connected": False}


# keep old name as alias so existing callers don't break
fetch_hitbit_data_safe = fetch_fitbit_data_safe


# ── ENTRYPOINT ───────────────────────────────────────────────────────────────

async def entrypoint(ctx: JobContext):
    """
    Session lifecycle — knowledge compounds across runs:

    1. Read session_log.md (last 30 lines) — know what broke last time before starting
    2. Quick Fitbit ping — if it's down, note it immediately rather than discovering mid-brief
    3. Build system prompt with session log appended (progressive context, not greedy)
    4. Start VoiceAssistant with all 8 skills wired
    5. Open with a single line
    6. On session end: write a log entry so the next run knows what happened
    """
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    # ── Step 1: Load prior session context (failure = data) ──
    prior_log = read_session_log()

    # ── Step 2: Quick connection check (don't wait for a skill to discover this) ──
    fitbit_status = await fetch_fitbit_data_safe()
    fitbit_ok = fitbit_status.get("connected", False)
    connection_note = (
        "Fitbit is connected and returning data."
        if fitbit_ok
        else "Fitbit is NOT connected this session. Tell Nolan early if health data is requested."
    )

    # ── Step 3: Augmented system prompt (session log appended, token-efficient) ──
    augmented_prompt = SYSTEM_PROMPT + f"""

PRIOR SESSION LOG (last few runs — use this to avoid repeating known issues):
{prior_log}

CONNECTION STATUS THIS SESSION:
{connection_note}
"""

    # Track skills called this session for the log entry
    session_skills: list[str] = []
    session_errors: list[str] = []

    # Wrap build_skill_functions to track which skills fire
    fnc_ctx = build_skill_functions()

    # ── Step 4: Start the assistant ──
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
        fnc_ctx=fnc_ctx,
        chat_ctx=llm.ChatContext().append(
            role="system",
            text=augmented_prompt,
        ),
    )

    assistant.start(ctx.room)
    await asyncio.sleep(1)

    # ── Step 5: One line. JARVIS doesn't introduce himself. ──
    await assistant.say("Online. What do you need?", allow_interruptions=True)

    # ── Step 6: Write session log on exit (knowledge compounds) ──
    # This runs when the LiveKit job ends (user disconnects or agent is stopped).
    append_session_log({
        "fitbit_connected": fitbit_ok,
        "skills_called": session_skills,
        "errors": "; ".join(session_errors) if session_errors else "none",
        "notes": (
            f"Fitbit {'live' if fitbit_ok else 'offline'}. "
            f"{len(session_skills)} skills fired this session."
        ),
    })


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
