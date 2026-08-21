#!/usr/bin/env python3
"""
migrate_knowledge.py — Migration Script for Modular Knowledge Base

Splits the monolithic knowledge_base.json into four modular files:
- personal_knowledge.json: Scribe's private thoughts, inventory, immediate sensory data
- world_knowledge.json: Active guild rosters, current maps, active quests, living NPCs
- general_knowledge.json: Static world rules, magic mechanics, standard currencies, common lore
- archived_knowledge.json: Completed historical quests, dead NPCs, ancient blueprints

Usage:
    python migrate_knowledge.py [--backup] [--dry-run]

Options:
    --backup: Create a backup of the original knowledge_base.json before migration
    --dry-run: Show what would be done without actually writing files
"""

import json
import shutil
from pathlib import Path
from datetime import datetime
import sys


# File paths
LEGACY_FILE = Path("data/knowledge_base.json")
BACKUP_DIR = Path("data/backups")

# New modular files
PERSONAL_FILE = Path("data/personal_knowledge.json")
WORLD_FILE = Path("data/world_knowledge.json")
GENERAL_FILE = Path("data/general_knowledge.json")
ARCHIVED_FILE = Path("data/archived_knowledge.json")


def create_backup():
    """Create a timestamped backup of the original knowledge_base.json."""
    if not LEGACY_FILE.exists():
        print(f"⚠️  Legacy file {LEGACY_FILE} not found, skipping backup")
        return None
    
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"knowledge_base_{timestamp}.json"
    
    shutil.copy2(LEGACY_FILE, backup_path)
    print(f"✅ Backup created: {backup_path}")
    return backup_path


def categorize_knowledge_entry(entry: dict) -> str:
    """
    Determine which category a knowledge entry belongs to based on its content.
    
    Returns: 'personal', 'world', 'general', or 'archived'
    """
    entry_id = entry.get("id", "").lower()
    name = entry.get("name", "").lower()
    tags = [tag.lower() for tag in entry.get("tags", [])]
    
    # Personal knowledge: scribe-specific, private thoughts, inventory
    personal_keywords = ["scribe", "elyra", "personal", "private", "thought", "inventory", "sensory"]
    if any(kw in entry_id or kw in name or any(kw in tag for tag in tags) for kw in personal_keywords):
        return "personal"
    
    # Archived knowledge: historical, ancient, completed, dead, defunct
    archived_keywords = ["ancient", "historical", "completed", "dead", "deceased", "defunct", "disbanded", "legacy"]
    if any(kw in entry_id or kw in name or any(kw in tag for tag in tags) for kw in archived_keywords):
        return "archived"
    
    # World knowledge: active entities, current state, guild-specific
    world_keywords = ["guild", "roster", "active", "current", "quest", "npc", "player", "settlement"]
    if any(kw in entry_id or kw in name or any(kw in tag for tag in tags) for kw in world_keywords):
        return "world"
    
    # Default to general knowledge for static mechanics, rules, lore
    return "general"


def categorize_world_state(state: dict) -> str:
    """Determine which category a world state belongs to."""
    state_id = state.get("id", "").lower()
    category = state.get("category", "").lower()
    
    # Personal world states
    if "personal" in state_id or "scribe" in state_id or "elyra" in state_id:
        return "personal"
    
    # Archived world states
    archived_keywords = ["ancient", "historical", "completed", "defunct", "disbanded"]
    if any(kw in state_id or kw in category for kw in archived_keywords):
        return "archived"
    
    # Default to world knowledge for active states
    return "world"


def categorize_entity_state(entity: dict) -> str:
    """Determine which category an entity state belongs to."""
    entity_id = entity.get("id", "").lower()
    name = entity.get("name", "").lower()
    entity_type = entity.get("entity_type", "").lower()
    
    # Personal entity (the scribe herself)
    if "elyra" in entity_id or "elyra" in name or "scribe" in name.lower():
        return "personal"
    
    # Archived entities (dead, deceased, historical)
    archived_keywords = ["dead", "deceased", "fallen", "ancient", "historical", "defunct"]
    if any(kw in entity_id or kw in name or kw in entity_type for kw in archived_keywords):
        return "archived"
    
    # Default to world knowledge for active entities
    return "world"


def migrate_data(data: dict, dry_run: bool = False) -> dict:
    """
    Split the monolithic data into four categories.
    
    Returns: Dictionary with keys 'personal', 'world', 'general', 'archived'
    """
    categories = {
        "personal": {"knowledge_entries": [], "world_states": [], "entity_states": []},
        "world": {"knowledge_entries": [], "world_states": [], "entity_states": []},
        "general": {"knowledge_entries": [], "world_states": [], "entity_states": []},
        "archived": {"knowledge_entries": [], "world_states": [], "entity_states": []},
    }
    
    # Categorize knowledge entries
    for entry in data.get("knowledge_entries", []):
        cat = categorize_knowledge_entry(entry)
        categories[cat]["knowledge_entries"].append(entry)
    
    # Categorize world states
    for state in data.get("world_states", []):
        cat = categorize_world_state(state)
        categories[cat]["world_states"].append(state)
    
    # Categorize entity states
    for entity in data.get("entity_states", []):
        cat = categorize_entity_state(entity)
        categories[cat]["entity_states"].append(entity)
    
    # Print summary
    print("\n📊 Migration Summary:")
    for cat_name, cat_data in categories.items():
        total = (
            len(cat_data["knowledge_entries"]) +
            len(cat_data["world_states"]) +
            len(cat_data["entity_states"])
        )
        print(f"  {cat_name.upper():12} : {total:3} items "
              f"(knowledge: {len(cat_data['knowledge_entries'])}, "
              f"world: {len(cat_data['world_states'])}, "
              f"entities: {len(cat_data['entity_states'])})")
    
    return categories


def write_category_files(categories: dict, dry_run: bool = False):
    """Write the categorized data to their respective files."""
    file_map = {
        "personal": PERSONAL_FILE,
        "world": WORLD_FILE,
        "general": GENERAL_FILE,
        "archived": ARCHIVED_FILE,
    }
    
    for cat_name, cat_data in categories.items():
        file_path = file_map[cat_name]
        
        if dry_run:
            print(f"\n[DRY RUN] Would write to {file_path}:")
            print(f"  knowledge_entries: {len(cat_data['knowledge_entries'])}")
            print(f"  world_states: {len(cat_data['world_states'])}")
            print(f"  entity_states: {len(cat_data['entity_states'])}")
        else:
            # Ensure parent directory exists
            file_path.parent.mkdir(parents=True, exist_ok=True)
            
            with file_path.open("w", encoding="utf-8") as f:
                json.dump(cat_data, f, indent=2, ensure_ascii=False)
            
            print(f"✅ Written {file_path}")


def main():
    """Main migration function."""
    args = sys.argv[1:]
    backup = "--backup" in args
    dry_run = "--dry-run" in args
    
    print("=" * 60)
    print("🔄 Knowledge Base Migration Script")
    print("=" * 60)
    
    # Check if legacy file exists
    if not LEGACY_FILE.exists():
        print(f"❌ Error: Legacy file {LEGACY_FILE} not found!")
        print("   Make sure you're running this script from the project root.")
        sys.exit(1)
    
    # Create backup if requested
    if backup and not dry_run:
        create_backup()
    
    # Load legacy data
    print(f"\n📖 Loading legacy data from {LEGACY_FILE}...")
    try:
        with LEGACY_FILE.open(encoding="utf-8") as f:
            data = json.load(f)
        print(f"✅ Loaded {len(data.get('knowledge_entries', []))} knowledge entries, "
              f"{len(data.get('world_states', []))} world states, "
              f"{len(data.get('entity_states', []))} entity states")
    except json.JSONDecodeError as e:
        print(f"❌ Error parsing JSON: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error loading file: {e}")
        sys.exit(1)
    
    # Migrate data
    print("\n🔀 Categorizing data...")
    categories = migrate_data(data, dry_run)
    
    # Write files
    if not dry_run:
        print("\n💾 Writing modular files...")
        write_category_files(categories, dry_run)
        print("\n✅ Migration complete!")
        print("\n📝 Next steps:")
        print("   1. Review the generated files in the data/ directory")
        print("   2. Test the bot to ensure knowledge retrieval works correctly")
        print("   3. If satisfied, you can delete the old knowledge_base.json")
        print("   4. Update your AI cog to use the new modular system")
    else:
        print("\n[DRY RUN] No files were written.")
        print("Run without --dry-run to perform the actual migration.")


if __name__ == "__main__":
    main()
