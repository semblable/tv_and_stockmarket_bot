# Gemini AI – Multi-Turn Conversation Plan

## 1. Objectives

* Extend the current **`/gemini`** (or `!gemini`) command from single-shot Q->A to a full conversation flow.
* Allow users to:
  1. **Start** a brand-new conversation.
  2. **Continue** an existing conversation.
  3. **Reset / end** an active conversation.
* Keep history scoped per **user** (or per *(guild, channel, user)* tuple) so different users do not share context.
* Limit memory usage by truncating or ageing out old messages.

---

## 2. Gemini SDK Capabilities

Google's Generative AI SDK exposes a *chat* abstraction:

```python
chat = model.start_chat(history=[])
reply = chat.send_message("Hello, world!")
print(reply.text)
```

The `chat` object maintains its own internal history list, so as long as we keep a reference to that object we can continue the conversation.

---

## 3. Data Structures

```python
class GeminiAI(commands.Cog):
    # ... existing code ...
    self.sessions: dict[tuple[int, int, int], genai.ChatSession] = {}
    # key = (guild_id, channel_id, user_id)
```

* `sessions` keeps one chat session per user **per channel** (adjust scoping as needed).
* Each value is a `genai.ChatSession` produced by `model.start_chat()`.
* Optional: wrap the `ChatSession` + metadata (last_activity, message_count) to implement TTL & trimming.

---

## 4. Command Surface

We have two design options:

1. **Command Group**

```text
/gemini ask      <prompt>   # continue or start automatically
/gemini new      <prompt>   # force new session
/gemini reset               # drop session
```

2. **Flags on single command**

```text
/gemini <prompt>                 # continue if exists
/gemini --new <prompt>           # start new
/gemini --reset                  # just reset
```

Option 1 is clearer for users & Discord's slash-command UX; recommended.

---

## 5. Implementation Steps

1. **Refactor Cog to Command Group**

```python
@commands.hybrid_group(name="gemini", invoke_without_command=True)
async def gemini(ctx, *, prompt: str):
    return await self.ask(ctx, prompt=prompt)
```

2. **Add Sub-Commands**

```python
@gemini.command(name="ask")
async def ask(self, ctx, *, prompt: str):
    chat = self._get_session(ctx)            # create if missing
    response = await loop.run_in_executor(None, chat.send_message, prompt)
    await ctx.send(response.text)

@gemini.command(name="new")
async def new(self, ctx, *, prompt: str):
    chat = self._get_session(ctx, reset=True) # force new
    ...

@gemini.command(name="reset")
async def reset(self, ctx):
    self._delete_session(ctx)
    await ctx.send("✅ Conversation reset.")
```

3. **Session Helpers**

```python
def _make_key(self, ctx):
    return (ctx.guild.id if ctx.guild else 0, ctx.channel.id, ctx.author.id)

def _get_session(self, ctx, reset: bool = False):
    key = self._make_key(ctx)
    if reset or key not in self.sessions:
        self.sessions[key] = self.model_primary.start_chat(history=[])
    return self.sessions[key]

def _delete_session(self, ctx):
    self.sessions.pop(self._make_key(ctx), None)
```

4. **History Management**

* Gemini's API cost grows with history length. After each `send_message`, prune:

```python
history = chat.history
max_tokens = 32_000              # model dependent
while chat.count_tokens() > 0.75 * max_tokens:
    history.pop(0)               # drop oldest turn
```

* Or cap to **N = 20** exchanges.

5. **Fallback Model**

Keep the primary / fallback logic as-is; both support `start_chat`.

6. **Error Handling & TTL**

* On SDK exceptions, try fallback or inform user.
* Optionally invalidate sessions after **30 min** idle:

```python
if time.time() - session.last_activity > 1800:
    del self.sessions[key]
```

---

## 6. Persistence (Optional)

For long-lived conversations, persist history to disk/DB. On bot restart, reload into `start_chat(history=...)`.

---

## 7. Example Flow

```text
User  → /gemini ask "Explain quantum entanglement"
Bot   → "Quantum entanglement is …"
User  → /gemini ask "Can you give an analogy?"
Bot   → "Imagine two coins…"
User  → /gemini reset
Bot   → "✅ Conversation reset."
User  → /gemini ask "New topic: What is Rust?"
Bot   → "Rust is a systems programming language…"
```

---

## 8. Future Enhancements

* **Thread Support** – spawn Discord threads for deep dives, tying one chat session per thread.
* **System / Persona Prompts** – allow `/gemini set-persona "You are Shakespeare"`.
* **Cost Tracking** – log calls & token usage.
* **Voice Integration** – speech-to-text (Whisper) + text-to-speech for voice channels.

---

## 9. Task Breakdown

| Task | Effort | Owner |
|------|--------|-------|
| Refactor Cog to command group         | 1-2 h | 🍺 |
| Implement session dict & helpers      | 1 h   | |
| Add ask/new/reset commands            | 1 h   | |
| History pruning logic                 | 0.5 h | |
| Unit tests / manual testing           | 1-2 h | |
| Documentation update                  | 0.5 h | |

---

> **Ready to implement 🚀** 