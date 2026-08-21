"""
cogs/mod_cog.py — Moderation Commands
Slash-based (/warn, /mute, /kick, etc.) moderation toolkit.
All actions are logged to the configured log channel automatically.
"""

from __future__ import annotations

import datetime
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from cogs.config_cog import load_config

log = logging.getLogger("bot.mod")


# ─── Helpers ─────────────────────────────────────────────────────────────────────
async def _send_log(
    guild: discord.Guild,
    embed: discord.Embed,
) -> None:
    """Posts an embed to the configured log channel."""
    cfg = load_config()
    channel_id: Optional[int] = cfg.get("log_channel_id")
    if not channel_id:
        return
    channel = guild.get_channel(channel_id)
    if isinstance(channel, discord.TextChannel):
        await channel.send(embed=embed)


def _mod_embed(
    title: str,
    color: discord.Color,
    *,
    moderator: discord.Member,
    target: discord.Member | discord.User,
    reason: str,
    extra: Optional[str] = None,
) -> discord.Embed:
    embed = discord.Embed(title=title, color=color, timestamp=datetime.datetime.utcnow())
    embed.add_field(name="User", value=f"{target.mention} (`{target.id}`)", inline=True)
    embed.add_field(name="Moderator", value=moderator.mention, inline=True)
    embed.add_field(name="Reason", value=reason or "No reason provided.", inline=False)
    if extra:
        embed.add_field(name="Details", value=extra, inline=False)
    embed.set_thumbnail(url=target.display_avatar.url)
    return embed


# ─── Mod Cog ─────────────────────────────────────────────────────────────────────
class ModCog(commands.Cog, name="Moderation"):

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── /warn ─────────────────────────────────────────────────────────────────────
    @app_commands.command(
        name="warn",
        description="Warn a member",
    )
    @app_commands.describe(
        member="The member to warn",
        reason="The reason for the warning",
    )
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.guild_only()
    async def warn(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str = "No reason provided.",
    ) -> None:
        await interaction.response.defer()

        embed = _mod_embed(
            "⚠️  Member Warned",
            discord.Color.yellow(),
            moderator=interaction.user,  # type: ignore[arg-type]
            target=member,
            reason=reason,
        )
        await interaction.followup.send(embed=embed)
        await _send_log(interaction.guild, embed)  # type: ignore[arg-type]

        # DM the warned user
        try:
            dm_embed = discord.Embed(
                title=f"⚠️  You were warned in {interaction.guild.name}",  # type: ignore[union-attr]
                description=f"**Reason:** {reason}",
                color=discord.Color.yellow(),
            )
            await member.send(embed=dm_embed)
        except discord.Forbidden:
            pass  # User has DMs closed

        log.info("WARN: %s warned %s — %s", interaction.user, member, reason)

    # ── /mute (timeout) ───────────────────────────────────────────────────────────
    @app_commands.command(
        name="mute",
        description="Timeout a member",
    )
    @app_commands.describe(
        member="The member to timeout",
        minutes="Duration in minutes (default: 10)",
        reason="The reason for the mute",
    )
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def mute(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        minutes: int = 10,
        reason: str = "No reason provided.",
    ) -> None:
        await interaction.response.defer()

        duration = datetime.timedelta(minutes=minutes)
        until = discord.utils.utcnow() + duration

        await member.timeout(until, reason=reason)

        embed = _mod_embed(
            "🔇  Member Muted (Timeout)",
            discord.Color.orange(),
            moderator=interaction.user,  # type: ignore[arg-type]
            target=member,
            reason=reason,
            extra=f"Duration: **{minutes} minute(s)**  •  Expires: <t:{int(until.timestamp())}:R>",
        )
        await interaction.followup.send(embed=embed)
        await _send_log(interaction.guild, embed)  # type: ignore[arg-type]

        try:
            dm_embed = discord.Embed(
                title=f"🔇  You were muted in {interaction.guild.name}",  # type: ignore[union-attr]
                description=f"**Duration:** {minutes} minute(s)\n**Reason:** {reason}",
                color=discord.Color.orange(),
            )
            await member.send(embed=dm_embed)
        except discord.Forbidden:
            pass

        log.info("MUTE: %s muted %s for %dm — %s", interaction.user, member, minutes, reason)

    # ── /unmute ───────────────────────────────────────────────────────────────────
    @app_commands.command(
        name="unmute",
        description="Remove a member's timeout",
    )
    @app_commands.describe(
        member="The member to unmute",
        reason="The reason for unmuting",
    )
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def unmute(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str = "Timeout removed by moderator.",
    ) -> None:
        await interaction.response.defer()

        await member.timeout(None, reason=reason)

        embed = _mod_embed(
            "🔊  Member Unmuted",
            discord.Color.green(),
            moderator=interaction.user,  # type: ignore[arg-type]
            target=member,
            reason=reason,
        )
        await interaction.followup.send(embed=embed)
        await _send_log(interaction.guild, embed)  # type: ignore[arg-type]
        log.info("UNMUTE: %s unmuted %s", interaction.user, member)

    # ── /kick ─────────────────────────────────────────────────────────────────────
    @app_commands.command(
        name="kick",
        description="Kick a member",
    )
    @app_commands.describe(
        member="The member to kick",
        reason="The reason for the kick",
    )
    @app_commands.default_permissions(kick_members=True)
    @app_commands.guild_only()
    async def kick(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str = "No reason provided.",
    ) -> None:
        await interaction.response.defer()

        try:
            dm_embed = discord.Embed(
                title=f"👢  You were kicked from {interaction.guild.name}",  # type: ignore[union-attr]
                description=f"**Reason:** {reason}",
                color=discord.Color.red(),
            )
            await member.send(embed=dm_embed)
        except discord.Forbidden:
            pass

        await member.kick(reason=reason)

        embed = _mod_embed(
            "👢  Member Kicked",
            discord.Color.red(),
            moderator=interaction.user,  # type: ignore[arg-type]
            target=member,
            reason=reason,
        )
        await interaction.followup.send(embed=embed)
        await _send_log(interaction.guild, embed)  # type: ignore[arg-type]
        log.info("KICK: %s kicked %s — %s", interaction.user, member, reason)

    # ── /ban ──────────────────────────────────────────────────────────────────────
    @app_commands.command(
        name="ban",
        description="Ban a member",
    )
    @app_commands.describe(
        member="The member to ban",
        reason="The reason for the ban",
    )
    @app_commands.default_permissions(ban_members=True)
    @app_commands.guild_only()
    async def ban(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str = "No reason provided.",
    ) -> None:
        await interaction.response.defer()

        try:
            dm_embed = discord.Embed(
                title=f"🔨  You were banned from {interaction.guild.name}",  # type: ignore[union-attr]
                description=f"**Reason:** {reason}",
                color=discord.Color.dark_red(),
            )
            await member.send(embed=dm_embed)
        except discord.Forbidden:
            pass

        await member.ban(reason=reason, delete_message_days=0)

        embed = _mod_embed(
            "🔨  Member Banned",
            discord.Color.dark_red(),
            moderator=interaction.user,  # type: ignore[arg-type]
            target=member,
            reason=reason,
        )
        await interaction.followup.send(embed=embed)
        await _send_log(interaction.guild, embed)  # type: ignore[arg-type]
        log.info("BAN: %s banned %s — %s", interaction.user, member, reason)

    # ── /unban ────────────────────────────────────────────────────────────────────
    @app_commands.command(
        name="unban",
        description="Unban a user by ID",
    )
    @app_commands.describe(
        user_id="The ID of the user to unban",
        reason="The reason for unbanning",
    )
    @app_commands.default_permissions(ban_members=True)
    @app_commands.guild_only()
    async def unban(
        self,
        interaction: discord.Interaction,
        user_id: str,
        reason: str = "No reason provided.",
    ) -> None:
        await interaction.response.defer()

        try:
            user_id_int = int(user_id)
            user = await self.bot.fetch_user(user_id_int)
        except (ValueError, discord.NotFound):
            await interaction.followup.send("❌ No user found with that ID.", ephemeral=True)
            return

        try:
            await interaction.guild.unban(user, reason=reason)  # type: ignore[union-attr]
        except discord.NotFound:
            await interaction.followup.send("❌ That user is not currently banned.", ephemeral=True)
            return

        embed = discord.Embed(
            title="✅  Member Unbanned",
            color=discord.Color.green(),
            timestamp=datetime.datetime.utcnow(),
        )
        embed.add_field(name="User", value=f"{user.mention} (`{user.id}`)", inline=True)
        embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.set_thumbnail(url=user.display_avatar.url)

        await interaction.followup.send(embed=embed)
        await _send_log(interaction.guild, embed)  # type: ignore[arg-type]
        log.info("UNBAN: %s unbanned %s — %s", interaction.user, user, reason)

    # ── /purge ────────────────────────────────────────────────────────────────────
    @app_commands.command(
        name="purge",
        description="Delete messages in bulk",
    )
    @app_commands.describe(
        amount="Number of messages to delete (1-100)",
        member="Only delete messages from this user (optional)",
    )
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.guild_only()
    async def purge(
        self,
        interaction: discord.Interaction,
        amount: int,
        member: Optional[discord.Member] = None,
    ) -> None:
        if amount < 1 or amount > 100:
            await interaction.response.send_message("❌ Amount must be between 1 and 100.", ephemeral=True)
            return

        await interaction.response.defer()

        def check(m: discord.Message) -> bool:
            return member is None or m.author == member

        deleted = await interaction.channel.purge(limit=amount, check=check)  # type: ignore[union-attr]

        confirm = await interaction.followup.send(
            embed=discord.Embed(
                description=f"🗑️  Deleted **{len(deleted)}** message(s)"
                            + (f" from {member.mention}" if member else "")
                            + ".",
                color=discord.Color.blurple(),
            )
        )
        # Auto-delete the confirmation after 5 seconds
        await confirm.delete(delay=5)

        log_embed = discord.Embed(
            title="🗑️  Bulk Delete",
            color=discord.Color.blurple(),
            timestamp=datetime.datetime.utcnow(),
        )
        log_embed.add_field(name="Channel", value=interaction.channel.mention, inline=True)  # type: ignore[union-attr]
        log_embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
        log_embed.add_field(name="Messages Deleted", value=str(len(deleted)), inline=True)
        if member:
            log_embed.add_field(name="Filtered to", value=member.mention, inline=True)
        await _send_log(interaction.guild, log_embed)  # type: ignore[arg-type]
        log.info("PURGE: %s deleted %d messages in #%s", interaction.user, len(deleted), interaction.channel)

    # ── /userinfo ─────────────────────────────────────────────────────────────────
    @app_commands.command(
        name="userinfo",
        description="Show information about a user",
    )
    @app_commands.describe(
        member="The user to show info for (defaults to you)",
    )
    @app_commands.guild_only()
    async def userinfo(
        self,
        interaction: discord.Interaction,
        member: Optional[discord.Member] = None,
    ) -> None:
        member = member or interaction.user  # type: ignore[assignment]

        roles = [r.mention for r in reversed(member.roles) if r.name != "@everyone"]
        joined_at = member.joined_at
        created_at = member.created_at

        embed = discord.Embed(
            title=f"👤  {member}",
            color=member.color if member.color.value else discord.Color.blurple(),
            timestamp=datetime.datetime.utcnow(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="ID", value=str(member.id), inline=True)
        embed.add_field(name="Nickname", value=member.nick or "None", inline=True)
        embed.add_field(name="Bot?", value="Yes" if member.bot else "No", inline=True)
        embed.add_field(
            name="Account Created",
            value=f"<t:{int(created_at.timestamp())}:D> (<t:{int(created_at.timestamp())}:R>)",
            inline=False,
        )
        embed.add_field(
            name="Joined Server",
            value=f"<t:{int(joined_at.timestamp())}:D> (<t:{int(joined_at.timestamp())}:R>)" if joined_at else "Unknown",
            inline=False,
        )
        embed.add_field(
            name=f"Roles [{len(roles)}]",
            value=" ".join(roles[:10]) + (" …" if len(roles) > 10 else "") if roles else "None",
            inline=False,
        )
        if member.is_timed_out():
            embed.add_field(
                name="⏳ Timed Out Until",
                value=f"<t:{int(member.timed_out_until.timestamp())}:R>",  # type: ignore[union-attr]
                inline=False,
            )

        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ModCog(bot))