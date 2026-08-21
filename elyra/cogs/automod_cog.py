"""
cogs/automod_cog.py — Advanced Auto-Moderation Engine
Regex-based link/invite/spam filtering with actionable infraction modals.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import defaultdict, deque
from typing import Deque

import discord
from discord import ui
from discord.ext import commands

from cogs.config_cog import load_config

log = logging.getLogger("bot.automod")

# ─── Regex Patterns ───────────────────────────────────────────────────────────────
INVITE_RE = re.compile(
    r"(discord\.gg|discordapp\.com/invite|discord\.com/invite)/[A-Za-z0-9\-]+",
    re.IGNORECASE,
)
URL_RE = re.compile(
    r"https?://(?!cdn\.discordapp\.com|media\.discordapp\.net)[^\s]+",
    re.IGNORECASE,
)
MENTION_SPAM_RE = re.compile(r"<@[!&]?\d+>")


# ─── Infraction Modal ────────────────────────────────────────────────────────────
class InfractionModal(ui.Modal, title="Log Infraction"):
    """
    A pop-up form that moderators fill in when they take action on a flagged user.
    This replaces plain-text warning messages with a structured, auditable record.
    """

    reason = ui.TextInput(
        label="Reason for Infraction",
        style=discord.TextStyle.paragraph,
        placeholder="Describe exactly what rule was broken…",
        max_length=500,
    )
    action_taken = ui.TextInput(
        label="Action Taken",
        placeholder='e.g. "Verbal warning", "1-hour mute", "Kick"',
        max_length=100,
    )

    def __init__(self, *, offender: discord.Member, trigger: str) -> None:
        super().__init__()
        self.offender = offender
        self.trigger = trigger

    async def on_submit(self, interaction: discord.Interaction) -> None:
        cfg = load_config()
        log_channel_id: int | None = cfg.get("log_channel_id")

        embed = discord.Embed(
            title="📋  Infraction Logged",
            color=discord.Color.from_str("#FF6B35"),
        )
        embed.add_field(name="Offender", value=self.offender.mention, inline=True)
        embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
        embed.add_field(name="Trigger", value=f"`{self.trigger}`", inline=False)
        embed.add_field(name="Reason", value=self.reason.value, inline=False)
        embed.add_field(name="Action Taken", value=self.action_taken.value, inline=False)
        embed.set_thumbnail(url=self.offender.display_avatar.url)
        embed.set_footer(text=f"User ID: {self.offender.id}")

        if log_channel_id:
            channel = interaction.guild and interaction.guild.get_channel(log_channel_id)
            if isinstance(channel, discord.TextChannel):
                await channel.send(embed=embed)

        await interaction.response.send_message(
            embed=discord.Embed(
                description="✅  Infraction logged successfully.",
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )
        log.info(
            "Infraction logged: offender=%s, mod=%s, action=%s",
            self.offender,
            interaction.user,
            self.action_taken.value,
        )


# ─── Log Infraction Button ────────────────────────────────────────────────────────
class LogInfractionButton(ui.Button):
    def __init__(self, offender: discord.Member, trigger: str) -> None:
        super().__init__(
            label="Log Infraction",
            emoji="📋",
            style=discord.ButtonStyle.red,
        )
        self.offender = offender
        self.trigger = trigger

    async def callback(self, interaction: discord.Interaction) -> None:
        # Only moderators can open the modal
        if not interaction.user.guild_permissions.manage_messages:  # type: ignore[union-attr]
            await interaction.response.send_message(
                "❌ You need the **Manage Messages** permission.", ephemeral=True
            )
            return
        await interaction.response.send_modal(
            InfractionModal(offender=self.offender, trigger=self.trigger)
        )


class DismissButton(ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Dismiss", style=discord.ButtonStyle.grey)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.message.delete()  # type: ignore[union-attr]


class ModerationAlertView(ui.View):
    def __init__(self, offender: discord.Member, trigger: str) -> None:
        super().__init__(timeout=300)
        self.add_item(LogInfractionButton(offender, trigger))
        self.add_item(DismissButton())


# ─── AutoMod Cog ─────────────────────────────────────────────────────────────────
class AutoModCog(commands.Cog, name="AutoMod"):
    """
    Listens on every message, applies regex checks, and notifies moderators
    via actionable embeds with inline Infraction Modals.
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # spam tracker: user_id → deque of timestamps
        self._spam_tracker: dict[int, Deque[float]] = defaultdict(
            lambda: deque(maxlen=20)
        )

    # ─── Helpers ────────────────────────────────────────────────────────────────
    def _is_spam(self, user_id: int, threshold: int, window: int) -> bool:
        now = time.monotonic()
        q = self._spam_tracker[user_id]
        q.append(now)
        # Count messages within the window
        recent = sum(1 for t in q if now - t <= window)
        return recent >= threshold

    async def _alert(
        self,
        message: discord.Message,
        *,
        reason: str,
        trigger: str,
        delete: bool = True,
    ) -> None:
        """Delete the offending message and post a moderator alert embed."""
        if delete:
            try:
                await message.delete()
            except discord.HTTPException:
                pass

        embed = discord.Embed(
            title="⚠️  AutoMod Alert",
            description=f"{message.author.mention} triggered the auto-moderator.",
            color=discord.Color.from_str("#FF4C4C"),
        )
        embed.add_field(name="Rule Broken", value=reason, inline=False)
        embed.add_field(name="Channel", value=message.channel.mention, inline=True)
        embed.set_author(
            name=str(message.author),
            icon_url=message.author.display_avatar.url,
        )
        embed.set_footer(text="AutoMod — use the button below to log an infraction")

        view = ModerationAlertView(message.author, trigger)  # type: ignore[arg-type]
        await message.channel.send(embed=embed, view=view)

    # ─── Event Listener ─────────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if not isinstance(message.guild, discord.Guild):
            return

        cfg = load_config()
        if not cfg.get("automod_enabled", True):
            return

        # Let admins and moderators bypass
        member = message.author
        if isinstance(member, discord.Member) and member.guild_permissions.manage_messages:
            return

        sensitivity = cfg.get("automod_sensitivity", "medium")
        spam_thresholds = {"low": (8, 5), "medium": (5, 5), "high": (3, 5)}
        spam_limit, spam_window = spam_thresholds[sensitivity]

        content = message.content

        # 1. Server invite detection
        if INVITE_RE.search(content):
            await self._alert(
                message,
                reason="🚫 Server Invite Link",
                trigger="discord_invite",
            )
            return

        # 2. External URL filter (medium/high only)
        if sensitivity in ("medium", "high") and URL_RE.search(content):
            await self._alert(
                message,
                reason="🔗 Unauthorized External Link",
                trigger="external_url",
            )
            return

        # 3. Mass mention spam
        mention_count = len(MENTION_SPAM_RE.findall(content))
        if mention_count >= 5:
            await self._alert(
                message,
                reason=f"📣 Mass Mention Spam ({mention_count} mentions)",
                trigger="mass_mention",
            )
            return

        # 4. Rapid message spam
        if self._is_spam(message.author.id, spam_limit, spam_window):
            await self._alert(
                message,
                reason=f"⚡ Message Spam (>{spam_limit} msgs/{spam_window}s)",
                trigger="spam",
                delete=False,
            )
            # Optionally apply a short timeout
            if isinstance(member, discord.Member):
                try:
                    await member.timeout(
                        discord.utils.utcnow() + __import__("datetime").timedelta(minutes=1),
                        reason="AutoMod: spam",
                    )
                except discord.Forbidden:
                    pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AutoModCog(bot))