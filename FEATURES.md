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

## 🔌 Day-to-day assistant (flip on once you connect the tool)

Connect Gmail, Notion, Google Calendar and GitHub once via `/mcp`, set `integrations.use_my_connectors: true`, and these come alive.

| # | Feature | What it does |
|:--:|:--|:--|
| 8 | **Morning brief** | On startup (or on *“brief me”*): important unread mail, today's calendar, Notion tasks due — in two or three spoken sentences. (`integrations.startup_brief`) |
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
| 16 | **Proactive nudges** | Learns your rhythm and speaks up on its own: *“You usually commit around now — want me to run the tests?”*, *“That meeting starts in 10 minutes.”* |
| 17 | **Daily & weekly recap** | An end-of-day summary of what you did and what's still open, written to Notion or spoken back. |
| 18 | **Routines you name** | *“Every morning, brief me and open my project.”* Save multi-step routines and trigger them by one phrase. |
| 19 | **System tray presence** | Lives quietly in the tray with a status dot; the HUD opens only when you summon it, so startup is invisible until you speak. |
| 20 | **Your voice, offline fallback** | A premium cloud voice option, and a fully-offline mode (Piper) for when there's no internet. |

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
| 25 | **"Status" briefing** | One word → important unread, grouped by company/sender, a line each; then *"read the one from X."* |
| 26 | **Multi-account inbox** | Several Gmail accounts at once; *"reply from my college account."* |
| 27 | **Sender/company intel** | Names who mailed me and why — recruiter, invoice, newsletter. |
| 28 | **Replies in my voice** | Drafts matched to how I write, shown before send. |

**Studies**

| # | Feature | What it does |
|:--:|:--|:--|
| 29 | **Study tutor** | *"Explain this topic"* → saves notes to Notion, builds a personal knowledge base. |
| 30 | **Deadline tracker** | Pulls due dates from mail / calendar / Notion and warns me ahead. |
| 31 | **Revision mode** | Quizzes me from my own notes, spaced repetition. |
| 32 | **Research & citations** | Reads papers and pages, summarizes, keeps the references. |
| 33 | **Lecture capture** | Transcribes a recording or live audio → summary + action items. |

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
| 39 | **Daily planner** | Morning plan from calendar + tasks + mail; evening recap of what moved. |
| 40 | **Universal recall** | *"Remember this,"* *"what did I say about X last week?"* — searchable memory across voice, mail and notes. |
| 42 | **"What's new" briefings** | *"Tell me something new in Three.js / my field"* — tracks topics I follow and surfaces what changed. |
| 43 | **Full system control + auto-apply** | Drives any app by voice (mouse + keyboard): reads my Telegram internship bot and applies on my behalf, fills forms, operates the whole PC — **always confirming before it submits, sends, buys or deletes.** |

---

<div align="center">
<sub>Features 1–7 ship today. 8–15 need only a one-time connector sign-in. 16–20 are the near-term roadmap.</sub>
<br/><sub>Priorities set with Nishant: <b>Email · Notion · Calendar · Code brain-bursting</b> first.</sub>
</div>
