"""
utils/knowledge.py — Lightweight Retrieval-Augmented Knowledge Layer

Loads a local JSON knowledge base and retrieves relevant entries before
any LLM call. The retrieval result is formatted into a consistent block
that gets injected into the system prompt identically for ALL providers.

Architecture:
  user message
      ↓
  retrieve(query) → top N KnowledgeEntry objects
      ↓
  build_rag_system_prompt(base_prompt, entries) → final system string
      ↓
  passed to FailoverChain.complete() — provider-agnostic, no changes needed there
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger("bot.knowledge")

# Four modular knowledge files
PERSONAL_FILE = Path("data/personal_knowledge.json")
WORLD_FILE = Path("data/world_knowledge.json")
GENERAL_FILE = Path("data/general_knowledge.json")
ARCHIVED_FILE = Path("data/archived_knowledge.json")

MAX_RESULTS = 5   # Maximum entries injected per query

# Knowledge categories for selective loading
class KnowledgeCategory:
    PERSONAL = "personal"
    WORLD = "world"
    GENERAL = "general"
    ARCHIVED = "archived"
    ALL = "all"


# ─── Data Model ──────────────────────────────────────────────────────────────────
@dataclass
class KnowledgeEntry:
    id: str
    name: str
    facts: list[str]
    tags: list[str] = field(default_factory=list)
    entry_type: str = "knowledge"  # "knowledge", "world_state", or "entity_state"
    source_type: str = "archival"  # "archival", "general", "personal", "web"

    def all_match_tokens(self) -> set[str]:
        """Returns every normalised token this entry can be matched against."""
        tokens: set[str] = set()
        tokens.update(_tokenize(self.name))
        for tag in self.tags:
            tokens.update(_tokenize(tag))
        return tokens


@dataclass
class WorldStateEntry:
    id: str
    category: str
    current_version: int
    state: list[str]
    timestamp: str

    def all_match_tokens(self) -> set[str]:
        """Returns every normalised token this entry can be matched against."""
        tokens: set[str] = set()
        tokens.update(_tokenize(self.id))
        tokens.update(_tokenize(self.category))
        for fact in self.state:
            tokens.update(_tokenize(fact))
        return tokens


@dataclass
class EntityStateEntry:
    id: str
    entity_type: str
    name: str
    aliases: list[str]
    current_version: int
    data: dict
    timestamp: str

    def all_match_tokens(self) -> set[str]:
        """Returns every normalised token this entry can be matched against."""
        tokens: set[str] = set()
        tokens.update(_tokenize(self.name))
        tokens.update(_tokenize(self.id))
        for alias in self.aliases:
            tokens.update(_tokenize(alias))
        # Also tokenize data values for matching
        for value in self.data.values():
            if isinstance(value, str):
                tokens.update(_tokenize(value))
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        tokens.update(_tokenize(item))
        return tokens


# ─── Tokenizer ───────────────────────────────────────────────────────────────────
def _tokenize(text: str) -> list[str]:
    """Lowercase, strip punctuation, split on whitespace and underscores."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)   # remove punctuation
    text = text.replace("_", " ")
    return [t for t in text.split() if t]


# ─── Knowledge Base ──────────────────────────────────────────────────────────────
class KnowledgeBase:
    def __init__(self, load_categories: list[str] | None = None) -> None:
        self._entries: list[KnowledgeEntry] = []
        self._world_states: list[WorldStateEntry] = []
        self._entity_states: list[EntityStateEntry] = []
        self._load_categories = load_categories or [KnowledgeCategory.ALL]
        self._load()

    def _validate_json(self, data: dict, file_name: str) -> bool:
        """Strict JSON validation with detailed error logging."""
        errors = []
        
        # Check top-level structure
        if not isinstance(data, dict):
            errors.append("Root must be a JSON object")
            return False
        
        # Validate knowledge_entries
        if "knowledge_entries" in data:
            if not isinstance(data["knowledge_entries"], list):
                errors.append("knowledge_entries must be an array")
            else:
                for i, entry in enumerate(data["knowledge_entries"]):
                    if not isinstance(entry, dict):
                        errors.append(f"knowledge_entries[{i}] must be an object")
                        continue
                    required = ["id", "name", "facts"]
                    for field in required:
                        if field not in entry:
                            errors.append(f"knowledge_entries[{i}] missing required field: {field}")
                    if "facts" in entry and not isinstance(entry["facts"], list):
                        errors.append(f"knowledge_entries[{i}].facts must be an array")
        
        # Validate world_states
        if "world_states" in data:
            if not isinstance(data["world_states"], list):
                errors.append("world_states must be an array")
            else:
                for i, entry in enumerate(data["world_states"]):
                    if not isinstance(entry, dict):
                        errors.append(f"world_states[{i}] must be an object")
                        continue
                    required = ["type", "id", "current_version", "versions"]
                    for field in required:
                        if field not in entry:
                            errors.append(f"world_states[{i}] missing required field: {field}")
                    if "versions" in entry and not isinstance(entry["versions"], list):
                        errors.append(f"world_states[{i}].versions must be an array")
                    elif "versions" in entry:
                        for j, version in enumerate(entry["versions"]):
                            if not isinstance(version, dict):
                                errors.append(f"world_states[{i}].versions[{j}] must be an object")
                                continue
                            version_required = ["version", "timestamp", "is_current", "state"]
                            for vfield in version_required:
                                if vfield not in version:
                                    errors.append(f"world_states[{i}].versions[{j}] missing required field: {vfield}")
        
        # Validate entity_states
        if "entity_states" in data:
            if not isinstance(data["entity_states"], list):
                errors.append("entity_states must be an array")
            else:
                for i, entry in enumerate(data["entity_states"]):
                    if not isinstance(entry, dict):
                        errors.append(f"entity_states[{i}] must be an object")
                        continue
                    required = ["type", "entity_type", "id", "name", "current_version", "versions"]
                    for field in required:
                        if field not in entry:
                            errors.append(f"entity_states[{i}] missing required field: {field}")
                    if "versions" in entry and not isinstance(entry["versions"], list):
                        errors.append(f"entity_states[{i}].versions must be an array")
                    elif "versions" in entry:
                        for j, version in enumerate(entry["versions"]):
                            if not isinstance(version, dict):
                                errors.append(f"entity_states[{i}].versions[{j}] must be an object")
                                continue
                            version_required = ["version", "timestamp", "is_current", "data"]
                            for vfield in version_required:
                                if vfield not in version:
                                    errors.append(f"entity_states[{i}].versions[{j}] missing required field: {vfield}")
        
        if errors:
            log.error("JSON validation failed for %s with %d errors:", file_name, len(errors))
            for error in errors:
                log.error("  - %s", error)
            return False
        
        return True

    def _load_file(self, file_path: Path, category: str) -> dict | None:
        """Load a single knowledge file and return its data."""
        if not file_path.exists():
            log.debug("%s not found at %s — skipping", file_path.name, file_path)
            return None
        
        try:
            with file_path.open(encoding="utf-8") as f:
                data = json.load(f)
            
            # Strict validation - reject if invalid
            if not self._validate_json(data, file_path.name):
                log.error("%s validation failed - rejecting load", file_path.name)
                return None
            
            log.debug("Loaded %s from %s", category, file_path.name)
            return data
        except json.JSONDecodeError as e:
            log.error("JSON parsing error in %s: %s at line %d", file_path.name, e.msg, e.lineno)
            return None
        except Exception:
            log.exception("Failed to load %s", file_path.name)
            return None

    def _load(self) -> None:
        """Load knowledge from modular JSON files based on specified categories."""
        should_load = {
            KnowledgeCategory.PERSONAL: KnowledgeCategory.ALL in self._load_categories or KnowledgeCategory.PERSONAL in self._load_categories,
            KnowledgeCategory.WORLD: KnowledgeCategory.ALL in self._load_categories or KnowledgeCategory.WORLD in self._load_categories,
            KnowledgeCategory.GENERAL: KnowledgeCategory.ALL in self._load_categories or KnowledgeCategory.GENERAL in self._load_categories,
            KnowledgeCategory.ARCHIVED: KnowledgeCategory.ALL in self._load_categories or KnowledgeCategory.ARCHIVED in self._load_categories,
        }
        
        # Load from each file if category is enabled
        if should_load[KnowledgeCategory.PERSONAL]:
            personal_data = self._load_file(PERSONAL_FILE, KnowledgeCategory.PERSONAL)
            if personal_data:
                self._process_data(personal_data, KnowledgeCategory.PERSONAL)
        
        if should_load[KnowledgeCategory.WORLD]:
            world_data = self._load_file(WORLD_FILE, KnowledgeCategory.WORLD)
            if world_data:
                self._process_data(world_data, KnowledgeCategory.WORLD)
        
        if should_load[KnowledgeCategory.GENERAL]:
            general_data = self._load_file(GENERAL_FILE, KnowledgeCategory.GENERAL)
            if general_data:
                self._process_data(general_data, KnowledgeCategory.GENERAL)
        
        if should_load[KnowledgeCategory.ARCHIVED]:
            archived_data = self._load_file(ARCHIVED_FILE, KnowledgeCategory.ARCHIVED)
            if archived_data:
                self._process_data(archived_data, KnowledgeCategory.ARCHIVED)
        
        # Fallback: try loading old monolithic file if no new files exist
        if not any([PERSONAL_FILE.exists(), WORLD_FILE.exists(), GENERAL_FILE.exists(), ARCHIVED_FILE.exists()]):
            log.warning("No modular knowledge files found, attempting to load legacy knowledge_base.json")
            legacy_data = self._load_file(Path("data/knowledge_base.json"), "legacy")
            if legacy_data:
                self._process_data(legacy_data, "legacy")
        
        log.info("Knowledge base loaded: %d knowledge entries, %d world states, %d entity states", 
                len(self._entries), len(self._world_states), len(self._entity_states))

    def _process_data(self, data: dict, category: str) -> None:
        """Process loaded JSON data and populate internal structures."""
        # Map category to source_type
        source_type_map = {
            KnowledgeCategory.PERSONAL: "personal",
            KnowledgeCategory.WORLD: "archival",
            KnowledgeCategory.GENERAL: "general",
            KnowledgeCategory.ARCHIVED: "archival",
            "legacy": "archival",
        }
        source_type = source_type_map.get(category, "archival")
        
        # Load knowledge entries
        for raw in data.get("knowledge_entries", []):
            entry = KnowledgeEntry(
                id=raw["id"],
                name=raw["name"],
                facts=raw.get("facts", []),
                tags=raw.get("tags", []) + [category],
                entry_type="knowledge",
                source_type=source_type,
            )
            self._entries.append(entry)
        
        # Load world states with version filtering
        for raw in data.get("world_states", []):
            current_version = raw.get("current_version", 1)
            for version in raw.get("versions", []):
                # Only load the current version
                if version.get("version") == current_version and version.get("is_current", False):
                    self._world_states.append(WorldStateEntry(
                        id=raw["id"],
                        category=raw.get("category", ""),
                        current_version=current_version,
                        state=version.get("state", []),
                        timestamp=version.get("timestamp", ""),
                    ))
                    log.debug("Loaded world_state %s at version %d from %s", raw["id"], current_version, category)
                    break
            else:
                log.warning("world_state %s has no matching current version %d", raw["id"], current_version)
        
        # Load entity states with version filtering
        for raw in data.get("entity_states", []):
            current_version = raw.get("current_version", 1)
            for version in raw.get("versions", []):
                # Only load the current version
                if version.get("version") == current_version and version.get("is_current", False):
                    self._entity_states.append(EntityStateEntry(
                        id=raw["id"],
                        entity_type=raw.get("entity_type", ""),
                        name=raw.get("name", ""),
                        aliases=raw.get("aliases", []),
                        current_version=current_version,
                        data=version.get("data", {}),
                        timestamp=version.get("timestamp", ""),
                    ))
                    log.debug("Loaded entity_state %s at version %d from %s", raw["id"], current_version, category)
                    break
            else:
                log.warning("entity_state %s has no matching current version %d", raw["id"], current_version)

    def reload(self, load_categories: list[str] | None = None) -> None:
        """Hot-reload the knowledge base without restarting the bot.
        
        Args:
            load_categories: Optional list of categories to load. If None, uses current categories.
        """
        self._entries.clear()
        self._world_states.clear()
        self._entity_states.clear()
        if load_categories is not None:
            self._load_categories = load_categories
        self._load()

    def _write_file(self, file_path: Path, data: dict) -> bool:
        """Write data to a single knowledge file."""
        try:
            # Ensure parent directory exists
            file_path.parent.mkdir(parents=True, exist_ok=True)
            
            with file_path.open("w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            log.debug("Successfully wrote %s", file_path.name)
            return True
        except Exception:
            log.exception("Failed to write %s", file_path.name)
            return False

    def save_category(self, category: str, data: dict) -> bool:
        """Save data to a specific category file.
        
        Args:
            category: One of KnowledgeCategory.PERSONAL, WORLD, GENERAL, ARCHIVED
            data: Dictionary containing knowledge_entries, world_states, and/or entity_states
            
        Returns:
            True if successful, False otherwise.
        """
        file_map = {
            KnowledgeCategory.PERSONAL: PERSONAL_FILE,
            KnowledgeCategory.WORLD: WORLD_FILE,
            KnowledgeCategory.GENERAL: GENERAL_FILE,
            KnowledgeCategory.ARCHIVED: ARCHIVED_FILE,
        }
        
        if category not in file_map:
            log.error("Unknown category: %s", category)
            return False
        
        return self._write_file(file_map[category], data)

    def add_knowledge_entry(self, entry: KnowledgeEntry, category: str) -> bool:
        """Add a knowledge entry to a specific category file.
        
        Args:
            entry: The KnowledgeEntry to add
            category: One of KnowledgeCategory.PERSONAL, WORLD, GENERAL, ARCHIVED
            
        Returns:
            True if successful, False otherwise.
        """
        file_map = {
            KnowledgeCategory.PERSONAL: PERSONAL_FILE,
            KnowledgeCategory.WORLD: WORLD_FILE,
            KnowledgeCategory.GENERAL: GENERAL_FILE,
            KnowledgeCategory.ARCHIVED: ARCHIVED_FILE,
        }
        
        if category not in file_map:
            log.error("Unknown category: %s", category)
            return False
        
        file_path = file_map[category]
        
        # Load existing data
        try:
            if file_path.exists():
                with file_path.open(encoding="utf-8") as f:
                    data = json.load(f)
            else:
                data = {"knowledge_entries": [], "world_states": [], "entity_states": []}
        except Exception:
            log.exception("Failed to load existing data from %s", file_path.name)
            return False
        
        # Add the entry
        entry_dict = {
            "id": entry.id,
            "name": entry.name,
            "facts": entry.facts,
            "tags": [tag for tag in entry.tags if tag != category],  # Remove category tag
        }
        data["knowledge_entries"].append(entry_dict)
        
        # Write back
        return self._write_file(file_path, data)

    def retrieve(self, query: str, max_results: int = MAX_RESULTS) -> list[KnowledgeEntry]:
        """
        Score every entry against the query and return the top matches.

        Scoring (cumulative per entry):
          +3  for each query token that exactly matches an entry token
          +1  for each query token that is a substring of an entry token
              ONLY if the substring is at least 3 characters long

        CRITICAL: Minimum score threshold of 3 required to be considered a match.
        This prevents false positives from single-character or short substring matches.
        
        CRITICAL: Only current_version entries are loaded, so no version filtering
        is needed at retrieval time. Older versions are never accessible.
        """
        query_tokens = _tokenize(query)
        if not query_tokens:
            log.debug("Query produced no tokens: %r", query)
            return []

        scored: list[tuple[int, KnowledgeEntry]] = []
        MIN_SCORE_THRESHOLD = 3  # Require at least one exact match or multiple partial matches
        
        # Score knowledge entries
        for entry in self._entries:
            entry_tokens = entry.all_match_tokens()
            score = 0
            for qt in query_tokens:
                if qt in entry_tokens:
                    score += 3                          # exact token match
                else:
                    # Only allow substring match if token is at least 3 chars
                    if len(qt) >= 3:
                        for et in entry_tokens:
                            if qt in et or et in qt:
                                score += 1              # substring / partial match
                                break
            if score >= MIN_SCORE_THRESHOLD:
                scored.append((score, entry))
        
        # Score world states (convert to KnowledgeEntry for consistency)
        for ws in self._world_states:
            entry_tokens = ws.all_match_tokens()
            score = 0
            for qt in query_tokens:
                if qt in entry_tokens:
                    score += 3
                else:
                    if len(qt) >= 3:
                        for et in entry_tokens:
                            if qt in et or et in qt:
                                score += 1
                                break
            if score >= MIN_SCORE_THRESHOLD:
                # Convert world state to knowledge entry format
                scored.append((score, KnowledgeEntry(
                    id=ws.id,
                    name=f"World State: {ws.category}",
                    facts=ws.state,
                    tags=["world_state", ws.category],
                    entry_type="world_state",
                    source_type="archival",
                )))
                log.debug("Retrieved world_state %s (version %d) with score %d", ws.id, ws.current_version, score)
        
        # Score entity states (convert to KnowledgeEntry for consistency)
        for es in self._entity_states:
            entry_tokens = es.all_match_tokens()
            score = 0
            for qt in query_tokens:
                if qt in entry_tokens:
                    score += 3
                else:
                    if len(qt) >= 3:
                        for et in entry_tokens:
                            if qt in et or et in qt:
                                score += 1
                                break
            if score >= MIN_SCORE_THRESHOLD:
                # Convert entity data to facts
                facts = []
                for key, value in es.data.items():
                    if isinstance(value, list):
                        facts.append(f"{key}: {', '.join(value)}")
                    else:
                        facts.append(f"{key}: {value}")
                scored.append((score, KnowledgeEntry(
                    id=es.id,
                    name=f"Entity: {es.name} ({es.entity_type})",
                    facts=facts,
                    tags=["entity_state", es.entity_type, es.name] + es.aliases,
                    entry_type="entity_state",
                    source_type="archival",
                )))
                log.debug("Retrieved entity_state %s (version %d) with score %d", es.id, es.current_version, score)

        if not scored:
            log.debug("No entries matched query (below threshold): %r", query)
        
        scored.sort(key=lambda x: x[0], reverse=True)
        results = [entry for _, entry in scored[:max_results]]
        
        if results:
            log.info("Retrieved %d entries for query: %r", len(results), query[:60])
        
        return results


# ─── Prompt Formatter ─────────────────────────────────────────────────────────────
def format_knowledge_block(entries: list[KnowledgeEntry]) -> Optional[str]:
    """
    Serialises retrieved entries into the canonical RELEVANT_KNOWLEDGE block
    that gets injected into the system prompt for every provider identically.
    Returns None if no entries were retrieved (no injection needed).
    """
    if not entries:
        return None

    lines: list[str] = ["RELEVANT_KNOWLEDGE:"]
    for entry in entries:
        lines.append(f'  "id": "{entry.id}",')
        lines.append(f'  "name": "{entry.name}",')
        lines.append(f'  "source_type": "{entry.source_type}",')
        if entry.tags:
            tag_str = ", ".join(f'"{t}"' for t in entry.tags)
            lines.append(f'  "tags": [{tag_str}],')
        lines.append('  "facts": [')
        for fact in entry.facts:
            lines.append(f'    "{fact}"')
        lines.append("  ]")
        lines.append("")   # blank line between entries

    return "\n".join(lines).rstrip()


# ─── System Prompt Builder ────────────────────────────────────────────────────────
_RAG_RULES = (
    "\n\nKNOWLEDGE GROUNDING RULES (enforce strictly): "
    "\n- RELEVANT_KNOWLEDGE entries below are tagged with 'source_type' indicating their origin:"
    "\n  - 'archival': Guild archives (world-specific lore, locations, NPCs, mechanics, procedures). This is the ONLY source of truth for world-specific information."
    "\n  - 'general': General knowledge (math, basic facts, common sense). You may use this freely without referencing archives."
    "\n  - 'personal': Personal information about you (Elyra). Use naturally without referencing archives."
    "\n  - 'web': Information retrieved from the guild's network (external sources). USE THIS to answer questions about current events, real-time data, or topics not in your archives."
    "\n- IMPORTANT: When you see entries with source_type='web', you MUST use that information to answer the user's question. Say 'I searched the guild's network and found...' or similar."
    "\n- If you see a web entry with id='web_search_failed' or tags containing 'offline', the guild's network is down. Mention this in your response: 'The guild's network is currently down, so I may not have the most current information.'"
    "\n- For world-specific questions (locations, NPCs, guild procedures, lore, mechanics): ONLY use entries with source_type='archival'. If not found, say 'I have no record of that in the archives'."
    "\n- For general knowledge questions: You may use entries with source_type='general' or your own knowledge. If you don't know, say 'I don't know' or 'I'm not certain' - do NOT reference archives."
    "\n- For personal questions: Use entries with source_type='personal' or answer naturally as yourself. Do NOT reference archives for personal matters."
    "\n- CRITICAL: Do NOT add, extend, invent, or infer facts beyond what is explicitly written in RELEVANT_KNOWLEDGE."
    "\n- If the answer to a factual question is not present in RELEVANT_KNOWLEDGE, say plainly that you are unsure and do not guess."
    "\n- When users ask 'what else', 'tell me more', or similar: If you have exhausted all relevant information from the provided knowledge, explicitly say 'I have no additional information on that topic' rather than inventing details."
    "\n- Casual conversation and non-factual responses are not bound by these rules."
)


def build_rag_system_prompt(base_prompt: str, entries: list[KnowledgeEntry]) -> str:
    """
    Combines the base system prompt + RAG rules + the retrieved knowledge block
    into a single system string. Works identically for every provider since
    it is just a string — no provider-specific handling required.

    If no entries were retrieved, the base prompt is returned unchanged
    (minus the RAG rules, since no grounding is available anyway — the model
    falls back to its own knowledge and Elyra's behavior rules).
    """
    knowledge_block = format_knowledge_block(entries)
    if not knowledge_block:
        return base_prompt

    return f"{base_prompt}{_RAG_RULES}\n\n{knowledge_block}"


# ─── Context Management ─────────────────────────────────────────────────────────────
def determine_load_categories(query: str) -> list[str]:
    """
    Analyze the query to determine which knowledge categories should be loaded.
    This prevents loading unnecessary data (e.g., archived knowledge) on every query.
    
    Args:
        query: The user's query string
        
    Returns:
        List of category names to load. Defaults to [PERSONAL, WORLD, GENERAL].
        ARCHIVED is only included if the query explicitly references history, past, old, etc.
    """
    query_lower = query.lower()
    
    # Keywords that suggest archival/historical queries
    archival_keywords = [
        "history", "historical", "past", "old", "ancient", "former", "previous",
        "used to", "was", "before", "archive", "record", "ancestors", "legacy",
        "deceased", "dead", "fallen", "defunct", "disbanded", "completed quest",
    ]
    
    # Keywords that suggest personal queries
    personal_keywords = [
        "your", "you", "scribe", "elyra", "personal", "private", "thought",
        "feeling", "opinion", "memory", "inventory", "sensory",
    ]
    
    categories = [KnowledgeCategory.PERSONAL, KnowledgeCategory.WORLD, KnowledgeCategory.GENERAL]
    
    # Check if query references archival content
    if any(keyword in query_lower for keyword in archival_keywords):
        categories.append(KnowledgeCategory.ARCHIVED)
        log.debug("Query includes archival keywords, loading ARCHIVED category")
    
    # If query is purely personal, we could optimize by only loading personal
    # But for now, keep world and general as they provide context
    if any(keyword in query_lower for keyword in personal_keywords):
        log.debug("Query includes personal keywords, prioritizing PERSONAL category")
    
    return categories


# ─── Module-level singleton ───────────────────────────────────────────────────────
# Instantiated once at import time. Shared across the entire bot process.
_kb: Optional[KnowledgeBase] = None


def get_knowledge_base(load_categories: list[str] | None = None) -> KnowledgeBase:
    """Get the singleton knowledge base instance.
    
    Args:
        load_categories: Optional list of categories to load. If None, loads all categories.
                      Use KnowledgeCategory constants for valid values.
    
    Returns:
        The KnowledgeBase singleton instance.
    """
    global _kb
    if _kb is None:
        _kb = KnowledgeBase(load_categories)
    elif load_categories is not None:
        # Reload with new categories if specified
        _kb.reload(load_categories)
    return _kb


def reload_knowledge_base(load_categories: list[str] | None = None) -> KnowledgeBase:
    """Force reload the knowledge base with optional category filtering.
    
    Args:
        load_categories: Optional list of categories to load. If None, loads all categories.
    
    Returns:
        The reloaded KnowledgeBase instance.
    """
    global _kb
    if _kb is None:
        _kb = KnowledgeBase(load_categories)
    else:
        _kb.reload(load_categories)
    return _kb