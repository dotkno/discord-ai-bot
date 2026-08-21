Elyra — Dual Discord AI Bot with RAG Knowledge Base
A two-bot Discord architecture: an Admin bot (moderation, automod, welcome)and an AI bot (Elyra) that responds to @mentions with context-aware LLMreplies powered by a four-category RAG knowledge base. Provider-agnostic —supports both Google Gemini and OpenAI.

Status: Prototype / actively developed. Core dual-bot architecture andRAG retrieval work; [note what's incomplete].

Architecture
┌──────────────────────┐ ┌──────────────────────┐
│ Admin Bot │ │ AI Bot (Elyra) │
│ bot_admin.py │ │ bot_ai.py │
│ • automod │ │ • @mention → LLM │
│ • /warn /mute /ban │ │ • RAG retrieval │
│ • welcome messages │ │ • /bot-config │
│ • Does NOT chat │ │ • Only bot that chats│
└──────────────────────┘ └──────────┬───────────┘
│
▼
┌──────────────────────┐
│ Knowledge Layer │
│ utils/knowledge.py │
│ • personal_knowledge │
│ • world_knowledge │
│ • general_knowledge │
│ • archived_knowledge │
│ (loaded by context) │
└──────────┬───────────┘
│
▼
┌──────────────────────┐
│ LLM Provider │
│ Google Gemini OR │
│ OpenAI (swappable) │
└──────────────────────┘

### Minecraft NPC integration

<!-- FILL THIS IN — this is the part reviewers will ask about -->
The AI bot also connects to a companion Minecraft Java plugin that spawns an
in-game NPC. Players can talk to the NPC in-game; the plugin forwards messages
to the bot, which calls the LLM and returns the response as NPC dialogue.

- **Companion plugin repo:** [link to arpgessentialsx or separate repo]
- **Transport between Python and Java:** [HTTP / WebSocket / Redis / other —
  state which]
- **Where the bridge code lives:** [bot_ai.py / a separate module / the plugin]

---

## Stack

- **Language:** Python 3.x
- **Framework:** discord.py 2.4+ (Cogs pattern, slash commands, app_commands)
- **LLM:** google-genai (Gemini) + openai (provider-agnostic)
- **Retrieval:** Custom RAG with 4 context-loaded knowledge categories
- **Other:** Pillow (image handling), aiohttp, python-dotenv

---

## Setup

1. Clone and enter the project:
   ```bash
   git clone https://github.com/dotkno/discord-ai-bot.git
   cd discord-ai-bot/elyra

2. Create a virtualenv and install deps:
   ```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt 

3. Copy .env.example → .env and fill in
DISCORD_ADMIN_TOKEN=your_admin_bot_token
DISCORD_AI_TOKEN=your_ai_bot_token
GUILD_ID=your_test_guild_id     # optional, for instant slash sync

4.
python launcher.py
# or in separate terminals:
python bot_admin.py
python bot_ai.py

main.py is the legacy single-bot entry and is deprecated — use
launcher.py / bot_admin.py + bot_ai.py instead.

Key design decisions
| Decision | Why |
|---|---|
| **Dual bot, not single** | Separation of concerns: moderation and chat have different permission scopes, intents, and failure modes |
| **RAG with 4 knowledge categories** | Loading only relevant context per query keeps the prompt small and responses fast |
| **Provider-agnostic LLM layer** | Can swap Gemini ↔ OpenAI without rewriting bot logic |
| **Dynamic Cog discovery** | Adding a new cog = dropping a file in `cogs/`, no registration code |
| **`when_mentioned_or("/")` prefix** | Bot responds to both @mention (natural) and slash commands (discoverable) |

Author
Ahren Tangog — renyuzaki.me · GitHub

  
