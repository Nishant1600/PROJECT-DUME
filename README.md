<div align="center">

<img src="docs/banner.svg" alt="PROJECT·DUME" width="100%" />

<h1>⟡ PROJECT·DUME ⟡</h1>

<b>A voice &amp; screen assistant for Windows — with the mind of Claude.</b><br/>
<sub>Say “Hey Jarvis”. It listens, thinks, talks back, reads your screen, and gets things done — behind a movie-style HUD.</sub>

<br/><br/>

<img src="https://img.shields.io/badge/python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.12" />
<img src="https://img.shields.io/badge/three.js-HUD-000000?style=for-the-badge&logo=three.js&logoColor=white" alt="three.js" />
<img src="https://img.shields.io/badge/Claude-brain-D97757?style=for-the-badge&logo=anthropic&logoColor=white" alt="Claude" />
<img src="https://img.shields.io/badge/Windows-11-0078D6?style=for-the-badge&logo=windows11&logoColor=white" alt="Windows 11" />
<img src="https://img.shields.io/badge/voice-100%25%20local-54d6ff?style=for-the-badge" alt="Local voice" />

<br/>

<img src="https://readme-typing-svg.demolab.com/?font=Chakra+Petch&weight=600&size=22&duration=2600&pause=700&color=54D6FF&center=true&vCenter=true&width=720&lines=listening...;transcribing+on+the+GPU;thinking+with+Claude;working+on+your+desktop;%22Good+day%2C+sir.%22" alt="status" />

</div>

---

<div align="center">
<img src="docs/hud.jpg" alt="The JARVIS HUD" width="88%" />
<br/><sub>The live HUD — a reactive energy core that changes colour with what JARVIS is doing, a live activity feed, the conversation, and a context meter.</sub>
</div>

---

## ✦ What it does

> 🎙️ **Hears you** — say *“Hey Jarvis”* once, then just talk. It waits through natural pauses and only replies when your sentence sounds finished.
> 🧠 **Thinks with Claude** — the Claude Code CLI is the brain, on your own subscription (no API key).
> 🗣️ **Talks back** — a calm British voice, streamed sentence-by-sentence as Claude writes.
> 👁️ **Reads your screen** — window text, OCR, or a screenshot, whichever is cheapest.
> 🖥️ **Runs your PC** — full terminal and desktop control, with hard safety limits.
> 🌌 **Shows it all** — a three.js HUD that glows, pulses to your voice, and never looks like a terminal.

## ✦ Personal &amp; always-on

- **Knows you** — addresses you as *“Sir”*, greets you by name, and remembers you between sessions.
- **Learns how you work** — after each conversation it distils your projects, tools, hours and habits into `memory/profile.md` and uses them next time, so answers fit your style without being asked.
- **Always there** — starts at login and waits for *“Hey Jarvis”*:
  ```bash
  python -m jarvis.main --install-autostart      # (and --uninstall-autostart)
  ```
- **Your day, done by voice** — once you connect Gmail, Notion, Calendar and GitHub (`/mcp`, then `integrations.use_my_connectors: true`), it checks mail, your Notion tasks, your calendar and your PRs, and brain-bursts on code with you.

## ✦ Roadmap &amp; progress

JARVIS grows in layers — and **everything below is already shipping.** Gmail, Notion, Google Calendar and Drive are all connected; the vision keeps widening from there.

**✅ Working today**
- **Knows &amp; learns you** — identity, an editable rulebook, and a mind that learns your corrections *and* your coding &amp; design style over time — all on a tight token budget
- **Always there** — starts at login, lives in the tray, holds a true back-and-forth conversation behind the reactive HUD
- **Safe hands** — `rm -rf` hard-blocked; every send / submit / delete asks first
- **Your day in a word** — *"status"* reads the mail that matters grouped by who it's from, *"brief me"* folds in your calendar, Notion tasks and deadlines, and a **deadline tracker** warns you before things are due
- **Email in your voice** — triages Gmail by sender, knows your mail accounts, and drafts replies that sound like *you* — always shown before send
- **Knowledge engine** — instant recall across everything it remembers, an **offline document brain** that answers from your own notes and PDFs, *"what's new"* briefings, researched answers *with citations*, and lecture/meeting transcription
- **Developer co-pilot** — scaffolds sites in your stack, live preview with self-review, design-to-code in your own patterns, a built-in SEO audit, PR &amp; deploy helpers, and a spoken *"walk me through my changes"* diff review
- **Runs your day** — reminders, proactive nudges, named routines, a daily plan &amp; recap, a one-word **focus mode**, and hands-free control of any app
- **Yours to shape** — pick your **voice and wake word** by voice, see a **usage radar** of how your plan is trending, and keep talking through a **zero-cloud fallback** when the network drops or you're saving usage

**🛠 On the horizon** — achievable next steps we're reaching for
- **Scheduled routines** — *"every weekday at nine, brief me"* — your named routines on a timer
- **Train your own wake word** — a guided flow to teach it a brand-new trigger phrase
- **Offline screen sense** — a small local vision model reads the screen when you're off the grid
- **Live meeting notetaker** — transcribe and summarise a call *as it happens*, not just afterwards
- **Reach your phone** — push a nudge, a brief or a finished job to your phone
- **Secure form-fill vault** — an opt-in, encrypted store of your profile details (never passwords) to speed up the forms JARVIS fills *with* you

📋 Full detail, feature by feature → **[FEATURES.md](FEATURES.md)**

## ✦ The core, by state

| State | Colour | Meaning |
|:--|:--|:--|
| ● **Standby** | steel blue | waiting for “Hey Jarvis” |
| ● **Listening** | cyan | hearing you — your words appear live |
| ● **Thinking** | violet | Claude is working it out |
| ● **Speaking** | bright cyan | replying out loud |
| ● **Working** | amber | acting on your desktop |

<div align="center">
<img src="docs/hud-thinking.jpg" alt="JARVIS thinking" width="70%" />
<br/><sub>Thinking — the core turns violet and churns while Claude reasons.</sub>
</div>

## ✦ Quickstart

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

Then **double-click `Start JARVIS.vbs`** — the HUD opens in its own window, no terminal.

```bash
# or from a terminal
.venv\Scripts\python -m jarvis.main            # HUD + voice + text box
.venv\Scripts\python -m jarvis.main --no-voice # HUD only, type in the box
.venv\Scripts\python -m jarvis.main --text     # plain terminal (debug)
```

> **Needs:** Windows 11, Python 3.12, an NVIDIA GPU (optional, speeds up speech-to-text),
> and the [Claude Code](https://claude.com/claude-code) CLI signed in. The HUD loads three.js from a CDN on first run.

## ✦ Say anything

| Say / type | What happens |
|:--|:--|
| *anything* | goes to Claude; the reply is spoken and shown |
| *“status”* | important unread mail, grouped by who it's from |
| *“brief me” / “good morning”* | mail + calendar + Notion + deadlines, in a sentence or two |
| *“add deadline &lt;thing&gt; by Friday”* · *“what's due?”* | track a due date; hear what's coming |
| *“focus”* · *“I'm back”* | hush / resume proactive nudges |
| *“walk me through my changes”* | spoken review of your working-tree diff |
| *“explain / translate / tidy what I just copied”* | the clipboard genie |
| *“change your voice to …”* · *“speak faster”* | pick your voice and pace |
| *“usage”* | how your usage is trending, and when the session rotates |
| *“new session”* · *“switch project to &lt;name&gt;”* · *“session status”* | session control |
| *“remember &lt;something&gt;”* | saved for every future session |
| *“goodbye”* | quit |

**Kill switch:** `Ctrl+Alt+J` (or the HUD **stop** button) halts the task and blocks all desktop actions until your next request.

## ✦ What it's allowed to do

Full terminal and desktop access, with two limits in [`command_policy.yaml`](jarvis/command_policy.yaml):

```diff
- BLOCKED  never runs, even if you say yes
    rm -rf and every recursive forced delete · deleting a drive root,
    Windows, Program Files or your user folder · formatting a disk
! CONFIRM  runs only after you approve (voice "yes" or the HUD)
    any delete · shutdown/restart · killing processes · installing software
    registry / service / scheduled-task changes · git history rewrites
+ ALLOWED  everything else runs freely
```

Two layers enforce it: the confirmation step, **and** a Claude Code `PreToolUse` hook that denies blocked commands even if a tool was pre-approved.

## ✦ How it fits together

```
  🎙  mic ─▶ "Hey Jarvis" ─▶ silero VAD ─▶ faster-whisper (GPU) ─▶ text
                                                                    │
                                                        ┌───────────▼───────────┐
   ⌨  HUD text box ──────────────────────────────────▶ │   Assistant (policy)  │
                                                        └───────────┬───────────┘
                                                 claude -p --stream-json --resume
                                                        │  tools · desktop MCP
                                                        ▼
   streamed reply ─▶ sentences ─▶ edge-tts 🔊     +     live events ─▶ three.js HUD 🌌
```

## ✦ Keeping usage low

- New session after **15 min idle** or **60k tokens** — a Haiku-written summary carries over.
- Only JARVIS's own tools are loaded, so a turn starts at ~15k tokens.
- Screen reading is text-first: window list → UI tree → OCR → screenshot last.
- Local commands (new session, status, remember…) never call Claude.

## ✦ Project map

```
Start JARVIS.vbs          no-terminal launcher
jarvis/main.py            boots the HUD server, window, voice, kill switch
jarvis/assistant.py       orchestrator: voice + HUD → Claude → speech + HUD
jarvis/voice_loop.py      wake → listen (live level + partial) → assistant
jarvis/policy.py          command classifier (allow / confirm / block)
jarvis/guard_hook.py      Claude Code PreToolUse hook — hard block backstop
jarvis/bus.py · ui_server.py   event bus + SSE server for the HUD
jarvis/hud/               the three.js HUD (index.html, core.js, app.js, hud.css)
jarvis/brain/             runs `claude -p`, session resume / rotate / summary
jarvis/desktop_mcp/       screen reading + mouse/keyboard (MCP server)
jarvis/audio/             wake word · speech-to-text · text-to-speech
```

<div align="center">
<br/>
<sub>Built by <b>Nishant Sharma</b> · powered locally, thinks with Claude.</sub>
<br/>
<img src="https://readme-typing-svg.demolab.com/?font=IBM+Plex+Mono&size=14&duration=4000&pause=1000&color=43566D&center=true&width=420&lines=%22At+your+service%2C+sir.%22" alt="" />
</div>
