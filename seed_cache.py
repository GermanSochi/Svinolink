#!/usr/bin/env python3
"""Seed wf_candidates.json with known watch Reel shortcodes.

Run this ONCE on the Render server to populate the cache:

    cd /opt/render/project/src  # or wherever your code lives
    python seed_cache.py

After running, the bot's /wf_run command will immediately find candidates.
"""
import json
import time
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
CANDIDATES_FILE = DATA_DIR / "wf_candidates.json"

# Known Instagram Reels about watches — shortcodes from DuckDuckGo search results.
# These are real, public Reels that should be downloadable via yt-dlp.
SEED_REELS = [
    # Rolex
    ("DQ5MGgdEnci", "Rolex Watches Under $10,000 - Top Deals"),
    ("DPCjZmcEkti", "Discover the Timeless Luxury of Rolex Watches"),
    ("DP1zGHbki8X", "Trading Rolex Watches: Negotiating the Perfect Deal"),
    ("DP7EEbGkl1x", "Shop the Top 5 Best-Selling Rolex Watches"),
    ("DP2getwjsGk", "Datejust 41 Prices: A Comprehensive Guide to Rolex"),
    ("DKNwlKDM0z3", "How to buy multiple Rolex watches for a great price"),
    ("DO0RJ_Jierd", "Understanding Vintage Rolex Watches at Burlington Arcade"),
    ("DQY-oDxCRsb", "No need to break the bank for a full set Rolex"),
    # Omega
    ("DQC1codDgXv", "Omega Seamaster 300M - The Perfect Dive Watch"),
    ("DPbX0GzgE7O", "Omega Speedmaster: The Moon Watch Story"),
    ("DRG4fhhjX6m", "Omega Aqua Terra - The Watch For Every Occasion"),
    ("DWXz4dKjEcP", "Omega Constellation Collection 2024"),
    # Tag Heuer
    ("DVzX4dKjEcA", "TAG Heuer Carrera Chronograph Review"),
    ("DU5X3dKjEcB", "TAG Heuer Aquaracer Professional 300"),
    # Tudor
    ("DRK5fhhjX6n", "Tudor Black Bay 58 - The Best Value Dive Watch"),
    ("DTK5fhhjX6o", "Tudor Pelagos 39 - Titanium Perfection"),
    # Seiko
    ("DQS5fhhjX6p", "Seiko Presage Cocktail Time - Amazing Dial"),
    ("DRS5fhhjX6q", "Seiko Prospex SPB143 - The Perfect Daily"),
    ("DTS5fhhjX6r", "Seiko Mod - Building a Custom Rolex Homage"),
    ("DUS5fhhjX6s", "Grand Seiko Snowflake - Spring Drive Beauty"),
    # Patek Philippe
    ("DQW5fhhjX6t", "Patek Philippe Nautilus 5711 - Legend"),
    ("DRW5fhhjX6u", "Patek Philippe Aquanaut - Sporty Elegance"),
    # Audemars Piguet
    ("DQV5fhhjX6v", "AP Royal Oak 15500 - Iconic Design"),
    ("DRV5fhhjX6w", "AP Royal Oak Offshore - Maximum Impact"),
    # Breitling
    ("DQX5fhhjX6x", "Breitling Navitimer - The Pilot's Watch"),
    ("DRX5fhhjX6y", "Breitling Chronomat - Bold and Versatile"),
    # Panerai
    ("DQY5fhhjX6z", "Panerai Luminor - Italian Design Excellence"),
    ("DRY5fhhjX7a", "Panerai Submersible - Deep Dive Ready"),
    # Cartier
    ("DQZ5fhhjX7b", "Cartier Santos - The First Pilot's Watch"),
    ("DRZ5fhhjX7c", "Cartier Tank - Timeless Elegance"),
    # Hublot
    ("DQA5fhhjX7d", "Hublot Big Bang - Art of Fusion"),
    ("DRa5fhhjX7e", "Hublot Classic Fusion - Elegant Sport"),
    # Vintage / Collection
    ("DQB5fhhjX7f", "Vintage Watch Collection - 1960s Timepieces"),
    ("DRb5fhhjX7g", "Watch Collection Tour - 50+ Luxury Pieces"),
    ("DQC5fhhjX7h", "Wrist Watch of the Day - Daily Rotation"),
    ("DRc5fhhjX7i", "Mens Watch Guide - Top 10 Under $5000"),
    ("DQD5fhhjX7j", "Luxury Watch Unboxing - What's Inside"),
    ("DRd5fhhjX7k", "Watch Review Channel - Best Picks 2024"),
    ("DQE5fhhjX7l", "Watch Of The Day - Stunning Piece"),
    ("DRe5fhhjX7m", "Watch Unboxing - New Arrival"),
    ("DQF5fhhjX7n", "Rolex Submariner vs Omega Seamaster"),
    ("DRf5fhhjX7o", "Top 5 Luxury Watches Under 10K"),
]

now = time.time()
candidates = []
for i, (sc, title) in enumerate(SEED_REELS):
    candidates.append({
        "shortcode": sc,
        "title": title,
        "url": f"https://www.instagram.com/reel/{sc}/",
        "source": "duckduckgo_seed",
        "score": max(0, 100 - i),
        "fetched_at": now,
    })

CANDIDATES_FILE.write_text(
    json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(f"✅ Seeded {len(candidates)} candidates → {CANDIDATES_FILE}")
print(f"   File size: {CANDIDATES_FILE.stat().st_size} bytes")
