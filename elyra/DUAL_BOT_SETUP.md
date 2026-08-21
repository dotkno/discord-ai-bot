# Dual Bot Setup Guide

This project now uses a dual-bot architecture for better separation of concerns and scalability.

## Architecture

### Admin Bot (Halcyon/SARPG)
- **Entry Point**: `bot_admin.py`
- **Token**: `DISCORD_ADMIN_TOKEN` in `.env`
- **Purpose**: Handles administrative functions only
- **Features**:
  - Auto-moderation
  - Moderation commands (warn, mute, ban, etc.)
  - Welcome messages
  - Server configuration
  - **Does NOT respond to chat messages**

### AI Bot (Elyra)
- **Entry Point**: `bot_ai.py`
- **Token**: `DISCORD_AI_TOKEN` in `.env`
- **Purpose**: Handles AI chat functionality
- **Features**:
  - AI chat responses with RAG knowledge base
  - Configurable status, bio, and settings
  - Context-aware knowledge loading
  - **Only bot that responds to chat messages**

## Setup Instructions

### 1. Create Two Discord Applications

You need two separate bot applications in the Discord Developer Portal:

1. **Admin Bot** (Halcyon/SARPG)
   - Create a new bot application
   - Enable required intents:
     - Message Content Intent
     - Server Members Intent
     - Auto Moderation Intent
   - Invite with permissions: Administrator or appropriate mod permissions
   - Copy the token to `DISCORD_ADMIN_TOKEN` in `.env`

2. **AI Bot** (Elyra)
   - Create a new bot application
   - Enable required intents:
     - Message Content Intent
     - Server Members Intent
   - Invite with permissions: Read Messages, Send Messages, Read Message History
   - Copy the token to `DISCORD_AI_TOKEN` in `.env`

### 2. Update .env File

Update your `.env` file with both bot tokens:

```env
# Admin Bot Token (Halcyon/SARPG)
DISCORD_ADMIN_TOKEN=your_admin_bot_token_here

# AI Bot Token (Elyra)
DISCORD_AI_TOKEN=your_ai_bot_token_here

# Legacy token (can be removed after migration)
DISCORD_TOKEN=your_legacy_token_here
```

### 3. Run Both Bots

You need to run both bots simultaneously. Choose one of the following methods:

#### Method 1: Separate Terminal Windows (Recommended for Development)

```bash
# Terminal 1 - Admin Bot
python bot_admin.py

# Terminal 2 - AI Bot
python bot_ai.py
```

#### Method 2: Background Processes (Linux/Mac)

```bash
python bot_admin.py &
python bot_ai.py &
```

#### Method 3: PowerShell Background Jobs (Windows)

```powershell
Start-Process python -ArgumentList "bot_admin.py"
Start-Process python -ArgumentList "bot_ai.py"
```

#### Method 4: Process Manager (Production)

Use a process manager like PM2, systemd, or Docker Compose to manage both bots.

## AI Bot Configuration

The AI bot has configurable settings stored in `data/ai_bot_config.json`. You can modify these via Discord commands:

### Commands

- `/bot-status <type> <text>` - Set bot status activity
  - Types: playing, watching, listening, competing
  - Example: `/bot-status watching "over the archives 📜"`

- `/bot-bio <bio>` - Set bot bio/description
  - Example: `/bot-bio "Guild Scribe of the Adventurers' Guild"`

- `/bot-config` - View current configuration
- `/bot-config <setting> <value>` - Update a specific setting
  - Settings: ai_enabled, context_window, max_tokens, temperature
  - Example: `/bot-config temperature 0.8`

### Configuration File

The configuration is stored in `data/ai_bot_config.json`:

```json
{
  "status": {
    "type": "watching",
    "text": "over the archives 📜"
  },
  "bio": "Guild Scribe of the Adventurers' Guild. Maintaining accurate records for future generations.",
  "settings": {
    "ai_enabled": true,
    "context_window": 20,
    "max_tokens": 600,
    "temperature": 0.7
  }
}
```

## Knowledge Base

The knowledge base is now split into four modular JSON files:

- `data/personal_knowledge.json` - Scribe's private thoughts, inventory, sensory data
- `data/world_knowledge.json` - Active guild rosters, current maps, active quests, living NPCs
- `data/general_knowledge.json` - Static world rules, magic mechanics, standard currencies, common lore
- `data/archived_knowledge.json` - Completed historical quests, dead NPCs, ancient blueprints

The AI bot automatically loads relevant categories based on query context to optimize performance.

## Migration from Single Bot

If you're migrating from the single bot setup:

1. Run the migration script to split your knowledge base:
   ```bash
   python migrate_knowledge.py --backup
   ```

2. Create your second bot application in Discord Developer Portal

3. Update `.env` with both tokens

4. Start both bots using the methods above

5. Test that admin commands work on the admin bot
6. Test that AI chat works on the AI bot

## Troubleshooting

### Admin Bot Not Responding
- Ensure you're using admin commands, not chat messages
- Admin bot does NOT respond to regular chat messages
- Check that `DISCORD_ADMIN_TOKEN` is correct

### AI Bot Not Responding
- Ensure you're mentioning the AI bot or in the AI channel
- Check that `DISCORD_AI_TOKEN` is correct
- Verify AI is enabled: `/bot-config ai_enabled true`

### Both Bots Using Same Token
- Make sure you have two different bot applications
- Each bot must have its own unique token
- Check `.env` for duplicate tokens

### Commands Not Syncing
- Wait up to 1 hour for global command sync
- Use `GUILD_ID` in `.env` for instant guild sync
- Restart the bot after syncing

## Benefits of Dual-Bot Architecture

1. **Separation of Concerns**: Admin functions and AI chat are completely separate
2. **Scalability**: Each bot can be scaled independently
3. **Reliability**: If AI bot crashes, admin bot stays online
4. **Rate Limits**: Separate rate limits per bot
5. **Cleaner Code**: Easier to maintain and debug
6. **Flexibility**: Can add more specialized bots in the future

## File Structure

```
discord-ai-bot/
├── bot_admin.py              # Admin bot entry point
├── bot_ai.py                 # AI bot entry point
├── main.py                   # Legacy single bot (can be removed)
├── cogs/
│   ├── ai_cog.py            # AI chat functionality
│   ├── automod_cog.py       # Auto-moderation
│   ├── mod_cog.py           # Moderation commands
│   ├── welcome_cog.py       # Welcome messages
│   └── config_cog.py        # Server configuration
├── data/
│   ├── personal_knowledge.json
│   ├── world_knowledge.json
│   ├── general_knowledge.json
│   ├── archived_knowledge.json
│   ├── ai_bot_config.json   # AI bot configuration
│   └── server_config.json   # Server configuration
└── utils/
    └── knowledge.py         # Knowledge base management
```
