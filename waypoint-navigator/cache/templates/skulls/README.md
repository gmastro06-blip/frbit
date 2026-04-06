# Skulls — PvP skull icon templates

Templates used by `PvPDetector` to detect skull markers on players in the
battle list.

## Required templates

| File | What to capture | Size |
| ------ | ---------------- | ------ |
| `white_skull.png` | White skull icon from battle list | 11-16px |
| `yellow_skull.png` | Yellow skull icon | 11-16px |
| `orange_skull.png` | Orange skull icon | 11-16px |
| `red_skull.png` | Red skull icon | 11-16px |
| `black_skull.png` | Black skull icon | 11-16px |
| `green_skull.png` | Green skull icon (party) | 11-16px |

## How to find

Skulls appear next to player names in the battle list. You need to be in a
PvP area where players have skulls to capture these.

```bash
python tools/capture_templates.py --from-file output/capture.png --crop
# Select "skulls" as category
```
