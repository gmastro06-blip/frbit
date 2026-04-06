"""
download_monster_templates.py
=============================
Descarga imágenes de monstruos desde TibiaWiki y las convierte en templates
listos para usar con CombatManager (BattleDetector).

- Descarga GIFs animados de la wiki
- Extrae el primer frame
- Redimensiona a target_size (default 32×32 px, tamaño típico de la battle list)
- Guarda como PNG en cache/templates/monsters/

Uso:
    python examples/download_monster_templates.py
    python examples/download_monster_templates.py --size 22
    python examples/download_monster_templates.py --monsters "Troll,Goblin,Orc"
    python examples/download_monster_templates.py --list          # muestra lista común

Requiere: requests, Pillow
    pip install requests Pillow
"""

from __future__ import annotations

import argparse
import sys
import time
from io import BytesIO
from pathlib import Path

# ─── Lista completa del Bestiary por clase (TibiaWiki/Bestiary/Classes) ──────
COMMON_MONSTERS = [
    # ── Amphibic (11) ────────────────────────────────────────────────────────
    "Azure Frog", "Toad", "Orchid Frog", "Coral Frog", "Crimson Frog",
    "Thornback Tortoise", "Tortoise", "Bog Raider", "Muddy Earth Elemental",
    "Quara Predator", "Quara Predator Scout",

    # ── Aquatic (40) ─────────────────────────────────────────────────────────
    "Crab", "Blood Crab", "Quara Constrictor", "Quara Constrictor Scout",
    "Quara Hydromancer", "Quara Hydromancer Scout",
    "Quara Mantassin", "Quara Mantassin Scout",
    "Quara Pincher", "Quara Pincher Scout",
    "Quara Predator", "Sea Serpent", "Young Sea Serpent",
    "Deepling Elite", "Deepling Guard", "Deepling Master Librarian",
    "Deepling Scout", "Deepling Spellsinger", "Deepling Warrior",
    "Shark", "Silencer",
    "Foam Stalker", "Leviathan",
    "Sulphur Spouter", "Tunnel Tyrant",

    # ── Bird (18) ────────────────────────────────────────────────────────────
    "Terror Bird", "Parrot", "Flamingo", "Chicken", "Seagull",
    "Sandstone Scorpion", "Harpy", "Gryphon",
    "Energetic Book", "Penguin",
    "Ice Witch", "Polar Bear", "Crystal Spider", "Frost Dragon Hatchling",
    "Frost Dragon",

    # ── Construct (34) ───────────────────────────────────────────────────────
    "Stone Golem", "Ice Golem", "War Golem", "Lava Golem",
    "Demon Skeleton", "Skeleton Warrior", "Undead Gladiator",
    "Damaged Worker Golem", "Worker Golem",
    "Clay Guardian", "Metal Gargoyle", "Stone Gargoyle",
    "Animated Sword", "Animated Snowman",
    "Rusty Armor (Common)", "Ancient Scarab",
    "Terrified Elephant", "Swarmer",
    "Sparkion", "Ghastly Dragon",

    # ── Demon (42) ───────────────────────────────────────────────────────────
    "Demon", "Fire Devil", "Destroyer", "Dark Torturer", "Hellhound",
    "Plaguesmith", "Hellspawn", "Juggernaut", "Defiler",
    "Hand of Cursed Fate", "Infernalist",
    "Massive Fire Elemental", "Prince Drazzak", "The Pale Worm",
    "Gaz'haragoth", "Apocalypse",
    "Hellfire Fighter", "Fire Elemental",
    "Retching Horror", "Feverish Citizen", "Deathstrike",
    "Shrieking Cry-Stal", "Grimeleech", "Vexclaw", "Guzzlemaw",
    "Frazzlemaw", "Choking Fear", "Wailing Widow",

    # ── Dragon (20) ──────────────────────────────────────────────────────────
    "Dragon", "Dragon Lord", "Dragon Hatchling", "Dragon Lord Hatchling",
    "Frost Dragon", "Frost Dragon Hatchling",
    "Hydra", "Serpent Spawn", "Medusa",
    "Bog Raider", "Sea Serpent", "Young Sea Serpent",
    "Draken Elite", "Draken Spellweaver", "Draken Warmaster",
    "Draken Abomination", "Ghastly Dragon",
    "Lizard Chosen", "Lizard Dragon Priest", "Lizard High Guard",

    # ── Elemental (22) ───────────────────────────────────────────────────────
    "Fire Elemental", "Water Elemental", "Massive Water Elemental",
    "Cliff Strider", "Earth Elemental", "Massive Earth Elemental",
    "Energy Elemental", "Massive Energy Elemental",
    "Crystalcrusher",
    "Lava Golem", "Sparkion", "Stone Golem",
    "Blazing Fire Elemental",
    "Magicthrower", "Rage Squid", "Sandcrawler", "Sulphur Spouter",

    # ── Extra Dimensional (12) ───────────────────────────────────────────────
    "Breach Brood", "Courage Leech", "Dread Intruder", "Yielothax",
    "Eradicator", "Instable Breach Brood",
    "Realityreaper", "Soulsnatcher", "Souleater",
    "Nightmare",

    # ── Fey (20) ─────────────────────────────────────────────────────────────
    "Boogy", "Dryad", "Wisp", "Dark Faun", "Pooka",
    "Faun", "Pixie", "Nymph", "Leaf Golem",
    "Canopic Jar", "Gloom Wolf", "Midnight Panther",
    "Slippery Northern Pike", "Thornback Tortoise",
    "Werewolf", "Werebear",
    "Lost Soul", "Haunted Dragon", "Lost Basher",

    # ── Giant (18) ───────────────────────────────────────────────────────────
    "Cyclops", "Cyclops Smith", "Cyclops Drone",
    "Behemoth", "Frost Giant", "Frost Giantess",
    "Ogre Brute", "Ogre Savage", "Ogre Shaman",
    "Troll Champion", "Troll Marauder", "Troll Guard",
    "Island Troll", "Rorc", "Diabolic Imp",

    # ── Human (77) ───────────────────────────────────────────────────────────
    "Amazon", "Valkyrie", "Witch", "Hero",
    "Hunter", "Assassin", "Pirate Marauder",
    "Pirate Cutthroat", "Pirate Buccaneer",
    "Barbarian Bloodwalker", "Barbarian Headsplitter", "Barbarian Skullhunter",
    "Dworc Fleshhunter", "Dworc Voodoomaster",
    "Nomad", "Nomad (Female)",
    "Renegade Knight",
    "Gladiator", "Bandit",
    "Dark Apprentice",
    "Medusa", "Werehyaena Shaman",
    "Vile Grandmaster", "Yalahari (Creature)",

    # ── Humanoid (96) ────────────────────────────────────────────────────────
    "Troll", "Frost Troll", "Island Troll",
    "Goblin", "Goblin Scavenger", "Goblin Leader",
    "Orc", "Orc Warrior", "Orc Spearman", "Orc Berserker",
    "Orc Leader", "Orc Rider", "Orc Shaman", "Orc Warlord",
    "Minotaur", "Minotaur Guard", "Minotaur Mage", "Minotaur Archer",
    "Minotaur Bruiser", "Minotaur Hunter",
    "Dwarf", "Dwarf Soldier", "Dwarf Guard", "Dwarf Geomancer",
    "Lizard Sentinel", "Lizard Snakecharmer", "Lizard Templar",
    "Lizard Legionnaire", "Lizard Zaogun",
    "Corym Charlatan", "Corym Skirmisher", "Corym Vanguard",
    "Gnarlhound", "Werehyaena", "Werehyaena Shaman",

    # ── Magical (58) ─────────────────────────────────────────────────────────
    "Bonelord", "Elder Bonelord",
    "Gargoyle", "Iron Servant",
    "Green Djinn", "Blue Djinn", "Efreet", "Marid",
    "Invisible", "Nightmare",
    "Bonebeast", "Crazed Summer Rearguard", "Crazed Winter Rearguard",
    "Crystal Spider", "Evil Mastermind",
    "Furious Troll", "Shadow Pupil",
    "Phantasm", "Spectre",
    "Betrayed Wraith",
    "Lost Soul", "Wyvern", "Rogue Naga",
    "Mummy", "The Evil Eye",
    "Necromancer", "Lich", "Undead Dragon",
    "Morgaroth",

    # ── Lycanthrope (15) ─────────────────────────────────────────────────────
    "Werewolf", "Werebear", "Werebadger", "Wereboar", "Werefox",
    "Werepanther", "Weretiger", "Werelion", "Werecrocodile",
    "Werehyaena", "Werehyaena Shaman",
    "Gloom Wolf", "Midnight Panther",
    "Manticore",

    # ── Mammal (74) ──────────────────────────────────────────────────────────
    "Rat", "Cave Rat", "Dog", "Wolf", "Bear", "Deer",
    "Sheep", "Black Sheep", "Mammoth",
    "Polar Bear", "War Wolf",
    "Lion", "Tiger",
    "Kongra", "Sibang", "Merlkin",
    "Wild Warrior", "Draptor",
    "Elephant", "Terrified Elephant",
    "Hyaena",
    "Dromedary", "Horse", "Donkey",
    "Panda", "Rabbit", "Squirrel",
    "Yeti",

    # ── Plant (18) ───────────────────────────────────────────────────────────
    "Shaman",
    "Spit Nettle", "Carniphila", "Bane Bringer",
    "Branchy Crawler",
    "Barkless Devotee", "Barkless Fanatic",
    "Animated Snowman",
    "Leaf Golem",
    "Thornback Tortoise", "Woodling",
    "Lizard Chosen",

    # ── Reptile (33) ─────────────────────────────────────────────────────────
    "Snake", "Cobra", "Crocodile",
    "Lizard Sentinel", "Lizard Snakecharmer", "Lizard Templar",
    "Lizard Legionnaire", "Lizard Zaogun",
    "Lizard Chosen", "Lizard Dragon Priest", "Lizard High Guard",
    "Naga Archer", "Naga Warrior",
    "Salamander", "Girtablilu Warrior", "Sandstone Scorpion",
    "Caiman", "Thornback Tortoise",
    "Dragon", "Dragon Hatchling",
    "Wyvern", "Sea Serpent",
    "Medusa", "Serpent Spawn",
    "Draken Abomination",

    # ── Slime (13) ───────────────────────────────────────────────────────────
    "Slime", "Son of Verminor", "Defiler",
    "Acid Blob", "Mercury Blob",
    "Carrion Worm",
    "Hydra", "Orc Berserker",
    "Rotworm",

    # ── Undead (94) ──────────────────────────────────────────────────────────
    "Skeleton", "Skeleton Warrior", "Undead Gladiator",
    "Zombie", "Ghoul", "Mummy",
    "Vampire", "Vampire Bride", "Vile Grandmaster",
    "Necromancer", "Lich", "Undead Dragon",
    "Ghost", "Demon Skeleton", "Crypt Shambler",
    "Grim Reaper", "Gravedigger",
    "Bonebeast", "Bonelord",
    "Betrayed Wraith",
    "Lost Soul", "Spectre", "Phantasm",
    "Gravedigger",
    "Morguthis", "Blightwalker",

    # ── Vermin (61) ──────────────────────────────────────────────────────────
    "Rotworm", "Carrion Worm",
    "Spider", "Poison Spider", "Tarantula", "Giant Spider",
    "Bug", "Ancient Scarab", "Scarab",
    "Wasp", "Ladybug",
    "Sandcrawler", "Stone Devourer",
    "Crawler", "Larva",
    "Centipede", "Scorpion",
    "Corym Charlatan", "Corym Skirmisher", "Corym Vanguard",
    "Flimsy Lost Soul",
    "Gloom Wolf", "Grynch Clan Goblin",
    "Swamp Troll", "Troll Marauder",
]

WIKI_API = "https://tibia.fandom.com/api.php"
WIKI_IMG = "https://static.wikia.nocookie.net/tibia/images"


def get_image_url(session, monster_name: str) -> str | None:
    """Obtiene la URL del GIF del monstruo desde la API de TibiaWiki."""
    filename = monster_name.replace(" ", "_") + ".gif"
    params = {
        "action": "query",
        "titles": f"File:{filename}",
        "prop": "imageinfo",
        "iiprop": "url",
        "format": "json",
    }
    try:
        r = session.get(WIKI_API, params=params, timeout=10)
        data = r.json()
        pages = data.get("query", {}).get("pages", {})
        for page in pages.values():
            info = page.get("imageinfo", [])
            if info:
                return info[0]["url"]
    except Exception as exc:
        print(f"  [FAIL] API error para '{monster_name}': {exc}")
    return None


def download_and_convert(
    session,
    url: str,
    out_path: Path,
    target_size: int,
) -> bool:
    """Descarga GIF animado, extrae primer frame, redimensiona y guarda PNG."""
    try:
        from PIL import Image

        r = session.get(url, timeout=15)
        r.raise_for_status()
        img = Image.open(BytesIO(r.content))
        img.seek(0)          # primer frame del GIF
        frame = img.convert("RGBA")

        # Redimensionar manteniendo aspecto, rellenando con negro
        frame.thumbnail((target_size, target_size), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (target_size, target_size), (0, 0, 0, 255))
        offset = (
            (target_size - frame.width) // 2,
            (target_size - frame.height) // 2,
        )
        canvas.paste(frame, offset, frame)
        rgb = canvas.convert("RGB")
        rgb.save(str(out_path), "PNG")
        return True
    except Exception as exc:
        print(f"    Error al convertir: {exc}")
        return False


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Descarga templates de monstruos desde TibiaWiki"
    )
    ap.add_argument(
        "--monsters", default=None,
        help='Lista separada por comas, ej: "Troll,Goblin,Orc"'
    )
    ap.add_argument(
        "--size", type=int, default=32,
        help="Tamaño objetivo del template en píxeles (default: 32)"
    )
    ap.add_argument(
        "--list", action="store_true",
        help="Mostrar la lista de monstruos comunes y salir"
    )
    ap.add_argument(
        "--overwrite", action="store_true",
        help="Sobreescribir templates existentes"
    )
    ap.add_argument(
        "--delay", type=float, default=0.4,
        help="Pausa entre requests (segundos, default: 0.4)"
    )
    args = ap.parse_args()

    if args.list:
        print("Monstruos en la lista predefinida:")
        for m in COMMON_MONSTERS:
            print(f"  {m}")
        return

    project_root = Path(__file__).parent.parent
    dest = project_root / "cache" / "templates" / "monsters"
    dest.mkdir(parents=True, exist_ok=True)

    if args.monsters:
        monsters = [m.strip() for m in args.monsters.split(",") if m.strip()]
    else:
        monsters = COMMON_MONSTERS

    print(f"Descargando {len(monsters)} templates -> {dest}")
    print(f"Tamaño: {args.size}×{args.size} px\n")

    try:
        import requests
    except ImportError:
        print("ERROR: instala requests -> pip install requests")
        sys.exit(1)

    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        print("ERROR: instala Pillow -> pip install Pillow")
        sys.exit(1)

    session = requests.Session()
    session.headers["User-Agent"] = "waypoint-navigator-template-downloader/1.0"

    ok = 0
    skip = 0
    fail = 0

    for monster in monsters:
        safe_name = monster.lower().replace(" ", "_")
        out_path = dest / f"{safe_name}.png"

        if out_path.exists() and not args.overwrite:
            print(f"  [SKIP] {monster:30s} (ya existe, usa --overwrite)")
            skip += 1
            continue

        url = get_image_url(session, monster)
        if not url:
            print(f"  [MISS] {monster:30s} (no encontrado en wiki)")
            fail += 1
            time.sleep(args.delay)
            continue

        success = download_and_convert(session, url, out_path, args.size)
        if success:
            print(f"  [ OK ] {monster:30s} -> {out_path.name}")
            ok += 1
        else:
            print(f"  [FAIL] {monster:30s} (fallo al convertir)")
            fail += 1

        time.sleep(args.delay)

    print(f"\n{'-' * 37}")
    print(f"  OK:       {ok}")
    print(f"  Omitidos: {skip}")
    print(f"  Fallos:   {fail}")
    print(f"  Total:    {len(monsters)}")
    print(f"\nTemplates en: {dest}")


if __name__ == "__main__":
    main()
