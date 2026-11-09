<div align="center">

# ⟡ JARVIS — Feature Roadmap

**The 20 must-have features to run Nishant's day.**

`✅ built  ·  🔌 built, needs a connector you authorize once (/mcp)  ·  🛠 planned`

</div>

---

## ✅ Already in (the foundation)

These make JARVIS *yours*, not a generic bot.

| # | Feature | What it does |
|:--:|:--|:--|
| 1 | **Knows you** | Addresses you as *“Sir”*, greets you by name and time of day, remembers you between sessions (`memory/identity.md`). |
| 2 | **Learns how you work** | After every conversation a profile is distilled — your projects, tools, hours, phrasing, recurring habits (e.g. *“runs pytest before every commit”*) — and fed into every future session, so answers fit your style without being asked. (`memory/profile.md`) |
| 3 | **Always on** | Starts automatically at login and waits silently for *“Hey Jarvis”*. No window to open, no command to run. |
| 4 | **Real conversation** | Waits through natural pauses, only replies when you've finished a thought, and lets you cut in mid-reply. Live captions as you speak. |
| 5 | **Movie-style HUD** | A reactive core that shows, at a glance, whether it's listening, thinking, speaking or working — plus a live activity feed and the conversation. |
| 6 | **Safe hands on the PC** | Full terminal + desktop control, but `rm -rf` and other destructive commands are hard-blocked, and risky ones ask first. |
| 7 | **Light on your Claude plan** | Auto-rotates sessions, loads only its own tools, reads the screen text-first. |

## 🔌 Day-to-day assistant (connectors — now live)

Gmail, Notion, Google Calendar and Drive are connected and `integrations.use_my_connectors: true`, so these are live. GitHub works through the `gh` CLI (no connector needed).

| # | Feature | What it does |
|:--:|:--|:--|
| 8 | **Morning brief** ✅ | On startup (or on *“brief me” / “good morning”*): important unread mail, today's calendar, Notion tasks and tracked deadlines — in two or three spoken sentences (`jarvis.brief`, `integrations.startup_brief`). |
| 9 | **Inbox triage** | *“Check my mail.”* Summarizes what's new, flags what matters, and drafts replies for your okay — never sends without confirmation. |
| 10 | **Notion workspace** | *“What's on my plate?”* Reads your tasks, notes and project pages; adds or updates items on request. |
| 11 | **Calendar & reminders** | *“What's my day look like?”* Reads your schedule, warns of clashes, and sets reminders and events (with confirmation). |
| 12 | **Code brain-bursting** | *“Let's think through the auth flow.”* Reads the repo you're in, talks through designs, writes and edits code, runs the tests — your pair-programmer by voice. |
| 13 | **Repo & PR watch** | *“Anything on my PRs?”* Checks GitHub for review comments, failing CI and open issues, and summarizes what needs you. |
| 14 | **Web lookups** | *“What changed in the Three.js r160 release?”* Searches and reads pages, then gives you the answer, not a list of links. |
| 15 | **Read any screen** | *“What does this error mean?”* Reads whatever window is in front of you and explains or acts on it. |

## 🛠 Next up (planned)

| # | Feature | What it does |
|:--:|:--|:--|
| 16 | **Proactive nudges** ✅ | Reminders you set (“remind me to X at 6pm”) fire on their own; opt-in nudges (e.g. uncommitted changes) speak up while idle — rate-limited, with quiet hours (`jarvis.nudges`). |
| 17 | **Daily & weekly recap** | An end-of-day summary of what you did and what's still open, written to Notion or spoken back. |
| 18 | **Routines you name** ✅ | *”Save routine morning: brief me and open my project”* then *”run morning”* — saved multi-step instructions triggered by one phrase (`jarvis.routines`). |
| 19 | **System tray presence** ✅ | A tray dot that changes colour with JARVIS's state; `--tray --no-window` starts invisible and opens the HUD only when you summon it (`jarvis.tray`). |
| 20 | **Your voice, offline fallback** ✅ | If the network is down, speech falls back automatically to a local voice — Piper if configured, else the built-in Windows voice (`synth_offline`). |

## 🚀 Phase 2 — being built now (21–43)

**Core brain — a rule section of my own, that learns**

| # | Feature | What it does |
|:--:|:--|:--|
| 21 | **JARVIS rulebook** ✅ | An always-loaded `memory/rules.md` (like Claude's rules): my hard do's/don'ts, tone, design defaults — authoritative every turn, hard-capped so it stays cheap. |
| 22 | **Learns from corrections** | *"Not like that"* / *"do more of this"* rewrites a rule, so next time differs — preference-based reinforcement. |
| 23 | **Design-pattern memory** | Learns my website style (stack, fonts, colours, layout, libraries) from repos I build; *"design me a site"* uses my patterns first. (`memory/design.md`) |
| 24 | **Context-budget guard** ✅ | Reads only the few files that matter, under hard token caps, so *"sees everything"* never burns my plan. |

**Email & accounts**

| # | Feature | What it does |
|:--:|:--|:--|
| 25 | **"Status" briefing** ✅ | One word → important unread, grouped by company/sender, a line each; then *"read the one from X."* (`jarvis.brief` status) |
| 26 | **Multi-account inbox** ✅ | Declare your accounts in config; *"reply from my college account"* resolves to the right address (`jarvis.accounts`). |
| 27 | **Sender/company intel** ✅ | Names who mailed me and why — recruiter, invoice, newsletter — grouped in the status briefing. |
| 28 | **Replies in my voice** ✅ | Learns how I write from my sent mail and drafts to match, shown before send (`jarvis.mailstyle`). |

**Studies**

| # | Feature | What it does |
|:--:|:--|:--|
| 29 | **Study tutor** | *"Explain this topic"* → saves notes to Notion, builds a personal knowledge base. |
| 30 | **Deadline tracker** ✅ | Track due dates by voice or let JARVIS register ones it finds in mail/calendar/Notion; warns me ahead, with heads-up nudges (`jarvis.deadlines`). |
| 31 | **Revision mode** | Quizzes me from my own notes, spaced repetition. |
| 32 | **Research & citations** ✅ | Web search/fetch, answers with sources, and saves them (`jarvis.research` → `memory/references.md`). |
| 33 | **Lecture capture** ✅ | Transcribes a recording to a timestamped transcript with the whisper model already loaded (`jarvis.lecture`) → summary + action items. |

**Website / development**

| # | Feature | What it does |
|:--:|:--|:--|
| 34 | **Scaffolder from my template** ✅ | New site spun up in my preferred stack/boilerplate (`jarvis.webdev scaffold`). |
| 35 | **Live preview + self-review** ✅ | Finds the dev command + URL (`jarvis.webdev preview-info`), opens it, screenshots, critiques against my rules. |
| 36 | **Design-to-code** ✅ | Describe or sketch → built to my learned patterns → iterates on *"make the hero bigger."* |
| 37 | **PR & repo buddy** ✅ | Reviews diffs, lists open PRs with CI + review state, drafts PR descriptions — via `gh`, no connector needed (`jarvis.repo`). |
| 38 | **Deploy helper** ✅ | Detects the host (Vercel / Netlify / Pages / static) and the build + deploy command (`jarvis.repo deploy-info`); always confirms before shipping. |
| 41 | **SEO built in** ✅ | Dependency-free on-page SEO audit — title, meta, OG, headings, alt, canonical, JSON-LD, viewport (`jarvis.webdev seo`). |

**Day-to-day / other**

| # | Feature | What it does |
|:--:|:--|:--|
| 39 | **Daily planner** ✅ | Plan my day and recap it from recent memory now; richer once calendar + mail are connected. |
| 40 | **Universal recall** ✅ | *"What did I say about X?"* — searches all my memory + the conversation (`jarvis.recall`). |
| 42 | **"What's new" briefings** ✅ | Tracks topics I follow (`memory/topics.md`) and uses web search to surface what changed. |
| 43 | **Full system control by voice** ✅ | Drives any app with mouse, keyboard and screen reading — opens apps, fills forms, operates the whole PC hands-free — **always confirming before it submits, sends, buys or deletes, and never typing secrets.** |

## ✅ Phase 2 — "On the horizon", now shipping (44–50)

The ideas that were on the horizon are built:

| # | Feature | What it does |
|:--:|:--|:--|
| 44 | **Clipboard genie** ✅ | *"Explain / translate / tidy what I just copied"* — reads the clipboard, does it, puts rewrites back ready to paste (`jarvis.clipboard`). |
| 45 | **Focus mode** ✅ | *"Focus"* hushes every proactive nudge; *"I'm back"* resumes. Reminders are held, not lost (`jarvis.focus`). |
| 46 | **Offline document brain** ✅ | Point it at a folder; it answers from my notes and PDFs with a pure-Python BM25 index — no network (`jarvis.docbrain`). |
| 47 | **Spoken diff review** ✅ | *"Walk me through my changes"* — a plain-spoken, file-by-file tour of my diff before I commit (`jarvis.diffreview`). |
| 48 | **Make it yours** ✅ | Pick my voice and pace (*"use an Indian English voice"*, *"speak faster"*) and set the wake word — all by voice (`jarvis.voices`). |
| 49 | **Zero-cloud fallback** ✅ | Trivia answered locally with no Claude call; if the network drops, a local Ollama model (or built-ins) still replies (`jarvis.offline_brain`). |
| 50 | **Usage radar** ✅ | *"Usage"* → how today compares to my recent average and how close this session is to rotating (`jarvis.usage`). |

## ✅ Phase 3 — now shipping (51–70)

Everything from the last horizon is built, plus reliability, studies and dev depth. All unit-tested.

**Staying alive & honest**

| # | Feature | What it does |
|:--:|:--|:--|
| 51 | **Auth watchdog** ✅ | Tracks the headless Claude sign-in's expiry, notices auth failures, and tells me to run `claude setup-token` *before* the always-on brain goes quiet (`jarvis.authwatch`). |
| 52 | **Self health-check** ✅ | *"Are you okay?"* checks mic, TTS, GPU, the CLI, connectors, disk and sign-in, and reports what needs attention in one spoken line (`jarvis.healthcheck`). |
| 53 | **Crash watchdog** ✅ | Supervises the app and relaunches it on an unexpected exit, with exponential backoff and a crash-loop guard, logged to `logs/watchdog.log` (`jarvis.watchdog`). |
| 54 | **Scheduled routines** ✅ | *"Brief me every weekday at nine"* — named instructions on a timer (daily / weekdays / weekly / interval), fired when idle (`jarvis.schedules`). |

**Studies**

| # | Feature | What it does |
|:--:|:--|:--|
| 55 | **Study tutor** ✅ | *"Teach me &lt;topic&gt;"* — explains it, then saves revision notes to a personal knowledge base (and Notion when connected) (`jarvis.tutor` / `jarvis.studykb`). |
| 56 | **Revision mode** ✅ | *"Quiz me"* — spaced-repetition questioning from my own saved notes, with SM-2 scheduling (`jarvis.revision`). |
| 57 | **Live notetaker** ✅ | *"Take notes"* keeps a timestamped transcript; *"stop taking notes"* turns it into a summary with decisions and action items (`jarvis.notetaker`). |
| 58 | **Agenda board** ✅ | *"What's on my board?"* — a glanceable view of deadlines, schedules and reminders, spoken and on the HUD (`jarvis.agenda`). |

**Day-to-day**

| # | Feature | What it does |
|:--:|:--|:--|
| 59 | **Daily & weekly recap** ✅ | *"Recap my week"* — what got done and what's still open, spoken and saved to Notion (`jarvis.recap`). |
| 60 | **Smart scheduling** ✅ | *"Find me an hour tomorrow for revision"* — proposes a real free slot and creates the event after I confirm (`jarvis.scheduling`). |
| 61 | **Reach your phone** ✅ | Push a brief or a finished job to my phone via ntfy / Pushover / Telegram — *"send that to my phone"* (`jarvis.push`). |
| 62 | **Secure form-fill vault** ✅ | Profile details (never passwords), encrypted at rest with Windows DPAPI, to help fill forms faster (`jarvis.vault`). |
| 63 | **Multilingual mode** ✅ | *"Speak in Hindi"* flips both listening and speaking language on command (`jarvis.lang`). |

**Website / development**

| # | Feature | What it does |
|:--:|:--|:--|
| 64 | **Accessibility audit** ✅ | A dependency-free a11y pass — alt text, labels, landmarks, zoom, WCAG contrast — the sibling of the SEO audit (`jarvis.a11y`). |
| 65 | **Performance budget** ✅ | Local page-weight check: oversized images, render-blocking scripts, layout-shift risks, against a budget (`jarvis.perf`). |
| 66 | **Your component library** ✅ | Save your own UI blocks and reuse them — *"build it with my hero section"* (`jarvis.components`). |
| 67 | **Full PR flow** ✅ | *"Open a PR"* — branch → commit → push → `gh pr create` → CI summary, confirming before each outward step (`jarvis.prflow`). |

**Voice & interaction**

| # | Feature | What it does |
|:--:|:--|:--|
| 68 | **Train your own wake word** ✅ | A guided openWakeWord flow: plans the job, synthesises samples, and walks me to a trained `.onnx` (`jarvis.waketrain`). |
| 69 | **Offline screen sense** ✅ | Answers "what's on my screen / is there an error" locally from window text when off-grid — optional local vision model (`jarvis.screensense`). |
| 70 | **Follow-up memory** ✅ | After a status briefing, *"read the one from the bank"* resolves to the right sender without re-asking (`jarvis.followup`). |

---

<div align="center">
<sub>Everything above is shipping; Gmail, Notion, Calendar and Drive are connected.</sub>
<br/><sub>70 features built — reliability, studies, day-to-day, dev and voice, every unit tested.</sub>
</div>
