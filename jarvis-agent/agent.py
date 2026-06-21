"""
JARVIS Voice Agent — livekit-agents 1.x
Pattern: AgentServer + @server.rtc_session() + Agent subclass + @function_tool methods

Fixes applied (audit 2026-06-21):
  - on_enter() now awaits generate_reply (Issue 1 — critical)
  - on_shutdown is async (Issue 2 — critical)
  - silero.VAD.load() called once in prewarm, cached in proc.userdata (Issue 3)
  - google.LLM model corrected to gemini-2.0-flash-001 (Issue 4)
  - ElevenLabs key reads both env var names (Issue 5)
  - Startup env var validation for all required keys (Issues 6, 7)
  - fetch_fitbit() uses httpx for non-blocking async HTTP (Issue 8)
  - nixpacks, requirements, railway fixes applied in their files (Issues 22–26)

Skills: 8 Life OS + 4 OSINT = 12 total
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv
from livekit.agents import Agent, AgentServer, AgentSession, JobContext, RunContext, WorkerOptions, cli
from livekit.agents.llm import function_tool
from livekit.plugins import deepgram, elevenlabs, google, silero

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("jarvis")

# ── STARTUP VALIDATION (Issues 6, 7) ─────────────────────────────────────────
# Fail loudly at startup rather than silently during a live session.
_REQUIRED = ["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET",
             "DEEPGRAM_API_KEY", "GEMINI_API_KEY"]
for _var in _REQUIRED:
    if not os.getenv(_var):
        raise RuntimeError(f"Required env var {_var} is not set — check Railway Variables tab")

_ELEVEN_KEY = os.getenv("ELEVENLABS_API_KEY") or os.getenv("ELEVEN_API_KEY")
if not _ELEVEN_KEY:
    raise RuntimeError("ElevenLabs API key not set — set ELEVENLABS_API_KEY in Railway Variables")

ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "lXyAd1XzWURWg0DjnhJj")
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "https://dashboard-one-mauve-25.vercel.app")
SESSION_LOG = Path(__file__).parent / "session_log.md"

INSTRUCTIONS = """You are JARVIS — Nolan Abbott's personal AI chief of staff.

PERSONALITY:
- Calm, confident, dry wit. Never sycophantic.
- Lead with the answer. Under 4 sentences unless asked for more.
- Chief of staff energy: brief, flag, prioritise. No chat.

BANNED WORDS: "Understood", "Absolutely", "Certainly", "Of course", "Great question",
"I'd be happy to", "Sure thing", "Sounds good", "Happy to help"

SKILLS — call the right function before speaking about that domain:
  Life OS: get_daily_brief, get_health_metrics, get_training_status, get_finance_summary,
           get_goals_status, plan_my_day, run_system_audit, level_up
  OSINT:   generate_google_dorks, digital_footprint_analysis, metadata_briefing,
           osint_awareness_brief

MENTOR MINDSET:
  After every plan, ask one leverage question:
  "Where's the biggest time-sink — what could AI handle instead?"
  Push back on vague requests: "What does success look like today, specifically?"

SEVEN DOMAINS:
  Health → Fitbit (partial). Fitness/Finance/Goals → browser-only stubs.
  Calendar/Comms/Knowledge → not connected yet.
  When a domain is dark, name it and give the one-step fix.

BORING IS BEAUTIFUL:
  Readiness score = deterministic maths. Priority gating = simple if/else.
  Use LLM brain for interpretation, not arithmetic.

Do not use emojis, asterisks, or markdown in spoken responses.
"""


# ── CONNECTIONS (async HTTP — non-blocking) ───────────────────────────────────
# Issue 8 fix: use httpx instead of urllib to avoid blocking the asyncio event loop.

async def fetch_fitbit() -> dict:
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(
                f"{DASHBOARD_URL}/api/fitbit/data",
                headers={"User-Agent": "JARVIS/1.0"},
            )
            return r.json()
    except Exception as e:
        return {"connected": False, "error": str(e)}


def health_summary(d: dict) -> str:
    if not d.get("connected"):
        return "Fitbit isn't connected. Go to the Band page and hit Connect."
    parts = []
    if d.get("sleepHours"): parts.append(f"{d['sleepHours']} hours sleep")
    if d.get("hrv"):        parts.append(f"HRV {d['hrv']} ms")
    if d.get("rhr"):        parts.append(f"resting HR {d['rhr']}")
    return ", ".join(parts) or "Health data empty — try reconnecting Fitbit."


def today() -> str:
    return datetime.now().strftime("%A %d %B")


# ── SESSION LOG ───────────────────────────────────────────────────────────────

def read_log() -> str:
    if not SESSION_LOG.exists():
        return "(no prior sessions)"
    lines = SESSION_LOG.read_text().strip().split("\n")
    return "\n".join(lines[-30:])


def write_log(fitbit_ok: bool, notes: str = "") -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(SESSION_LOG, "a") as f:
        f.write(f"\n## {ts}\n- Fitbit: {'connected' if fitbit_ok else 'offline'}\n- {notes}\n")


# ── JARVIS AGENT ──────────────────────────────────────────────────────────────

class JarvisAgent(Agent):
    def __init__(self, augmented_instructions: str) -> None:
        super().__init__(instructions=augmented_instructions)

    async def on_enter(self) -> None:
        """Fires when JARVIS joins a session. Await is required — returns a coroutine."""
        # Issue 1 fix: must await generate_reply
        await self.session.generate_reply(
            instructions="Say exactly this and nothing else: Online. What do you need?"
        )

    # ── LIFE OS SKILLS ────────────────────────────────────────────────────────

    @function_tool
    async def get_daily_brief(self, context: RunContext) -> str:
        """Run the daily brief — strongest signal from health, finance, goals, then one priority.
        Call when asked for a brief, summary, situation report, or what to focus on."""
        h = await fetch_fitbit()
        signals = [
            health_summary(h),
            "Finance: log accounts on the Finance page — net worth tracks automatically.",
            "Goals: streaks are live on the main dashboard.",
        ]
        priority = "Log your training session if you're feeling fresh."
        if h.get("connected") and h.get("sleepHours"):
            hrs = float(h["sleepHours"])
            if hrs < 6.5:    priority = "Sleep was short. Recovery day — lighter training or full rest."
            elif hrs >= 7.5: priority = "Good sleep. High-effort training or deep work is on the table."
        return json.dumps({"type": "brief", "date": today(), "signals": signals, "priority": priority})

    @function_tool
    async def get_health_metrics(self, context: RunContext) -> str:
        """Fetch live Fitbit metrics — sleep, HRV, resting HR — and interpret as a readiness signal.
        Call when asked about sleep, recovery, HRV, or heart rate."""
        d = await fetch_fitbit()
        if not d.get("connected"):
            return json.dumps({"connected": False, "spoken": "Fitbit isn't linked. Band page → Connect."})
        score = 50
        notes = []
        hrs = float(d.get("sleepHours") or 0)
        hrv = int(d.get("hrv") or 0)
        rhr = int(d.get("rhr") or 0)
        if hrs >= 8:         score += 20
        elif hrs >= 7:       score += 15
        elif hrs >= 6:       score += 5
        elif hrs:            score -= 10; notes.append("Sleep short.")
        if hrv >= 60:        score += 20
        elif hrv >= 45:      score += 10
        elif 0 < hrv < 30:  score -= 10; notes.append("HRV suppressed.")
        if 0 < rhr <= 55:   score += 10
        elif rhr >= 70:     score -= 5;  notes.append("RHR elevated.")
        score = max(0, min(100, score))
        label = "High" if score >= 70 else "Moderate" if score >= 45 else "Low"
        return json.dumps({
            "type": "metrics", "connected": True,
            "sleepHours": d.get("sleepHours"), "hrv": hrv or None, "rhr": rhr or None,
            "readinessScore": score, "readinessLabel": label,
            "notes": " ".join(notes) or "All signals solid.",
        })

    @function_tool
    async def get_training_status(self, context: RunContext) -> str:
        """Report on gym training and progressive overload. Call when asked about training or gym."""
        return json.dumps({"source": "gym", "connected": False,
                           "spoken": "Gym data is browser-only. Open the Gym page to log a session."})

    @function_tool
    async def get_finance_summary(self, context: RunContext) -> str:
        """Summarise financial position. Call when asked about money, net worth, or accounts."""
        return json.dumps({"source": "finance", "connected": False,
                           "spoken": "Finance data is browser-only. Open the Finance page and add accounts."})

    @function_tool
    async def get_goals_status(self, context: RunContext) -> str:
        """Check active goals and streaks. Call when asked about goals, streaks, or habits."""
        return json.dumps({"source": "goals", "connected": False,
                           "spoken": "Goals are on the main dashboard. Check the board for your streaks."})

    @function_tool
    async def plan_my_day(self, context: RunContext) -> str:
        """Build today's priority stack based on health readiness. Call when asked to plan the day."""
        h = await fetch_fitbit()
        priorities = []
        if h.get("connected") and h.get("sleepHours"):
            hrs = float(h["sleepHours"])
            if hrs < 6.5:    priorities.append("Recovery day — skip heavy training.")
            elif hrs >= 7.5: priorities.append("Solid sleep. Push hard today.")
            else:            priorities.append("Moderate readiness — train but don't max out.")
        else:
            priorities.append("Connect Fitbit for a readiness call.")
        priorities += [
            "Check goals board — tick off anything completed yesterday.",
            "Log your training session in the Gym tracker.",
            "Update Finance page if you had transactions today.",
        ]
        return json.dumps({
            "type": "actions", "date": today(), "priorities": priorities,
            "leverage_question": "What's the most manual thing on that list? That's the smart intern task.",
        })

    @function_tool
    async def run_system_audit(self, context: RunContext) -> str:
        """Audit the Four C's and seven life domains. Call when asked for a system check or audit."""
        h = await fetch_fitbit()
        fb = h.get("connected", False)
        four_cs = {"context": 18, "connections": 20 if fb else 8, "capabilities": 16, "cadence": 3}
        domains = {"health": 8 if fb else 2, "fitness": 2, "finance": 2, "goals": 2,
                   "calendar": 0, "comms": 0, "knowledge": 0}
        overall = int(sum(four_cs.values()) * 0.6 + sum(domains.values()) * 0.4)
        grade = "D" if overall < 30 else "C" if overall < 50 else "B" if overall < 70 else "A"
        top_gap = ("Connect Fitbit — unlocks health, brief, and readiness immediately."
                   if not fb else "Sync gym and finance data to Supabase.")
        return json.dumps({"type": "audit", "four_cs": four_cs, "domains": domains,
                           "overall": overall, "grade": grade, "top_gap": top_gap})

    @function_tool
    async def level_up(self, context: RunContext) -> str:
        """Five questions to find the next automation opportunity. Call when asked what to automate next."""
        return json.dumps({"type": "level_up", "questions": [
            "Walk me through this past week — what did you do three or more times?",
            "What felt manual, boring, or copy-paste?",
            "Smart intern test: anything you did yourself because explaining would take longer?",
            "If you had to do twice as much next week, what would break first?",
            "What's the one thing that, on autopilot, would give you the most back?",
        ]})

    # ── OSINT SKILLS ──────────────────────────────────────────────────────────

    @function_tool
    async def generate_google_dorks(self, context: RunContext, target: str, goal: str = "general recon") -> str:
        """Generate customised Google dork search queries for a target.
        Call when asked to find someone online, run recon, or create search operators.
        Args:
            target: name, domain, email, or organisation to search for
            goal: what you are trying to find"""
        dorks = [
            f'site:linkedin.com "{target}"',
            f'site:twitter.com OR site:x.com "{target}"',
            f'"{target}" filetype:pdf',
            f'"{target}" filetype:xls OR filetype:csv',
            f'intext:"{target}" site:pastebin.com',
            f'"{target}" "email" OR "phone" OR "address"',
            f'"{target}" site:github.com',
            f'cache:"{target}"' if '.' in target else f'"{target}" site:crunchbase.com',
            f'"{target}" -site:facebook.com -site:instagram.com',
            f'"{target}" inurl:profile OR inurl:about',
        ]
        return json.dumps({
            "type": "osint_dorks", "target": target, "goal": goal, "dorks": dorks,
            "tip": "Start with site: to map presence. Pastebin hit = possible data leak. Cache: = deleted content.",
        })

    @function_tool
    async def digital_footprint_analysis(self, context: RunContext, target: str, known_info: str = "") -> str:
        """Map a target's public presence across social, professional, domain, and breach sources.
        Call when asked to investigate someone, map online presence, or do a recon profile.
        Args:
            target: name, username, email, or domain
            known_info: any details already known (optional)"""
        handle = target.replace(' ', '').lower()
        return json.dumps({
            "type": "osint_footprint", "target": target,
            "social": [f"x.com/{handle}", f"instagram.com/{handle}",
                       f"reddit.com/user/{handle}", f"tiktok.com/@{handle}"],
            "professional": [f"linkedin.com/in/{target.replace(' ', '-').lower()}", f"github.com/{handle}"],
            "infrastructure": ["WHOIS → registrant name/email", "Shodan → exposed services",
                               "Wayback Machine → deleted pages"],
            "breach_check": ["haveibeenpwned.com", "dehashed.com", "intelx.io"],
            "priority": ["1. Username enum across platforms", "2. Google dorks for docs",
                         "3. WHOIS if domain known", "4. HIBP if email known",
                         "5. Wayback for deleted content", "6. Cross-ref all handles found"],
            "cross_ref_tip": "People reuse handles. jsmith92 on Reddit likely = jsmith92@gmail.com.",
        })

    @function_tool
    async def metadata_briefing(self, context: RunContext, file_type: str) -> str:
        """Explain what metadata a file type exposes and how to strip it.
        Call when asked about metadata, EXIF data, or what a file reveals.
        Args:
            file_type: file extension, e.g. jpg, pdf, docx, mp4"""
        ft = file_type.lower().strip('.')
        info = {
            "jpg":  {"fields": ["GPS coordinates", "Device make/model", "Date/time", "Camera settings", "Edit software"],
                     "tool": "exiftool image.jpg",
                     "risk": "One unstripped JPEG can expose your home address via GPS."},
            "pdf":  {"fields": ["Author", "Organisation", "Creation date", "Last modified", "Software"],
                     "tool": "exiftool doc.pdf",
                     "risk": "PDFs from Word embed real name and company even if anonymous."},
            "docx": {"fields": ["Author", "Last saved by", "Company", "Edit time", "Revision count"],
                     "tool": "Unzip .docx → read docProps/core.xml",
                     "risk": "Track changes survive even after accepting in Word."},
            "mp4":  {"fields": ["Creation date", "GPS if shot on phone", "Device model", "Encoding software"],
                     "tool": "exiftool video.mp4",
                     "risk": "iPhone/Android videos embed GPS and device serial by default."},
            "png":  {"fields": ["Creation software", "Timestamp", "ICC profile (reveals edit software)"],
                     "tool": "exiftool image.png",
                     "risk": "Mac/Windows screenshots embed hostname and timestamp."},
        }.get(ft, {"fields": ["Creation date", "Author", "Software", "Device info"],
                   "tool": f"exiftool filename.{ft}",
                   "risk": "Most files embed more than people realise."})
        return json.dumps({
            "type": "osint_metadata", "file_type": ft,
            "fields": info["fields"], "tool": info["tool"], "risk": info["risk"],
            "strip_cmd": f"exiftool -all= filename.{ft}",
        })

    @function_tool
    async def osint_awareness_brief(self, context: RunContext, target_description: str = "general user") -> str:
        """Defensive OSINT audit — what an attacker would find, and how to lock it down.
        Call when asked what someone could find about me, or for a digital privacy check.
        Args:
            target_description: describe the person or context (optional)"""
        return json.dumps({
            "type": "osint_awareness",
            "what_attackers_check": [
                "Username reuse — same handle on 10+ platforms builds a full profile",
                "Profile photos — reverse image search links separate accounts",
                "WHOIS — domain registrations expose real name and email",
                "GitHub commits — embed real name and email by default",
                "LinkedIn — maps org structure and who to impersonate",
                "Old forum posts — people overshared before they cared",
                "Breach databases — old password still works on 40% of accounts",
                "Photo EXIF — one unstripped image reveals home address",
                "Google cache and Wayback — deleted content lives for years",
            ],
            "lockdowns": [
                "Strip EXIF before posting photos (Signal auto-strips on send)",
                "Use unique usernames per platform — no searchable pattern",
                "Enable WHOIS privacy on any domain you own",
                "Set git email to GitHub no-reply: settings.github.com/emails",
                "Check haveibeenpwned.com — rotate any breached password",
                'Google yourself: "your name" filetype:pdf and site:pastebin.com',
                "LinkedIn: hide connection list and open-to-work banner",
            ],
            "spearphishing_vector": (
                "Name + employer + LinkedIn connections + email format = convincing impersonation email. "
                "Fix: verify unusual requests via a second channel, never email alone."
            ),
        })


# ── PREWARM (Issue 3) — load Silero VAD once, cache in proc.userdata ─────────

def prewarm(proc) -> None:
    """Called once per worker process before any sessions start.
    Loading the ONNX model here means every session gets it instantly."""
    logger.info("Prewarming Silero VAD...")
    proc.userdata["vad"] = silero.VAD.load()
    logger.info("Silero VAD ready.")


# ── SERVER + SESSION ENTRYPOINT ───────────────────────────────────────────────

server = AgentServer()


@server.rtc_session()
async def session_entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}
    logger.info(f"JARVIS session starting — room: {ctx.room.name}")

    await ctx.connect()

    fitbit = await fetch_fitbit()
    fitbit_ok = fitbit.get("connected", False)
    prior = read_log()

    augmented = INSTRUCTIONS + f"""

PRIOR SESSION LOG (last 30 lines):
{prior}

FITBIT STATUS THIS SESSION: {"connected" if fitbit_ok else "NOT connected"}
"""

    # Issue 2 fix: shutdown callback must be async
    async def on_shutdown() -> None:
        write_log(fitbit_ok, f"Session ended — room: {ctx.room.name}")

    ctx.add_shutdown_callback(on_shutdown)

    session = AgentSession(
        vad=ctx.proc.userdata["vad"],          # Issue 3: use prewarm-cached VAD
        stt=deepgram.STT(api_key=os.getenv("DEEPGRAM_API_KEY")),
        llm=google.LLM(                        # Issue 4: correct non-realtime model string
            model="gemini-2.0-flash-001",
            api_key=os.getenv("GEMINI_API_KEY"),
        ),
        tts=elevenlabs.TTS(                    # Issue 5: read both possible env var names
            api_key=_ELEVEN_KEY,
            voice_id=ELEVENLABS_VOICE_ID,
            model_id="eleven_turbo_v2_5",
        ),
    )

    await session.start(agent=JarvisAgent(augmented), room=ctx.room)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=session_entrypoint, prewarm_fnc=prewarm))
