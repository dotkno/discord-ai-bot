"""
cogs/welcome_cog.py — Dynamic Visual Welcome Cards
Generates a personalized welcome image for each new member using Pillow.
Fetches their avatar, composites it onto a template, and posts to the welcome channel.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

import aiohttp
import discord
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from cogs.config_cog import load_config

log = logging.getLogger("bot.welcome")

# ─── Asset Paths ─────────────────────────────────────────────────────────────────
ASSETS = Path("assets")
TEMPLATE_PATH = ASSETS / "templates" / "welcome_bg.png"
FONT_PATH = ASSETS / "fonts" / "welcome_font.ttf"  # Drop in your own .ttf
FALLBACK_FONT_SIZE = 36


async def _fetch_avatar_bytes(url: str) -> bytes:
    """Download avatar as raw bytes via aiohttp (non-blocking)."""
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            return await resp.read()


def _build_welcome_image(
    avatar_bytes: bytes,
    username: str,
    discriminator: str,
    member_number: int,
) -> io.BytesIO:
    """
    Composites the welcome card synchronously (run in executor to avoid blocking).
    Returns a BytesIO PNG ready for discord.File.
    """
    # ── Background ──────────────────────────────────────────────────────────────
    if TEMPLATE_PATH.exists():
        card = Image.open(TEMPLATE_PATH).convert("RGBA").resize((900, 300))
    else:
        # Fallback gradient background
        card = Image.new("RGBA", (900, 300), color=(30, 31, 34, 255))

    draw = ImageDraw.Draw(card)

    # ── Circular Avatar ──────────────────────────────────────────────────────────
    avatar_size = 200
    avatar_img = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
    avatar_img = avatar_img.resize((avatar_size, avatar_size), Image.LANCZOS)

    # Create a circular mask
    mask = Image.new("L", (avatar_size, avatar_size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.ellipse((0, 0, avatar_size, avatar_size), fill=255)

    # Slight glow ring
    ring = Image.new("RGBA", (avatar_size + 8, avatar_size + 8), (0, 0, 0, 0))
    ring_draw = ImageDraw.Draw(ring)
    ring_draw.ellipse((0, 0, avatar_size + 8, avatar_size + 8), outline=(88, 101, 242, 200), width=4)
    card.alpha_composite(ring, dest=(46, 46))

    avatar_composite = Image.new("RGBA", (avatar_size, avatar_size), (0, 0, 0, 0))
    avatar_composite.paste(avatar_img, mask=mask)
    card.alpha_composite(avatar_composite, dest=(50, 50))

    # ── Typography ───────────────────────────────────────────────────────────────
    try:
        font_large = ImageFont.truetype(str(FONT_PATH), 42)
        font_small = ImageFont.truetype(str(FONT_PATH), 24)
        font_tiny  = ImageFont.truetype(str(FONT_PATH), 18)
    except (IOError, OSError):
        font_large = font_small = font_tiny = ImageFont.load_default()

    text_x = 290
    # Welcome line
    draw.text((text_x, 70), "WELCOME TO THE SERVER", font=font_tiny, fill=(180, 180, 200, 200))
    # Username
    draw.text((text_x, 105), username, font=font_large, fill=(255, 255, 255, 255))
    # Discriminator / tag
    if discriminator and discriminator != "0":
        draw.text((text_x, 160), f"#{discriminator}", font=font_small, fill=(150, 150, 170, 200))
    # Member count
    draw.text(
        (text_x, 220),
        f"You are member #{member_number:,}",
        font=font_small,
        fill=(100, 220, 150, 220),
    )

    # ── Export ───────────────────────────────────────────────────────────────────
    buf = io.BytesIO()
    card.convert("RGB").save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf


# ─── Welcome Cog ─────────────────────────────────────────────────────────────────
class WelcomeCog(commands.Cog, name="Welcome"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        cfg = load_config()
        if not cfg.get("welcome_enabled", True):
            return

        channel_id: int | None = cfg.get("welcome_channel_id")
        if not channel_id:
            return

        channel = member.guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        # Show typing indicator while generating the image
        async with channel.typing():
            try:
                avatar_url = member.display_avatar.replace(size=256, format="png").url
                avatar_bytes = await _fetch_avatar_bytes(avatar_url)

                # Run Pillow in a thread executor to avoid blocking the event loop
                loop = __import__("asyncio").get_event_loop()
                image_buf = await loop.run_in_executor(
                    None,
                    _build_welcome_image,
                    avatar_bytes,
                    member.display_name,
                    member.discriminator,
                    member.guild.member_count or 1,
                )

                file = discord.File(image_buf, filename="welcome.png")
                embed = discord.Embed(
                    description=(
                        f"Hey {member.mention}, welcome to **{member.guild.name}**!\n"
                        "Read the rules and enjoy your stay. 🎉"
                    ),
                    color=discord.Color.from_str("#5865F2"),
                )
                embed.set_image(url="attachment://welcome.png")
                embed.set_footer(text=f"Member #{member.guild.member_count:,}")

                await channel.send(embed=embed, file=file)
                log.info("Welcome card sent for %s", member)

            except Exception:
                log.exception("Failed to generate welcome card for %s", member)
                # Graceful fallback — plain text welcome
                await channel.send(
                    f"👋 Welcome to **{member.guild.name}**, {member.mention}! "
                    f"You're member #{member.guild.member_count:,}."
                )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WelcomeCog(bot))