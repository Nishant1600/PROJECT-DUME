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

---

<div align="center">
<sub>Features 1–7 ship today. 8–15 need only a one-time connector sign-in. 16–20 are the near-term roadmap.</sub>
<br/><sub>Priorities set with Nishant: <b>Email · Notion · Calendar · Code brain-bursting</b> first.</sub>
</div>
