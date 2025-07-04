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

### Final Design (Implemented)

Single hybrid command with optional flags — works for both slash (`/`) and prefix (`!`) invocations.

```text
/gemini <prompt>                 # continue existing session (creates one if needed)
/gemini new:true <prompt>        # force start a new conversation
/gemini reset:true               # reset/forget conversation without sending a prompt

!gemini <prompt>                 # same via prefix
!gemini new <prompt>
!gemini reset
```

Flags are simple booleans for slash usage (`new:true`, `reset:true`). Prefix aliases accept the first word `new|reset|n|r` as shorthand.

This keeps the command surface minimal while still covering all flows.

---

## 5. Implementation Notes (Implemented)

Key points of the final implementation (`cogs/gemini.py`):

1. **Single Hybrid Command** – `@commands.hybrid_command("gemini")` with parameters `prompt`, `new`, `reset`.
2. **Flag Parsing** – slash users set `new:true` or `reset:true`; prefix users may begin with `new` or `reset` tokens.
3. **Session Dict** – `sessions: dict[SessionKey, SessionEntry]` keyed by `(guild_id, channel_id, user_id)`.
4. **History Trimming** – keep last 20 exchanges to control token cost.
5. **Fallback Model** – automatically switches to *flash* if *pro* fails.
6. **Typing / Defer Safety** – context manager ensures Discord doesn't stay in endless "thinking".

All tasks in section 9 are now ✅ complete.

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