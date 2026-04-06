# Anchors — UI anchor templates for Adaptive ROI detection

Templates in this folder are used by `AdaptiveROIDetector` to automatically
locate UI elements regardless of window position or resolution.

## Required templates

| File | What to capture | Approx size |
| ------ | ---------------- | ------------- |
| `hp_bar_corner.png` | Left edge of the HP bar (green area start) | 20-40px |
| `mp_bar_corner.png` | Left edge of the MP bar (blue area start) | 20-40px |
| `minimap_corner.png` | Top-left corner of the minimap border | 20-40px |
| `battle_list_header.png` | "Battle List" text header | 60-100px wide |
| `inventory_header.png` | "Inventory" text header | 60-100px wide |
| `chat_header.png` | Chat tab area header | 60-100px wide |

## How to capture

```bash
# 1. Take a screenshot
python tools/capture_templates.py --window "Tibia" --screenshot output/capture.png

# 2. Crop interactively
python tools/capture_templates.py --from-file output/capture.png --crop

# Select "anchors" as category when prompted.
```
