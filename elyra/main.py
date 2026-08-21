"""
main.py — Discord AI Assistant Bot Entry Point
Handles bot initialization, Cog loading, command tree sync, and global error handling.
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

TOKEN: str = os.environ["DISCORD_TOKEN"]
GUILD_ID: Optional[int] = int(os.environ.get("GUILD_ID", 0)) or None  # None = global sync

# ─── Logging ────────────────────────────────────────────────────────────────────
LOG_DIR = Path("data/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_DIR / "bot.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("bot.main")

# ─── Cog Discovery ──────────────────────────────────────────────────────────────
COGS_DIR = Path("cogs")
COG_MODULES: list[str] = [
    f"cogs.{p.stem}"
    for p in COGS_DIR.glob("*.py")
    if not p.stem.startswith("_")
]


# ─── Bot Subclass ────────────────────────────────────────────────────────────────
class AIAssistantBot(commands.Bot):
    """
    Custom Bot subclass with typed setup_hook for clean async initialization.
    Loads all Cogs dynamically and syncs the slash command tree once on startup.
    """

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.guilds = True

        super().__init__(
            command_prefix=commands.when_mentioned_or("/"),  # /warn, /mute, etc.
            intents=intents,
            help_command=None,
        )
        self.guild_id: Optional[int] = GUILD_ID
        self._synced: bool = False

    # ── Lifecycle ────────────────────────────────────────────────────────────────
    async def setup_hook(self) -> None:
        """Called once after login, before the gateway is opened."""
        # Seed server_config.json from .env on first run so channel IDs work immediately
        _seed_config_from_env()

        log.info("Loading Cogs…")
        for module in COG_MODULES:
            try:
                await self.load_extension(module)
                log.info("  ✓  %s", module)
            except Exception:
                log.exception("  ✗  Failed to load %s", module)

        # Sync slash commands to guild (instant) or globally (up to 1 h propagation)
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
        Required so prefix commands AND Cog on_message listeners both fire.
        Without this, discord.py's default handler can swallow events before
        Cogs like ai_cog and automod_cog get to see them.
        """
        if message.author.bot:
            return
        await self.process_commands(message)

    async def on_ready(self) -> None:
        log.info("━" * 50)
        log.info("Logged in as %s (ID: %s)", self.user, self.user.id)  # type: ignore[union-attr]
        log.info("discord.py v%s", discord.__version__)
        log.info("━" * 50)
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="over the server 👁",
            )
        )

    # ── Global App-Command Error Handler ─────────────────────────────────────────
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


# ─── Seed config from .env ───────────────────────────────────────────────────────
def _seed_config_from_env() -> None:
    """
    On first run (or whenever a channel ID is missing from the JSON),
    pull WELCOME_CHANNEL_ID / LOG_CHANNEL_ID / AI_CHAT_CHANNEL_ID from .env
    and write them into server_config.json so the bot can find them immediately
    without needing a /settings visit first.
    """
    import json
    from pathlib import Path

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
        ("LOG_CHANNEL_ID",     "log_channel_id"),
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


# ─── Entry Point ─────────────────────────────────────────────────────────────────
async def main() -> None:
    async with AIAssistantBot() as bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())