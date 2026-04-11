import pytest


def test_parse_start_args_supports_project_and_goal_any_order():
    from cogs import timer as timer_module

    parsed = timer_module.parse_start_args("Deep work goal:Ship it project:Bot")

    assert parsed == {
        "description": "Deep work",
        "project": "Bot",
        "goal": "Ship it",
    }


def test_timer_start_writes_timer_state_directly(monkeypatch):
    from cogs import timer as timer_module

    captured = {}

    monkeypatch.setattr(timer_module, "_read_timer_state", lambda: None)
    monkeypatch.setattr(timer_module, "_resolve_project", lambda name: {"id": 7, "name": "Bot"} if name == "Bot" else None)
    monkeypatch.setattr(
        timer_module,
        "_resolve_goal",
        lambda name, project_id=None: {"id": 11, "description": "Ship it"} if name == "Ship it" and project_id == 7 else None,
    )
    monkeypatch.setattr(timer_module.time, "time", lambda: 1000.0)

    def fake_put(path, data):
        captured["path"] = path
        captured["data"] = data
        return {}

    monkeypatch.setattr(timer_module, "_fb_put", fake_put)

    success, message = timer_module._timer_start("Deep work", "Bot", "Ship it")

    assert success is True
    assert captured["path"] == "discord-sync/timer-state"
    assert captured["data"]["active"] is True
    assert captured["data"]["description"] == "Deep work"
    assert captured["data"]["projectId"] == 7
    assert captured["data"]["goalId"] == 11
    assert captured["data"]["origin"] == "discord"
    assert captured["data"]["sessionId"] == "discord-1000000"
    assert 'Timer started: "Deep work"' in message
    assert "Project: Bot" in message
    assert "Goal: Ship it" in message


def test_timer_stop_writes_pending_entry_and_clears_state(monkeypatch):
    from cogs import timer as timer_module

    posts = []
    puts = []

    monkeypatch.setattr(
        timer_module,
        "_read_timer_state",
        lambda: {
            "active": True,
            "description": "Deep work",
            "projectId": 7,
            "goalId": 11,
            "goalName": "Ship it",
            "sessionId": "discord-123",
            "startTime": 1_000,
        },
    )
    monkeypatch.setattr(timer_module.time, "time", lambda: 5.0)
    monkeypatch.setattr(timer_module, "_fb_post", lambda path, data: posts.append((path, data)) or {})
    monkeypatch.setattr(timer_module, "_fb_put", lambda path, data: puts.append((path, data)) or {})

    success, message = timer_module._timer_stop()

    assert success is True
    assert posts[0][0] == "discord-sync/pending-entries"
    assert posts[0][1]["description"] == "Deep work"
    assert posts[0][1]["duration"] == 4
    assert posts[0][1]["sessionId"] == "discord-123"
    assert puts[0] == ("discord-sync/timer-state", {"active": False, "lastUpdated": 5000})
    assert 'Timer stopped: "Deep work"' in message
    assert 'Logged to goal "Ship it"' in message


@pytest.mark.asyncio
async def test_timer_start_rejects_non_owner(monkeypatch, mock_bot):
    import config
    from cogs.timer import TimerCog

    class _Author:
        id = 999

    class _Ctx:
        def __init__(self):
            self.author = _Author()
            self.guild = None
            self.interaction = None
            self.sent = []

        async def send(self, content=None, **kwargs):
            self.sent.append((content, kwargs))
            return None

    async def fake_check_configured(self, ctx):
        return True

    monkeypatch.setattr(TimerCog, "_check_configured", fake_check_configured)
    monkeypatch.setattr(config, "TIMER_OWNER_ID", 123, raising=False)

    cog = TimerCog(mock_bot)
    ctx = _Ctx()

    await cog.timer_start.callback(cog, ctx, args="Deep work")

    assert ctx.sent == [("You don't have access to timer commands.", {})]
