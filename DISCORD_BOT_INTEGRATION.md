# Discord Bot Timer Integration — Instructions for AI Agent

## Overview

You are adding timer commands to an existing Python Discord bot. The bot communicates with a local todo/planner app through **Firebase Realtime Database REST API**. No Firebase SDK is needed — just HTTP requests. The bot writes commands to Firebase, the local app polls for them, executes them (creating time entries in its local database), and writes results back to Firebase for the bot to read.

**The server side is already built.** You only need to implement the Discord bot commands.

**Single-user system.** Only the bot owner can use timer commands. Auth is two-layered:
1. **Discord side**: check `ctx.author.id` matches the owner's ID — reject everyone else
2. **Firebase side**: a shared `secret` field in every command — the server rejects mismatches

---

## Architecture

```
Bot owner (Discord)  →  Bot (Python, HTTP)  →  Firebase REST API  →  Local server (polls)  →  SQLite
                              ↑                                              |
                              └───────────── command-results ────────────────┘
```

No SDKs needed. Both sides use plain HTTP (fetch on Node.js, requests/aiohttp on Python).

---

## Configuration the Bot Needs

These values must be available to the bot (env vars, config file, however the bot handles config):

```
FIREBASE_DATABASE_URL=https://sync-apps-845f7-default-rtdb.europe-west1.firebasedatabase.app
FIREBASE_DATABASE_SECRET=<the Firebase database secret - get from owner>
DISCORD_SYNC_SECRET=<shared secret string - get from owner>
TIMER_OWNER_ID=<owner's Discord user ID>
```

The owner will provide `FIREBASE_DATABASE_SECRET` and `DISCORD_SYNC_SECRET`. Both are static strings set once.

---

## Firebase REST API Pattern

All Firebase operations are simple HTTP requests. The database secret is passed as a query parameter.

**Base URL:** `{FIREBASE_DATABASE_URL}/{path}.json?auth={FIREBASE_DATABASE_SECRET}`

| Operation | HTTP Method | Body |
|-----------|------------|------|
| Read data | GET | none |
| Write/overwrite | PUT | JSON |
| Push (auto-ID) | POST | JSON |
| Update fields | PATCH | JSON |

All responses are JSON.

---

## Firebase Data Structure

### `/discord-sync/commands/{auto-id}` — Bot WRITES here (POST)

```json
{
  "type": "start",
  "description": "Working on login page",
  "project": "Web App",
  "goal": "Finish Auth",
  "secret": "<DISCORD_SYNC_SECRET>",
  "timestamp": 1712345678000,
  "processed": false
}
```

**Required fields for ALL commands:**
| Field | Type | Description |
|-------|------|-------------|
| `type` | string | `"start"`, `"stop"`, `"status"`, `"projects"`, `"goals"` |
| `secret` | string | The shared secret (DISCORD_SYNC_SECRET) |
| `timestamp` | int | Current time in milliseconds |
| `processed` | bool | Always `false` |

**Extra fields for `start`:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `description` | string | no | What to work on. If omitted, uses server's default title |
| `project` | string | no | Project name (fuzzy-matched) |
| `goal` | string | no | Goal name (fuzzy-matched) |

**Extra fields for `goals`:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `project` | string | no | Filter goals by project name |

### `/discord-sync/command-results/{command-id}` — Bot READS here (GET)

The server writes results here after processing. The key matches the auto-ID from the POST response.

```json
{
  "success": true,
  "message": "Timer started: \"Working on login page\" | Project: Web App",
  "timestamp": 1712345678500
}
```

| Field | Type | Always | Description |
|-------|------|--------|-------------|
| `success` | bool | yes | Whether it worked |
| `message` | string | yes | Human-readable — display this to the user |
| `timestamp` | int | yes | When processed |
| `duration` | int | stop only | Seconds elapsed |
| `entryId` | int | stop only | Database entry ID |

### `/discord-sync/timer-state/` — Bot can READ directly (GET)

For fast status checks without the command queue. Works even if local app is offline (shows last known state).

```json
{
  "active": true,
  "description": "Working on login page",
  "projectName": "Web App",
  "goalName": "Finish Auth",
  "startTime": 1712345678000,
  "lastUpdated": 1712345678000
}
```

---

## Commands to Implement

### `!timer start [description] [project:<name>] [goal:<name>]`

Start a timer. All arguments optional — no description means server uses its default title.

```
!timer start                                          → default title
!timer start Working on the login page                → custom title
!timer start Bug fixes project:Web App                → with project
!timer start Studying project:School goal:Textbook    → with project + goal
```

**Parsing:** everything before `project:` or `goal:` is the description. Keywords can appear in any order. Names are fuzzy-matched — "web" matches "Web App".

### `!timer stop`
Stop the current timer. Server logs the time entry.

### `!timer status`
Check if a timer is running. **Read `/discord-sync/timer-state/` directly** — faster, works offline.

### `!timer projects`
List available projects.

### `!timer goals [project:<name>]`
List available goals, optionally filtered by project.

---

## Implementation

### No extra dependencies needed

Use `requests` (sync) or `aiohttp` (async) — whichever the bot already has. If neither, `requests` is simplest.

### Owner Check

```python
import os

TIMER_OWNER_ID = int(os.getenv("TIMER_OWNER_ID", "0"))

def is_timer_owner(ctx):
    return ctx.author.id == TIMER_OWNER_ID
```

### Core Helper: Send Command and Wait for Result

```python
import time
import requests
import os

FIREBASE_DB_URL = os.getenv("FIREBASE_DATABASE_URL", "").rstrip("/")
FIREBASE_DB_SECRET = os.getenv("FIREBASE_DATABASE_SECRET", "")
DISCORD_SYNC_SECRET = os.getenv("DISCORD_SYNC_SECRET", "")

def fb_url(path):
    return f"{FIREBASE_DB_URL}/{path}.json?auth={FIREBASE_DB_SECRET}"

def send_timer_command(cmd_data, timeout=15):
    """POST a command to Firebase, then poll for the result."""
    # Push command
    resp = requests.post(fb_url("discord-sync/commands"), json={
        **cmd_data,
        "secret": DISCORD_SYNC_SECRET,
        "timestamp": int(time.time() * 1000),
        "processed": False,
    })
    resp.raise_for_status()
    cmd_id = resp.json()["name"]  # Firebase returns {"name": "<auto-id>"}

    # Poll for result
    result_url = fb_url(f"discord-sync/command-results/{cmd_id}")
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = requests.get(result_url)
        result = r.json()
        if result is not None:
            return result
        time.sleep(0.5)

    return {"success": False, "message": "Timed out — is the local app running?"}
```

**For async bots (discord.py):** wrap in `asyncio.to_thread()`:

```python
import asyncio

async def send_timer_command_async(cmd_data, timeout=15):
    return await asyncio.to_thread(send_timer_command, cmd_data, timeout)
```

### Argument Parsing

```python
import re

def parse_start_args(args_str):
    """Parse 'description project:Foo goal:Bar' into components."""
    project = None
    goal = None

    proj_match = re.search(r'\bproject:(.+?)(?=\bgoal:|$)', args_str, re.IGNORECASE)
    goal_match = re.search(r'\bgoal:(.+?)(?=\bproject:|$)', args_str, re.IGNORECASE)

    cut_start = len(args_str)
    if proj_match:
        project = proj_match.group(1).strip()
        cut_start = min(cut_start, proj_match.start())
    if goal_match:
        goal = goal_match.group(1).strip()
        cut_start = min(cut_start, goal_match.start())

    description = args_str[:cut_start].strip()

    return {
        "description": description or None,
        "project": project,
        "goal": goal,
    }
```

### Direct Status Read

```python
def read_timer_status():
    """Read timer state directly from Firebase — no command queue needed."""
    r = requests.get(fb_url("discord-sync/timer-state"))
    state = r.json()

    if not state or not state.get("active"):
        return "No timer running."

    elapsed_sec = int((time.time() * 1000 - state["startTime"]) / 1000)
    h, remainder = divmod(elapsed_sec, 3600)
    m, s = divmod(remainder, 60)

    msg = f'Timer running: "{state.get("description", "?")}" | {h}h {m}m {s}s'
    if state.get("projectName"):
        msg += f" | Project: {state['projectName']}"
    if state.get("goalName"):
        msg += f" | Goal: {state['goalName']}"

    last_updated = state.get("lastUpdated", 0)
    if time.time() * 1000 - last_updated > 120_000:
        msg += "\n(local app may be offline)"

    return msg
```

---

## Full Command Handler

Adapt to your bot's structure (cog, slash commands, etc.):

```python
@bot.command(name="timer")
async def timer_cmd(ctx, action: str = "status", *, args: str = ""):
    if not is_timer_owner(ctx):
        await ctx.send("You don't have access to timer commands.")
        return

    if action == "start":
        parsed = parse_start_args(args)
        cmd = {"type": "start"}
        if parsed["description"]:
            cmd["description"] = parsed["description"]
        if parsed["project"]:
            cmd["project"] = parsed["project"]
        if parsed["goal"]:
            cmd["goal"] = parsed["goal"]

        await ctx.send("Starting timer...")
        result = await send_timer_command_async(cmd)
        await ctx.send(result["message"])

    elif action == "stop":
        await ctx.send("Stopping timer...")
        result = await send_timer_command_async({"type": "stop"})
        await ctx.send(result["message"])

    elif action == "status":
        msg = await asyncio.to_thread(read_timer_status)
        await ctx.send(msg)

    elif action == "projects":
        result = await send_timer_command_async({"type": "projects"})
        await ctx.send(result["message"])

    elif action == "goals":
        cmd = {"type": "goals"}
        if args.strip():
            parsed = parse_start_args(args)
            if parsed["project"]:
                cmd["project"] = parsed["project"]
        result = await send_timer_command_async(cmd)
        await ctx.send(result["message"])

    else:
        await ctx.send(
            "**Usage:**\n"
            "`!timer start [description] [project:<name>] [goal:<name>]`\n"
            "`!timer stop`\n"
            "`!timer status`\n"
            "`!timer projects`\n"
            "`!timer goals [project:<name>]`"
        )
```

---

## Server Response Messages

Display `result["message"]` directly to the user:

| Scenario | `success` | `message` |
|----------|-----------|-----------|
| Unauthorized | false | `Unauthorized.` |
| Start OK | true | `Timer started: "desc" \| Project: X \| Goal: Y` |
| Already running | false | `Timer already running: "desc". Stop it first.` |
| Project not found | false | `Project "X" not found. Available: A, B, C` |
| Goal not found | false | `Goal "X" not found. Available: A, B, C` |
| Stop OK | true | `Timer stopped: "desc" \| Duration: 1h 23m 45s` |
| Nothing to stop | false | `No timer is currently running.` |
| Status running | true | `Timer running: "desc" \| 0h 23m 15s` |
| Status idle | true | `No timer running.` |
| Timeout | false | `Timed out — is the local app running?` |

---

## Setup Checklist for Bot Owner

1. Generate shared secret: `python -c "import secrets; print(secrets.token_urlsafe(32))"`
2. Give the bot developer these 3 values:
   - `FIREBASE_DATABASE_URL` (the Firebase Realtime Database URL)
   - `FIREBASE_DATABASE_SECRET` (from Firebase Console → Project Settings → Service Accounts → Database Secrets)
   - `DISCORD_SYNC_SECRET` (the generated shared secret)
3. Tell them your Discord user ID for `TIMER_OWNER_ID`
4. No pip packages needed beyond `requests` (which most bots already have)
