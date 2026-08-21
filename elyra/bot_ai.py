"""
bot_ai.py — AI Chat Bot Entry Point (Elyra)
Handles AI chat functionality with configurable status, bio, and settings.
Only this bot responds to chat messages.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import traceback
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

# ─── Environment ────────────────────────────────────────────────────────────────
load_dotenv()

TOKEN: str = os.environ["DISCORD_AI_TOKEN"]
GUILD_ID: Optional[int] = int(os.environ.get("GUILD_ID", 0)) or None

# ─── Logging ────────────────────────────────────────────────────────────────────
LOG_DIR = Path("data/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_DIR / "ai_bot.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("bot.ai")


# ─── AI Bot Configuration ───────────────────────────────────────────────────────
class AIBotConfig:
    """Configurable settings for the AI bot."""
    
    def __init__(self) -> None:
        self.config_file = Path("data/ai_bot_config.json")
        self.config = self._load_config()
    
    def _load_config(self) -> dict:
        """Load AI bot configuration from JSON file."""
        default_config = {
            "presence": "online",
            "status": {
                "type": "watching",
                "text": "over the archives 📜"
            },
            "bio": "Guild Scribe of the Adventurers' Guild. Maintaining accurate records for future generations.",
            "settings": {
                "ai_enabled": True,
                "context_window": 20,
                "max_tokens": 600,
                "temperature": 0.7
            }
        }
        
        if self.config_file.exists():
            try:
                with self.config_file.open(encoding="utf-8") as f:
                    loaded = json.load(f)
                # Merge with defaults to ensure all keys exist
                return {**default_config, **loaded}
            except (json.JSONDecodeError, IOError) as e:
                log.error("Failed to load ai_bot_config.json: %s. Using defaults.", e)
                return default_config
        else:
            # Create default config file
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            with self.config_file.open("w", encoding="utf-8") as f:
                json.dump(default_config, f, indent=2)
            log.info("Created default ai_bot_config.json")
            return default_config
    
    def reload(self) -> None:
        """Reload configuration from file."""
        self.config = self._load_config()
    
    def get_presence(self) -> str:
        """Get presence status."""
        return self.config.get("presence", "online")
    
    def get_status(self) -> tuple[str, str]:
        """Get status type and text."""
        status = self.config.get("status", {})
        return status.get("type", "watching"), status.get("text", "over the archives 📜")
    
    def get_bio(self) -> str:
        """Get bot bio."""
        return self.config.get("bio", "Guild Scribe of the Adventurers' Guild.")
    
    def get_setting(self, key: str, default=None):
        """Get a specific setting."""
        return self.config.get("settings", {}).get(key, default)
    
    def update_presence(self, presence: str) -> bool:
        """Update presence status."""
        self.config["presence"] = presence
        return self._save_config()
    
    def update_status(self, status_type: str, text: str) -> bool:
        """Update status configuration."""
        if "status" not in self.config:
            self.config["status"] = {}
        self.config["status"]["type"] = status_type
        self.config["status"]["text"] = text
        return self._save_config()
    
    def update_bio(self, bio: str) -> bool:
        """Update bot bio."""
        self.config["bio"] = bio
        return self._save_config()
    
    def update_setting(self, key: str, value) -> bool:
        """Update a specific setting."""
        if "settings" not in self.config:
            self.config["settings"] = {}
        self.config["settings"][key] = value
        return self._save_config()
    
    def _save_config(self) -> bool:
        """Save configuration to file."""
        try:
            with self.config_file.open("w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2)
            return True
        except IOError as e:
            log.error("Failed to save ai_bot_config.json: %s", e)
            return False


# ─── Cog Discovery (AI Only) ───────────────────────────────────────────────────
COGS_DIR = Path("cogs")
AI_COG_MODULES: list[str] = [
    "cogs.ai_cog",
]


# ─── Bot Subclass ────────────────────────────────────────────────────────────────
class AIBot(commands.Bot):
    """
    AI Chat Bot subclass for Elyra.
    Handles AI chat functionality only.
    """

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.guilds = True

        super().__init__(
            command_prefix=commands.when_mentioned_or("/"),
            intents=intents,
            help_command=None,
        )
        self.guild_id: Optional[int] = GUILD_ID
        self._synced: bool = False
        self.bot_config = AIBotConfig()

    async def setup_hook(self) -> None:
        """Called once after login, before the gateway is opened."""
        log.info("Loading AI Cogs…")
        for module in AI_COG_MODULES:
            try:
                await self.load_extension(module)
                log.info("  ✓  %s", module)
            except Exception:
                log.exception("  ✗  Failed to load %s", module)

        # Sync slash commands
        target: Optional[discord.Object] = (
            discord.Object(id=self.guild_id) if self.guild_id else None
        )
        if target:
            self.tree.copy_global_to(guild=target)
            synced = await self.tree.sync(guild=target)
        else:
            synced = await self.tree.sync()

        log.info("Synced %d slash command(s) [guild=%s]", len(synced), self.guild_id)

    async def on_message(self, message: discord.Message) -> None:
        """
        Process commands - AI cog handles chat responses.
        """
        if message.author.bot:
            return
        await self.process_commands(message)

    async def on_ready(self) -> None:
        log.info("━" * 50)
        log.info("AI Bot logged in as %s (ID: %s)", self.user, self.user.id)
        log.info("discord.py v%s", discord.__version__)
        log.info("━" * 50)
        
        # Apply configured status and presence
        presence = self.bot_config.get_presence()
        status_type, status_text = self.bot_config.get_status()
        activity_type = getattr(discord.ActivityType, status_type, discord.ActivityType.watching)
        presence_enum = getattr(discord.Status, presence, discord.Status.online)
        await self.change_presence(
            status=presence_enum,
            activity=discord.Activity(
                type=activity_type,
                name=status_text,
            )
        )
        
        # Set bio if supported (note: Discord doesn't have a native bio field for bots)
        # This is stored in config for reference and can be used in responses
        log.info("Bot bio: %s", self.bot_config.get_bio())

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        original = getattr(error, "original", error)

        if isinstance(original, app_commands.MissingPermissions):
            title, desc = "🔒 Permission Denied", "You lack the required permissions."
        elif isinstance(original, app_commands.BotMissingPermissions):
            title, desc = "🤖 Bot Missing Permissions", str(original)
        elif isinstance(original, app_commands.CommandOnCooldown):
            title, desc = (
                "⏳ Slow Down",
                f"This command is on cooldown. Retry in **{original.retry_after:.1f}s**.",
            )
        elif isinstance(original, app_commands.NoPrivateMessage):
            title, desc = "🚫 Server Only", "This command cannot be used in DMs."
        else:
            title, desc = "⚠️ Unexpected Error", "An internal error occurred. It has been logged."
            log.error("Unhandled app command error:\n%s", traceback.format_exc())

        embed = discord.Embed(
            title=title,
            description=desc,
            color=discord.Color.from_str("#FF4C4C"),
        )
        embed.set_footer(text="If this persists, contact an administrator.")

        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except discord.HTTPException:
            pass


async def main() -> None:
    async with AIBot() as bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
