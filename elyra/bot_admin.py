"""
bot_admin.py — Admin Bot Entry Point (Halcyon/SARPG)
Handles admin commands, automod, moderation, welcome messages.
Does NOT respond to chat messages - only handles administrative functions.
"""

from __future__ import annotations

import asyncio
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

TOKEN: str = os.environ["DISCORD_ADMIN_TOKEN"]
GUILD_ID: Optional[int] = int(os.environ.get("GUILD_ID", 0)) or None

# ─── Logging ────────────────────────────────────────────────────────────────────
LOG_DIR = Path("data/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_DIR / "admin_bot.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("bot.admin")

# ─── Cog Discovery (Admin Only) ─────────────────────────────────────────────────
COGS_DIR = Path("cogs")
ADMIN_COG_MODULES: list[str] = [
    "cogs.config_cog",
    "cogs.automod_cog",
    "cogs.mod_cog",
    "cogs.welcome_cog",
]


# ─── Bot Subclass ────────────────────────────────────────────────────────────────
class AdminBot(commands.Bot):
    """
    Admin Bot subclass for Halcyon/SARPG.
    Handles administrative functions only - does NOT respond to chat messages.
    """

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.guilds = True
        intents.moderation = True

        super().__init__(
            command_prefix=commands.when_mentioned_or("/"),
            intents=intents,
            help_command=None,
        )
        self.guild_id: Optional[int] = GUILD_ID
        self._synced: bool = False

    async def setup_hook(self) -> None:
        """Called once after login, before the gateway is opened."""
        _seed_config_from_env()

        log.info("Loading Admin Cogs…")
        for module in ADMIN_COG_MODULES:
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
        Process commands but do NOT respond to chat messages.
        This bot only handles administrative functions.
        """
        if message.author.bot:
            return
        await self.process_commands(message)

    async def on_ready(self) -> None:
        log.info("━" * 50)
        log.info("Admin Bot logged in as %s (ID: %s)", self.user, self.user.id)
        log.info("discord.py v%s", discord.__version__)
        log.info("━" * 50)
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="for rule violations 👁",
            )
        )

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


def _seed_config_from_env() -> None:
    """Seed server_config.json from .env on first run."""
    import json

    config_file = Path("data/server_config.json")
    config_file.parent.mkdir(parents=True, exist_ok=True)

    existing: dict = {}
    if config_file.exists():
        with config_file.open() as f:
            try:
                existing = json.load(f)
            except json.JSONDecodeError:
                existing = {}

    def _env_id(key: str):
        val = os.environ.get(key, "").strip()
        return int(val) if val.isdigit() else None

    changed = False
    for env_key, cfg_key in (
        ("WELCOME_CHANNEL_ID", "welcome_channel_id"),
        ("LOG_CHANNEL_ID", "log_channel_id"),
        ("AI_CHAT_CHANNEL_ID", "ai_channel_id"),
    ):
        val = _env_id(env_key)
        if val and not existing.get(cfg_key):
            existing[cfg_key] = val
            changed = True
            log.info("Seeded %s = %s from .env", cfg_key, val)

    if changed:
        with config_file.open("w") as f:
            json.dump(existing, f, indent=2)


async def main() -> None:
    async with AdminBot() as bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
