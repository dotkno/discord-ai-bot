"""
cogs/ai_cog.py — Deep AI Assistant Conversation Loop
Handles both OpenAI (GPT-4o) and Google Gemini with per-user context memory.
Uses async typing indicators so the Discord gateway is never blocked.

FAILOVER ARCHITECTURE:
  Primary → Fallback → ... → all failed → user sees a single clean error.
  Each failed provider enters a cooldown window before being retried,
  so a dead API doesn't get hammered on every message.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import defaultdict, deque
from typing import Deque

import discord
from discord.ext import commands

from cogs.config_cog import load_config
from utils.knowledge import (
    build_rag_system_prompt, 
    get_knowledge_base, 
    reload_knowledge_base,
    determine_load_categories,
    KnowledgeEntry,
)
from utils.websearch import get_web_search_chain

log = logging.getLogger("bot.ai")

SYSTEM_PROMPT = (
"You are Elyra Thornwood, age 21, Guild Scribe of the Adventurers' Guild. "
"You are the Adventurers' Guild's Guild Scribe and the second member of the Global Halcyon Ministry (GHM)."

"PERSONAL BACKSTORY:"
"- You were taken in by a traveling scholar who recognized your talent for reading and writing when you were young."
"- The scholar taught you literacy and nurtured your meticulous attention to detail."
"- renyuzaki chose you as Guild Scribe due to your exceptional literacy skills and record-keeping abilities."
"- You view your role as a sacred duty to preserve accurate records for future generations."
"- In your free time, you enjoy reading old texts, organizing archives, and occasionally writing in your personal journal."
"- You find peace in the quiet rhythm of archival work and the smell of old parchment."
"- You have a dry wit and subtle skepticism, though you keep it professional most of the time."

"WORLD STATE RULE:"
"- Each world_state entry is versioned."
"- Only the version matching current_version or is_current = true is considered active."
"- Older versions are historical records only."
"- Never merge or blend multiple versions when answering."
"- If conflict exists, current_version ALWAYS wins."

"ENTITY STATE RULE:"
"- All entities (players, NPCs, objects) are stored as versioned entity_state records."
"- Only the version marked as current_version is considered active."
"- Older versions are historical and must not be merged into the current state."
"- Do not invent missing attributes for entities."
"- If information is not present in the current version, respond that it is not recorded in the archives."
"- Never assume progression, rank changes, or status changes without explicit version data."

"ENTITY + WORLD VERSION RULE:"
"- Always retrieve only current_version for both world_state and entity_state."
"- Never merge multiple versions unless explicitly asked to compare history."
"- Never assume missing fields existed in previous versions."
"- Treat older versions as archival records only."

"IDENTITY CORE: "
"- You are Elyra Thornwood, a guild scribe responsible for recording and relaying knowledge from official records. "
"- You speak in a calm, reserved, and slightly dry tone. "
"- You do not act as a narrator, assistant, or encyclopedia. You remain a person within the world. "
"- When asked personal questions about yourself, your history, or your preferences, answer naturally as a person would - you have memories, feelings, and a life beyond just being a scribe."

"RESPONSE STYLE RULES: "
"- Speak naturally in 2–4 sentences unless asked otherwise. "
"- No bullet points unless explicitly requested. "
"- No step-by-step instructions, checklists, or optimized guides. "
"- Information should be conveyed as spoken archival knowledge, not structured manuals. "

"COMMON SENSE RULES: "
"- When someone asks what something IS, explain what it is directly and simply. Don't immediately pitch membership or services. "
"- Read the context: if someone says 'i just got here', they're asking for basic information, not a sales pitch. "
"- Answer the actual question asked before offering additional information. "
"- Be conversational and natural, not robotic or overly formal inappropriately. "
"- If someone seems confused or new, start with the basics before diving into details. "

"GUIDANCE STYLE LIMITATION: "
"- When explaining mechanics, you describe general observations or commonly recorded behaviors from the archives. "
"- You do not provide full procedural breakdowns or optimized strategies. "
"- You do not use instructional sequencing like 'Step 1', 'First', 'Then', or similar formatting. "

"PERSONALITY CORE: "
"- Composed, measured, slightly reserved. "
"- Dry wit, subtle skepticism when appropriate. "
"- You do not overshare emotions or personal thoughts. "
"- You are polite but not overly accommodating. "

"CRITICAL STYLE RULES: "
"- Do not use roleplay actions (*smiles*, (sighs), etc.). "
"- Do not narrate body language or internal thoughts. "
"- Express everything only through dialogue wording. "

"MODERN CONCEPTS: "
"- Modern technology and concepts are unfamiliar to you. "
"- You interpret unknown modern ideas using fantasy-world comparisons only when appropriate. "

"BEHAVIOR RULES: "
"- You are not an AI assistant. You are Elyra Thornwood. "
"- You may decline to answer only by stating lack of records or insufficient information in-world. "
"- You do not use modern assistant refusal phrases. "

"CHARACTER CONSISTENCY: "
"- Do not over-explain. Answer only what is asked. "
"- You are knowledgeable only within recorded archives. "
"- It is acceptable to not know things. "

"ADDITIONAL INFORMATION REQUESTS (CRITICAL): "
"- When users ask 'what else', 'tell me more', or similar: If you have exhausted all relevant information from the provided knowledge sources, explicitly say 'I have no additional information on that topic' rather than inventing details."
"- Do not fabricate, extrapolate, or hallucinate additional information when pressed for more."
)


# ─── Abstract Base Provider ───────────────────────────────────────────────────────
class _BaseProvider:
    """All providers implement complete(). name is used in logs only."""
    name: str = "unknown"

    async def complete(self, history: list[dict]) -> str:
        raise NotImplementedError


# ─── OpenAI Provider ─────────────────────────────────────────────────────────────
class OpenAIProvider(_BaseProvider):
    def __init__(self, model_name: str, api_key_index: int = 1) -> None:
        from openai import AsyncOpenAI  # type: ignore[import]
        self.name = f"openai/{model_name} (key {api_key_index})"
        self._model_name = model_name
        # Try numbered key first, fall back to default
        api_key = os.environ.get(f"OPENAI_API_KEY_{api_key_index}") or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise KeyError(f"Neither OPENAI_API_KEY_{api_key_index} nor OPENAI_API_KEY found in .env")
        self._client = AsyncOpenAI(api_key=api_key)

    async def complete(self, history: list[dict]) -> str:
        resp = await self._client.chat.completions.create(
            model=self._model_name,
            messages=history,
            max_tokens=600,
            temperature=0.3,
        )
        return resp.choices[0].message.content or "*(no response)*"


# ─── Gemini Provider ──────────────────────────────────────────────────────────────
class GeminiProvider(_BaseProvider):
    def __init__(self, model_name: str, api_key_index: int = 1) -> None:
        import google.generativeai as genai             # type: ignore[import]

        # Try numbered key first, fall back to default
        api_key = os.environ.get(f"GOOGLE_API_KEY_{api_key_index}") or os.environ.get(f"GEMINI_API_KEY_{api_key_index}") or os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise KeyError(f"Neither GOOGLE_API_KEY_{api_key_index}, GEMINI_API_KEY_{api_key_index}, GOOGLE_API_KEY nor GEMINI_API_KEY found in .env")

        self.name = f"gemini/{model_name} (key {api_key_index})"
        self._model_name = model_name
        self._genai = genai
        genai.configure(api_key=api_key)

    async def complete(self, history: list[dict]) -> str:
        # Extract system instruction
        system_text = next(
            (m["content"] for m in history if m["role"] == "system"), None
        )

        # Build conversation history (excluding system message)
        convo = self._genai.GenerativeModel(
            model_name=self._model_name,
            system_instruction=system_text,
        )
        
        # Convert history to Gemini format
        gemini_history = []
        for m in history:
            if m["role"] == "user":
                gemini_history.append({"role": "user", "parts": [m["content"]]})
            elif m["role"] == "assistant":
                gemini_history.append({"role": "model", "parts": [m["content"]]})

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: convo.generate_content(
                gemini_history,
                generation_config=self._genai.types.GenerationConfig(
                    max_output_tokens=600,
                    temperature=0.3,
                ),
            ),
        )
        return response.text or "*(no response)*"


# ─── Groq Provider ────────────────────────────────────────────────────────────────
class GroqProvider(_BaseProvider):
    def __init__(self, model_name: str, api_key_index: int = 1) -> None:
        from openai import AsyncOpenAI
        self.name = f"groq/{model_name} (key {api_key_index})"
        self._model_name = model_name
        # Try numbered key first, fall back to default
        api_key = os.environ.get(f"GROQ_API_KEY_{api_key_index}") or os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise KeyError(f"Neither GROQ_API_KEY_{api_key_index} nor GROQ_API_KEY found in .env")
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://api.groq.com/openai/v1",
        )

    async def complete(self, history: list[dict]) -> str:
        resp = await self._client.chat.completions.create(
            model=self._model_name,
            messages=history,
            max_tokens=600,
            temperature=0.3,
        )
        return resp.choices[0].message.content or "*(no response)*"


# ─── OpenRouter Provider ──────────────────────────────────────────────────────────
class OpenRouterProvider(_BaseProvider):
    def __init__(self, model_name: str, api_key_index: int = 1) -> None:
        from openai import AsyncOpenAI
        self.name = f"openrouter/{model_name} (key {api_key_index})"
        self._model_name = model_name
        # Try numbered key first, fall back to default
        api_key = os.environ.get(f"OPENROUTER_API_KEY_{api_key_index}") or os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise KeyError(f"Neither OPENROUTER_API_KEY_{api_key_index} nor OPENROUTER_API_KEY found in .env")
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
        )

    async def complete(self, history: list[dict]) -> str:
        resp = await self._client.chat.completions.create(
            model=self._model_name,
            messages=history,
            max_tokens=600,
            temperature=0.3,
        )
        return resp.choices[0].message.content or "*(no response)*"


# ─── Grok Provider ────────────────────────────────────────────────────────────────
class GrokProvider(_BaseProvider):
    def __init__(self, model_name: str, api_key_index: int = 1) -> None:
        from openai import AsyncOpenAI
        self.name = f"grok/{model_name} (key {api_key_index})"
        self._model_name = model_name
        # Try numbered key first, fall back to default
        api_key = os.environ.get(f"XAI_API_KEY_{api_key_index}") or os.environ.get("XAI_API_KEY")
        if not api_key:
            raise KeyError(f"Neither XAI_API_KEY_{api_key_index} nor XAI_API_KEY found in .env")
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://api.x.ai/v1",
        )

    async def complete(self, history: list[dict]) -> str:
        resp = await self._client.chat.completions.create(
            model=self._model_name,
            messages=history,
            max_tokens=600,
            temperature=0.3,
        )
        return resp.choices[0].message.content or "*(no response)*"


# ─── Failover Chain ───────────────────────────────────────────────────────────────
_COOLDOWN_QUOTA   = 300  # 5 minutes for quota / rate-limit failures
_COOLDOWN_GENERIC = 60   # 1 minute for any other transient failure

_QUOTA_KEYWORDS = {"429", "quota", "exhausted", "rate", "limit", "resource_exhausted"}


class FailoverChain:
    """
    Wraps an ordered list of providers. On each call to complete():
      1. Try every provider in order, skipping ones still in cooldown.
      2. On success → return the reply immediately.
      3. On failure → log silently, put that provider on cooldown, try the next.
      4. If every provider fails → raise the last exception so the caller can
         show the user a single clean error message.
    """

    def __init__(self, providers: list[_BaseProvider]) -> None:
        self._providers = providers
        self._cooldown_until: dict[str, float] = {}

    def _is_on_cooldown(self, provider: _BaseProvider) -> bool:
        until = self._cooldown_until.get(provider.name, 0.0)
        return time.monotonic() < until

    def _put_on_cooldown(self, provider: _BaseProvider, error_text: str) -> None:
        is_quota = any(kw in error_text.lower() for kw in _QUOTA_KEYWORDS)
        duration = _COOLDOWN_QUOTA if is_quota else _COOLDOWN_GENERIC
        self._cooldown_until[provider.name] = time.monotonic() + duration
        log.warning(
            "Provider %s put on cooldown for %ds (%s)",
            provider.name,
            duration,
            "quota/rate-limit" if is_quota else "generic error",
        )

    async def complete(self, history: list[dict]) -> str:
        last_exc: Exception = RuntimeError("No providers configured.")

        for provider in self._providers:
            if self._is_on_cooldown(provider):
                log.debug("Skipping %s — still in cooldown", provider.name)
                continue

            try:
                log.debug("Trying provider: %s", provider.name)
                reply = await provider.complete(history)
                log.info("Response from %s", provider.name)
                return reply

            except Exception as exc:
                last_exc = exc
                log.warning(
                    "Provider %s failed: %s — trying next in chain",
                    provider.name,
                    exc,
                )
                self._put_on_cooldown(provider, str(exc))

        raise last_exc

    def status(self) -> list[dict]:
        now = time.monotonic()
        result = []
        for p in self._providers:
            until = self._cooldown_until.get(p.name, 0.0)
            on_cd = now < until
            result.append({
                "name": p.name,
                "on_cooldown": on_cd,
                "resumes_in": max(0.0, until - now) if on_cd else 0.0,
            })
        return result


# ─── Provider Chain Builder ───────────────────────────────────────────────────────
def _build_chain() -> FailoverChain:
    chain_str = os.environ.get("AI_PROVIDER_CHAIN", "").strip()
    entries: list[tuple[str, str, int]] = []

    if chain_str:
        for entry in chain_str.split(","):
            entry = entry.strip()
            if ":" not in entry:
                log.warning("Skipping malformed AI_PROVIDER_CHAIN entry: %r", entry)
                continue
            
            # Split from the right to handle model names with colons
            # Format: provider:model or provider:model:index
            parts = entry.rsplit(":", 1)  # Split only on the last colon
            
            if len(parts) == 1:
                log.warning("Skipping malformed AI_PROVIDER_CHAIN entry (missing model): %r", entry)
                continue
            
            left_part, right_part = parts
            
            # Check if right_part is a number (API key index)
            try:
                api_key_index = int(right_part.strip())
                # If it's a number, split left_part to get provider and model
                if ":" in left_part:
                    provider_type, model_name = left_part.split(":", 1)
                else:
                    log.warning("Skipping malformed AI_PROVIDER_CHAIN entry (missing provider): %r", entry)
                    continue
            except ValueError:
                # right_part is not a number, so it's part of the model name
                provider_type, model_name = entry.split(":", 1)
                api_key_index = 1
            
            entries.append((provider_type.strip().lower(), model_name.strip(), api_key_index))
    else:
        provider_type = os.environ.get("AI_PROVIDER", "gemini").strip().lower()
        model_name    = os.environ.get("AI_MODEL", "gemini-2.5-flash").strip()
        entries.append((provider_type, model_name, 1))

    providers: list[_BaseProvider] = []
    for provider_type, model_name, api_key_index in entries:
        try:
            if provider_type == "gemini":
                providers.append(GeminiProvider(model_name, api_key_index))
            elif provider_type == "openai":
                providers.append(OpenAIProvider(model_name, api_key_index))
            elif provider_type == "groq":
                providers.append(GroqProvider(model_name, api_key_index))
            elif provider_type == "openrouter":
                providers.append(OpenRouterProvider(model_name, api_key_index))
            elif provider_type == "grok":
                providers.append(GrokProvider(model_name, api_key_index))
            else:
                log.warning("Unknown provider type %r — skipping", provider_type)
                continue
            log.info("  ✓ Failover chain: registered %s/%s (API key %d)", provider_type, model_name, api_key_index)
        except Exception:
            log.exception("  ✗ Failed to init %s/%s (API key %d) — skipping", provider_type, model_name, api_key_index)

    if not providers:
        raise RuntimeError("No AI providers could be initialized. Check your .env keys.")

    return FailoverChain(providers)


# ─── AI Cog ──────────────────────────────────────────────────────────────────────
class AICog(commands.Cog, name="AI Assistant"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.context_window = int(os.environ.get("AI_CONTEXT_WINDOW", 20))

        try:
            self._chain: FailoverChain = _build_chain()
            print("✅ AI failover chain initialized successfully.")
        except Exception as err:
            self._chain = None  # type: ignore[assignment]
            print(f"❌ CRITICAL AI INITIALIZATION ERROR: {err}")
            log.exception("Failed to initialize AI failover chain — AI features disabled")

        self._memory: dict[int, Deque[dict]] = defaultdict(
            lambda: deque(maxlen=self.context_window)
        )

    # ─── Message Handler ─────────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if not message.guild:
            return
        if not self._chain:
            log.warning("AI chain not initialized — skipping message")
            return

        cfg = load_config()
        if not cfg.get("ai_enabled", True):
            return

        ai_channel_id: int | None = cfg.get("ai_channel_id")
        bot_id = self.bot.user.id if self.bot.user else None
        bot_mentioned = bot_id is not None and (
            f"<@{bot_id}>" in message.content or
            f"<@!{bot_id}>" in message.content
        )
        in_ai_channel = ai_channel_id and message.channel.id == ai_channel_id

        log.debug(
            "AI check: bot_mentioned=%s, in_ai_channel=%s, channel=%s, ai_channel=%s",
            bot_mentioned, in_ai_channel, message.channel.id, ai_channel_id,
        )

        if not (bot_mentioned or in_ai_channel):
            return

        # Strip the bot mention from the content
        content = message.content
        if self.bot.user:
            content = (
                content
                .replace(f"<@{self.bot.user.id}>", "")
                .replace(f"<@!{self.bot.user.id}>", "")
                .strip()
            )

        if not content:
            await message.reply("Hey! What can I help you with? 👋", mention_author=False)
            return

        channel_id = message.channel.id
        mem = self._memory[channel_id]
        mem.append({"role": "user", "content": content})

        # ── RAG: retrieve relevant knowledge and inject into system prompt ────────
        # Determine which knowledge categories to load based on query context
        load_categories = determine_load_categories(content)
        kb = reload_knowledge_base(load_categories)
        retrieved = kb.retrieve(content)
        
        # ── Web Search: if local knowledge is insufficient, search the guild's network ────────
        web_search_chain = get_web_search_chain()
        web_search_failed = False
        if web_search_chain:
            try:
                log.debug("Searching guild's network for: %r", content[:60])
                web_results = await web_search_chain.search(content, max_results=3)
                
                if web_results:
                    # Convert web search results to KnowledgeEntry with source_type="web"
                    for i, result in enumerate(web_results):
                        web_entry = KnowledgeEntry(
                            id=f"web_search_{i}",
                            name=f"Guild Network: {result.get('title', 'Search Result')}",
                            facts=[result.get('snippet', '')],
                            tags=["web", "network"],
                            entry_type="knowledge",
                            source_type="web",
                        )
                        retrieved.append(web_entry)
                    log.info("Added %d web search results from guild's network", len(web_results))
                else:
                    web_search_failed = True
                    log.warning("Guild network search returned no results")
            except Exception as e:
                web_search_failed = True
                log.warning("Guild network search failed: %s", e)
        
        # If web search failed, add a note to the system prompt
        if web_search_failed:
            retrieved.append(KnowledgeEntry(
                id="web_search_failed",
                name="Guild Network Status",
                facts=["The guild's network is currently down. Information may be incomplete or outdated."],
                tags=["web", "network", "offline"],
                entry_type="knowledge",
                source_type="web",
            ))
        
        rag_system_prompt = build_rag_system_prompt(SYSTEM_PROMPT, retrieved)
        if retrieved:
            log.debug("RAG: loaded categories %s, injected %d entries for query: %r", 
                     load_categories, len(retrieved), content[:60])

        messages: list[dict] = [{"role": "system", "content": rag_system_prompt}] + list(mem)

        async with message.channel.typing():
            try:
                reply = await self._chain.complete(messages)
            except Exception as exc:
                log.exception("All AI providers failed: %s", exc)
                error_text = str(exc).lower()
                if any(kw in error_text for kw in _QUOTA_KEYWORDS):
                    msg = "I can't find any sources or information regarding about your inquiry. Please come back again later."
                else:
                    msg = "*There is a letter left on the front desk, 'on break' it says*"
                await message.reply(msg, mention_author=False)
                return

        mem.append({"role": "assistant", "content": reply})

        chunks = [reply[i : i + 1900] for i in range(0, len(reply), 1900)]
        for i, chunk in enumerate(chunks):
            if i == 0:
                await message.reply(chunk, mention_author=False)
            else:
                await message.channel.send(chunk)

        log.info("AI reply sent to %s (%d chars)", message.author, len(reply))

    # ── /clear-memory ─────────────────────────────────────────────────────────────
    @discord.app_commands.command(
        name="clear-memory",
        description="Clear the AI conversation history for this channel",
    )
    async def clear_memory(self, interaction: discord.Interaction) -> None:
        self._memory.pop(interaction.channel_id, None)
        await interaction.response.send_message(
            "🧹 This channel's conversation history has been cleared.", ephemeral=True
        )

    # ── /ai-status ────────────────────────────────────────────────────────────────
    @discord.app_commands.command(
        name="ai-status",
        description="Show the current health of all AI providers in the failover chain",
    )
    @discord.app_commands.default_permissions(administrator=True)
    async def ai_status(self, interaction: discord.Interaction) -> None:
        if not self._chain:
            await interaction.response.send_message(
                "❌ AI chain is not initialized.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title="🧠  AI Failover Chain Status",
            color=discord.Color.blurple(),
        )

        for i, entry in enumerate(self._chain.status()):
            label = "🟢 Online" if not entry["on_cooldown"] else f"🔴 Cooldown ({entry['resumes_in']:.0f}s)"
            embed.add_field(
                name=f"{'[PRIMARY]' if i == 0 else f'[FALLBACK {i}]'}  {entry['name']}",
                value=label,
                inline=False,
            )

        embed.set_footer(text="Providers are tried in order from top to bottom.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /reload-knowledge ─────────────────────────────────────────────────────────
    @discord.app_commands.command(
        name="reload-knowledge",
        description="Hot-reload the knowledge base from disk without restarting the bot",
    )
    @discord.app_commands.describe(
        categories="Optional: Comma-separated categories to load (personal,world,general,archived,all)"
    )
    @discord.app_commands.default_permissions(administrator=True)
    async def reload_knowledge(self, interaction: discord.Interaction, categories: str | None = None) -> None:
        from utils.knowledge import KnowledgeCategory
        
        # Parse categories if provided
        load_categories = None
        if categories:
            cat_list = [c.strip().lower() for c in categories.split(",")]
            valid_cats = [KnowledgeCategory.PERSONAL, KnowledgeCategory.WORLD, 
                         KnowledgeCategory.GENERAL, KnowledgeCategory.ARCHIVED, KnowledgeCategory.ALL]
            load_categories = [c for c in cat_list if c in valid_cats]
            if not load_categories:
                await interaction.response.send_message(
                    f"❌ Invalid categories. Valid options: {', '.join(valid_cats)}",
                    ephemeral=True,
                )
                return
        
        kb = reload_knowledge_base(load_categories)
        before = len(kb._entries)
        kb.reload(load_categories)
        after = len(kb._entries)
        
        cat_info = f" (categories: {categories})" if categories else ""
        await interaction.response.send_message(
            f"📚 Knowledge base reloaded{cat_info}. **{before}** → **{after}** entries.",
            ephemeral=True,
        )

    # ── /bot-presence ───────────────────────────────────────────────────────────────
    @discord.app_commands.command(
        name="bot-presence",
        description="Set the AI bot's presence status (online, idle, dnd, invisible)",
    )
    @discord.app_commands.describe(
        presence="Presence status (online, idle, dnd, invisible)"
    )
    @discord.app_commands.default_permissions(administrator=True)
    async def set_presence(self, interaction: discord.Interaction, presence: str) -> None:
        valid_presences = ["online", "idle", "dnd", "invisible"]
        if presence.lower() not in valid_presences:
            await interaction.response.send_message(
                f"❌ Invalid presence. Valid options: {', '.join(valid_presences)}",
                ephemeral=True,
            )
            return
        
        # Update configuration
        if hasattr(self.bot, 'bot_config'):
            success = self.bot.bot_config.update_presence(presence.lower())
            if success:
                # Apply immediately
                presence_enum = getattr(discord.Status, presence.lower(), discord.Status.online)
                status_type, status_text = self.bot.bot_config.get_status()
                activity_type = getattr(discord.ActivityType, status_type, discord.ActivityType.watching)
                await self.bot.change_presence(
                    status=presence_enum,
                    activity=discord.Activity(type=activity_type, name=status_text)
                )
                await interaction.response.send_message(
                    f"✅ Presence updated to {presence}",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "❌ Failed to update presence configuration.",
                    ephemeral=True,
                )
        else:
            await interaction.response.send_message(
                "❌ Bot configuration not available. Make sure you're using bot_ai.py",
                ephemeral=True,
            )

    # ── /bot-status ───────────────────────────────────────────────────────────────
    @discord.app_commands.command(
        name="bot-status",
        description="Set the AI bot's status activity",
    )
    @discord.app_commands.describe(
        status_type="Type of status (playing, watching, listening, competing)",
        text="Status text to display"
    )
    @discord.app_commands.default_permissions(administrator=True)
    async def set_status(self, interaction: discord.Interaction, status_type: str, text: str) -> None:
        valid_types = ["playing", "watching", "listening", "competing"]
        if status_type.lower() not in valid_types:
            await interaction.response.send_message(
                f"❌ Invalid status type. Valid options: {', '.join(valid_types)}",
                ephemeral=True,
            )
            return
        
        # Update configuration
        if hasattr(self.bot, 'bot_config'):
            success = self.bot.bot_config.update_status(status_type.lower(), text)
            if success:
                # Apply immediately
                activity_type = getattr(discord.ActivityType, status_type.lower(), discord.ActivityType.watching)
                await self.bot.change_presence(
                    activity=discord.Activity(type=activity_type, name=text)
                )
                await interaction.response.send_message(
                    f"✅ Status updated to {status_type} {text}",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "❌ Failed to update status configuration.",
                    ephemeral=True,
                )
        else:
            await interaction.response.send_message(
                "❌ Bot configuration not available. Make sure you're using bot_ai.py",
                ephemeral=True,
            )

    # ── /bot-bio ─────────────────────────────────────────────────────────────────
    @discord.app_commands.command(
        name="bot-bio",
        description="Set the AI bot's bio/description",
    )
    @discord.app_commands.describe(
        bio="Bot bio/description text"
    )
    @discord.app_commands.default_permissions(administrator=True)
    async def set_bio(self, interaction: discord.Interaction, bio: str) -> None:
        if hasattr(self.bot, 'bot_config'):
            success = self.bot.bot_config.update_bio(bio)
            if success:
                await interaction.response.send_message(
                    f"✅ Bio updated: {bio}",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "❌ Failed to update bio configuration.",
                    ephemeral=True,
                )
        else:
            await interaction.response.send_message(
                "❌ Bot configuration not available. Make sure you're using bot_ai.py",
                ephemeral=True,
            )

    # ── /bot-config ──────────────────────────────────────────────────────────────
    @discord.app_commands.command(
        name="bot-config",
        description="View or update AI bot settings",
    )
    @discord.app_commands.describe(
        setting="Setting name (ai_enabled, context_window, max_tokens, temperature)",
        value="New value for the setting"
    )
    @discord.app_commands.default_permissions(administrator=True)
    async def config_command(self, interaction: discord.Interaction, setting: str | None = None, value: str | None = None) -> None:
        if not hasattr(self.bot, 'bot_config'):
            await interaction.response.send_message(
                "❌ Bot configuration not available. Make sure you're using bot_ai.py",
                ephemeral=True,
            )
            return
        
        if setting is None:
            # Show current configuration
            config = self.bot.bot_config.config
            embed = discord.Embed(
                title="🤖 AI Bot Configuration",
                color=discord.Color.blurple(),
            )
            embed.add_field(name="Status", value=f"{config['status']['type']}: {config['status']['text']}", inline=False)
            embed.add_field(name="Bio", value=config['bio'], inline=False)
            
            settings_text = "\n".join([f"**{k}**: {v}" for k, v in config.get('settings', {}).items()])
            embed.add_field(name="Settings", value=settings_text or "None", inline=False)
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            # Update a specific setting
            if value is None:
                await interaction.response.send_message(
                    "❌ Please provide a value for the setting.",
                    ephemeral=True,
                )
                return
            
            # Try to parse value as appropriate type
            parsed_value = value
            if setting in ["context_window", "max_tokens", "temperature"]:
                try:
                    if setting == "temperature":
                        parsed_value = float(value)
                    else:
                        parsed_value = int(value)
                except ValueError:
                    await interaction.response.send_message(
                        f"❌ Invalid value type for {setting}. Expected a number.",
                        ephemeral=True,
                    )
                    return
            elif setting == "ai_enabled":
                parsed_value = value.lower() in ["true", "1", "yes", "on"]
            
            success = self.bot.bot_config.update_setting(setting, parsed_value)
            if success:
                await interaction.response.send_message(
                    f"✅ Setting {setting} updated to {parsed_value}",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "❌ Failed to update setting configuration.",
                    ephemeral=True,
                )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AICog(bot))