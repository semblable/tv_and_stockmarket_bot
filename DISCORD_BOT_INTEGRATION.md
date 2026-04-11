# Discord Bot Timer Integration Spec

## Purpose

Add timer commands to the existing Python Discord bot so it can read and write timer state through the Firebase Realtime Database REST API.

This is a bot-side integration only:

- The Discord bot reads and writes Firebase directly.
- The local server syncs Firebase data with SQLite.
- Only the bot owner can use the timer commands.

No Firebase SDK is required. Plain HTTP requests are enough.

---

## System Responsibilities

```
Bot (Python, HTTP)  ◄──── reads/writes ────►  Firebase REST API
                                                    │
                                        (server syncs periodically)
                                                    │
                                              Local server  ──►  SQLite
```

### Bot responsibilities

- Read the current timer state from Firebase.
- Start and stop timers by writing to Firebase.
- Read synced projects and goals from Firebase for command resolution.
- Reject timer commands from anyone except the configured owner.

### Local server responsibilities

- Sync projects and goals from SQLite to Firebase every 30 seconds and on startup.
- Drain completed entries from Firebase into SQLite when it is online.
- Restore shared timer state from Firebase on restart.

### Shared behavior

- Firebase is the single source of truth for the active timer.
- Discord and the browser UI must respect the same timer state.
- Only one timer can be active at a time across all clients.

---

## Required Configuration

Provide these values to the bot through env vars or the bot's existing config system:

```text
FIREBASE_DATABASE_URL=https://sync-apps-845f7-default-rtdb.europe-west1.firebasedatabase.app
FIREBASE_DATABASE_SECRET=<owner-provided Firebase REST auth credential>
TIMER_OWNER_ID=<owner Discord user ID>
```

Notes:

- `FIREBASE_DATABASE_URL` should not include a trailing slash.
- `FIREBASE_DATABASE_SECRET` is the credential passed in the Firebase REST `auth` query parameter.
- `TIMER_OWNER_ID` is the only Discord user allowed to run timer commands.

---

## Firebase REST Access Pattern

All Firebase operations are standard HTTP requests that return JSON.

**Base URL pattern:** `{FIREBASE_DATABASE_URL}/{path}.json?auth={FIREBASE_DATABASE_SECRET}`

| Operation | HTTP Method | Body |
|-----------|-------------|------|
| Read data | GET | none |
| Write or overwrite | PUT | JSON |
| Push with auto ID | POST | JSON |
| Update fields | PATCH | JSON |

---

## Firebase Data Contract

### `/discord-sync/timer-state`

The bot and browser both read and write this path. It is the single source of truth for whether a timer is running.

```json
{
  "active": true,
  "description": "Working on login page",
  "projectName": "Web App",
  "goalName": "Finish Auth",
  "projectId": 3,
  "goalId": 7,
  "startTime": 1712345678000,
  "sessionId": "discord-1712345678000-a1b2c3",
  "origin": "discord",
  "lastUpdated": 1712345678000
}
```

| Field | Type | Description |
|-------|------|-------------|
| `active` | bool | Whether a timer is currently running |
| `description` | string | What the user is working on |
| `projectName` | string\|null | Display name of the selected project |
| `goalName` | string\|null | Display name of the selected goal |
| `projectId` | int\|null | Project ID used during SQLite sync |
| `goalId` | int\|null | Goal ID used during SQLite sync |
| `startTime` | int | Epoch milliseconds when the timer started |
| `sessionId` | string | Unique ID used for deduplication |
| `origin` | string | `"discord"` or `"browser"` |
| `lastUpdated` | int | Epoch milliseconds of the latest write |

### `/discord-sync/pending-entries/{auto-id}`

The bot writes a completed entry here when a timer is stopped. The local server later syncs these entries into SQLite.

```json
{
  "description": "Working on login page",
  "startTime": "2025-04-05T10:00:00.000Z",
  "endTime": "2025-04-05T11:23:45.000Z",
  "duration": 5025,
  "projectId": 3,
  "goalId": 7,
  "sessionId": "discord-1712345678000-a1b2c3",
  "createdAt": 1712350703000
}
```

| Field | Type | Description |
|-------|------|-------------|
| `description` | string | Task description |
| `startTime` | string | ISO 8601 start time |
| `endTime` | string | ISO 8601 end time |
| `duration` | int | Duration in seconds |
| `projectId` | int\|null | Project ID |
| `goalId` | int\|null | Goal ID |
| `sessionId` | string | Must match the timer state's `sessionId` |
| `createdAt` | int | Epoch milliseconds when the entry was created |

### `/discord-sync/projects`

Array of projects synced from SQLite by the local server.

```json
[
  { "id": 1, "name": "Web App" },
  { "id": 2, "name": "Mobile" },
  { "id": 3, "name": "Backend" }
]
```

### `/discord-sync/goals`

Array of goals synced from SQLite by the local server.

```json
[
  { "id": 1, "description": "Finish Auth", "projectId": 1, "targetHours": 20 },
  { "id": 2, "description": "Write Tests", "projectId": 1, "targetHours": 10 }
]
```

---

## Command Contract

### `!timer start [description] [project:<name>] [goal:<name>]`

Start a timer. All arguments are optional. If no description is provided, use `"Discord timer"`.

```text
!timer start
!timer start Working on the login page
!timer start Bug fixes project:Web App
!timer start Studying project:School goal:Textbook
```

Parsing rules:

- Everything before `project:` or `goal:` is treated as the description.
- `project:` and `goal:` can appear in any order.
- Project and goal names are fuzzy-matched using case-insensitive substring matching.
- Starting a timer must fail if another timer is already active.

### `!timer stop`

Stop the current timer, write the completed entry to `pending-entries`, and clear the active timer state.

### `!timer status`

Read `timer-state` and report whether a timer is running.

### `!timer projects`

List available projects from Firebase.

### `!timer goals [project:<name>]`

List available goals from Firebase, optionally filtered by project.

---

## Implementation Guidance

Use the examples below as reference code. Adapt them to your bot structure, command framework, and dependency stack.

### Dependency choice

Use whichever HTTP client the bot already uses:

- `requests` for synchronous helpers
- `aiohttp` if the bot already has an async HTTP layer

### Owner check

```python
import os

TIMER_OWNER_ID = int(os.getenv("TIMER_OWNER_ID", "0"))

def is_timer_owner(ctx):
    return ctx.author.id == TIMER_OWNER_ID
```

### Firebase helpers

```python
import os
import time

import requests

FIREBASE_DATABASE_URL = os.getenv("FIREBASE_DATABASE_URL", "").rstrip("/")
FIREBASE_DATABASE_SECRET = os.getenv("FIREBASE_DATABASE_SECRET", "")

def fb_url(path):
    return f"{FIREBASE_DATABASE_URL}/{path}.json?auth={FIREBASE_DATABASE_SECRET}"

def fb_get(path):
    """Read data from Firebase."""
    r = requests.get(fb_url(path))
    r.raise_for_status()
    return r.json()

def fb_put(path, data):
    """Write or overwrite data in Firebase."""
    r = requests.put(fb_url(path), json=data)
    r.raise_for_status()
    return r.json()

def fb_post(path, data):
    """Push data with an auto-generated ID."""
    r = requests.post(fb_url(path), json=data)
    r.raise_for_status()
    return r.json()
```

### Project and goal resolution

```python
def resolve_project(name):
    """Fuzzy-match a project name against Firebase-synced projects."""
    if not name:
        return None
    projects = fb_get("discord-sync/projects") or []
    lower = name.lower()
    for p in projects:
        if p["name"].lower() == lower:
            return p
    matches = [p for p in projects if lower in p["name"].lower()]
    matches.sort(key=lambda p: len(p["name"]))
    return matches[0] if matches else None


def resolve_goal(name, project_id=None):
    """Fuzzy-match a goal description against Firebase-synced goals."""
    if not name:
        return None
    goals = fb_get("discord-sync/goals") or []
    if project_id:
        goals = [g for g in goals if g.get("projectId") == project_id]
    lower = name.lower()
    for g in goals:
        if g["description"].lower() == lower:
            return g
    matches = [g for g in goals if lower in g["description"].lower()]
    matches.sort(key=lambda g: len(g["description"]))
    return matches[0] if matches else None


def list_projects():
    """List available projects from Firebase. Returns a message string."""
    projects = fb_get("discord-sync/projects") or []
    if not projects:
        return "No projects found. (Run the local app at least once to sync.)"
    names = ", ".join(p["name"] for p in projects[:10])
    return f"Available projects: {names}"


def list_goals(project_name=None):
    """List available goals from Firebase. Returns a message string."""
    goals = fb_get("discord-sync/goals") or []
    if project_name:
        project = resolve_project(project_name)
        if project:
            goals = [g for g in goals if g.get("projectId") == project["id"]]
    if not goals:
        return "No goals found. (Run the local app at least once to sync.)"
    names = ", ".join(g["description"] for g in goals[:15])
    return f"Available goals: {names}"
```

### Timer operations

```python
def timer_start(description="Discord timer", project_name=None, goal_name=None):
    """Start a timer by writing directly to Firebase. Returns (success, message)."""
    state = fb_get("discord-sync/timer-state")
    if state and state.get("active"):
        desc = state.get("description", "?")
        return False, f'Timer already running: "{desc}". Stop it first.'

    project = resolve_project(project_name)
    goal = resolve_goal(goal_name, project["id"] if project else None)

    if project_name and not project:
        projects = fb_get("discord-sync/projects") or []
        available = ", ".join(p["name"] for p in projects[:10]) or "(none)"
        return False, f'Project "{project_name}" not found. Available: {available}'

    if goal_name and not goal:
        goals = fb_get("discord-sync/goals") or []
        available = ", ".join(g["description"] for g in goals[:10]) or "(none)"
        return False, f'Goal "{goal_name}" not found. Available: {available}'

    now_ms = int(time.time() * 1000)
    session_id = f"discord-{now_ms}-{os.urandom(3).hex()}"

    resolved_project_name = project["name"] if project else None
    resolved_goal_name = goal["description"] if goal else None

    fb_put("discord-sync/timer-state", {
        "active": True,
        "description": description,
        "projectName": resolved_project_name,
        "goalName": resolved_goal_name,
        "projectId": project["id"] if project else None,
        "goalId": goal["id"] if goal else None,
        "startTime": now_ms,
        "sessionId": session_id,
        "origin": "discord",
        "lastUpdated": now_ms,
    })

    msg = f'Timer started: "{description}"'
    if resolved_project_name:
        msg += f" | Project: {resolved_project_name}"
    if resolved_goal_name:
        msg += f" | Goal: {resolved_goal_name}"
    return True, msg


def timer_stop():
    """Stop the running timer and write a pending entry. Returns (success, message)."""
    state = fb_get("discord-sync/timer-state")
    if not state or not state.get("active"):
        return False, "No timer is currently running."

    now_ms = int(time.time() * 1000)
    start_ms = state["startTime"]
    duration_sec = round((now_ms - start_ms) / 1000)

    from datetime import datetime, timezone
    start_iso = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).isoformat()
    end_iso = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).isoformat()

    fb_post("discord-sync/pending-entries", {
        "description": state.get("description", "Discord timer"),
        "startTime": start_iso,
        "endTime": end_iso,
        "duration": duration_sec,
        "projectId": state.get("projectId"),
        "goalId": state.get("goalId"),
        "sessionId": state.get("sessionId", ""),
        "createdAt": now_ms,
    })

    fb_put("discord-sync/timer-state", {
        "active": False,
        "lastUpdated": now_ms,
    })

    h, remainder = divmod(duration_sec, 3600)
    m, s = divmod(remainder, 60)
    msg = f'Timer stopped: "{state.get("description", "?")}" | Duration: {h}h {m}m {s}s'
    if state.get("goalName"):
        msg += f' | Logged to goal "{state["goalName"]}"'
    return True, msg


def timer_status():
    """Read timer status directly from Firebase. Returns a message string."""
    state = fb_get("discord-sync/timer-state")
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
    return msg
```

### Async wrappers for `discord.py`

```python
import asyncio

async def timer_start_async(*args, **kwargs):
    return await asyncio.to_thread(timer_start, *args, **kwargs)

async def timer_stop_async():
    return await asyncio.to_thread(timer_stop)

async def timer_status_async():
    return await asyncio.to_thread(timer_status)

async def list_projects_async():
    return await asyncio.to_thread(list_projects)

async def list_goals_async(project_name=None):
    return await asyncio.to_thread(list_goals, project_name)
```

### Argument parsing

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

### Example command handler

Adapt to your bot's command structure, cog layout, and permissions system:

```python
@bot.command(name="timer")
async def timer_cmd(ctx, action: str = "status", *, args: str = ""):
    if not is_timer_owner(ctx):
        await ctx.send("You don't have access to timer commands.")
        return

    if action == "start":
        parsed = parse_start_args(args)
        success, msg = await timer_start_async(
            description=parsed["description"] or "Discord timer",
            project_name=parsed["project"],
            goal_name=parsed["goal"],
        )
        await ctx.send(msg)

    elif action == "stop":
        success, msg = await timer_stop_async()
        await ctx.send(msg)

    elif action == "status":
        msg = await timer_status_async()
        await ctx.send(msg)

    elif action == "projects":
        msg = await list_projects_async()
        await ctx.send(msg)

    elif action == "goals":
        project_name = None
        if args.strip():
            parsed = parse_start_args(args)
            project_name = parsed["project"]
        msg = await list_goals_async(project_name)
        await ctx.send(msg)

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

## Setup Checklist

1. Give the bot developer:
   - `FIREBASE_DATABASE_URL`
   - `FIREBASE_DATABASE_SECRET`
2. Give the developer the Discord user ID that should populate `TIMER_OWNER_ID`.
3. Run the local app at least once so projects and goals are present in Firebase.
4. Make sure the bot already has an HTTP client available, such as `requests`.

---

## Operational Notes

- All timer commands operate through Firebase, so they do not depend on the local server being online at command time.
- `!timer start` with no description should use `"Discord timer"`.
- Project and goal matching is case-insensitive substring matching. For example, `"web"` can match `"Web App"`.
- If a timer is already active, starting another timer from Discord must be rejected even if that timer was started in the browser.
- When the bot stops a timer, it writes a completed item to `pending-entries`; the local server later syncs that item into SQLite.
- If the local app has never synced projects and goals to Firebase, commands such as `!timer projects` and `!timer goals` should explain that no synced data is available yet.
- The browser UI and Discord bot share timer visibility through the same `timer-state` record.
