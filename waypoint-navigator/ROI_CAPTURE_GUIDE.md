# Manual ROI Capture Tools - OBS Projector Integration

This directory contains two ROI (Region of Interest) capture tools specifically designed to capture from the OBS projector on Monitor 2, using the same frame sources as the Waypoint Navigator bot.

## 🖥️ GUI Tool: `manual_roi_capture.py`

**Full-featured graphical interface for ROI capture and management.**

### Features
- ✨ Interactive GUI with real-time preview
- 🔍 **Advanced zoom controls** - up to 800% zoom for pixel-perfect selection
- 🎥 **OBS projector integration** - captures from Monitor 2 using bot's frame system
- 📷 Monitor 2 screenshot capture (backup method)
- 🎬 Live frame preview from OBS projector
- 🎯 Click-and-drag ROI selection with crosshairs
- 📋 Preset templates for all bot modules
- 🖱️ **Pan and zoom** - middle-click pan, mouse wheel zoom
- 💾 Save/load ROI configurations
- 🧪 Test ROIs against live OBS frames
- 📤 Export directly to bot config files

### Requirements
```bash
pip install tkinter opencv-python pillow mss numpy
```

### Launch
```bash
cd waypoint-navigator
python manual_roi_capture.py
```

### Usage Workflow
1. **Setup OBS**: Ensure OBS projector is running on Monitor 2 (1920×1080)
2. **Capture Frame**: Click "🎥 Capture from OBS (Live)" for real-time capture, or "📷 Screenshot Monitor 2" for static capture
3. **Select ROI Type**: Choose from presets (Minimap, HP Bar, etc.) or Custom
4. **Use Zoom Controls**:
   - **➕➖ Buttons**: Zoom in/out (25% to 800%)
   - **Mouse Wheel**: Zoom towards cursor position
   - **Middle-click + drag**: Pan when zoomed in
   - **100% Button**: Reset to fit view
5. **Draw Region**: Click and drag on the OBS frame to select the ROI
6. **Precision Mode**: Zoom to 400%+ for small ROIs (HP/MP bars: 120×12 pixels)
7. **Add to List**: Click "➕ Add ROI" to save the selection
8. **Test**: Click "🧪 Test ROI" to preview the captured region
9. **Export**: Click "📤 Export All" to save to bot config files

### Zoom Controls Reference

**GUI Tool**:
- **➕➖ Buttons**: Step through zoom levels (25%, 50%, 75%, 100%, 150%, 200%, 300%, 400%, 600%, 800%)
- **Mouse Wheel**: Smooth zoom towards cursor
- **Middle-click + drag**: Pan around when zoomed
- **Right-click**: Clear current selection
**CLI Tool**:
- **+/- Keys**: Zoom in/out through preset levels
- **Mouse Wheel**: Zoom towards cursor position
- **Arrow Keys**: Pan when zoomed in
- **'f' Key**: Fit to window (reset zoom/pan)
- **'r' Key**: Reset selection and zoom
- **Crosshairs**: Auto-displayed at 150%+ zoom for precision
- **Click + drag**: Select ROI area
- **Enter/Space**: Confirm selection
- **Esc/q**: Cancel selection

---

## ⚡ CLI Tool: `cli_roi_capture.py`

**Command-line interface for quick ROI calibration without GUI dependencies.**

### Features
- 🚀 Fast command-line operation
- 🔍 **OpenCV zoom interface** - up to 800% zoom with keyboard/mouse controls
- 🎥 **OBS projector capture** using bot's frame system
- 📷 Monitor 2 screenshot capture (backup method)
- 🎯 Interactive OpenCV-based selection with crosshairs
- 📋 Preset coordinate application
- ⌨️ Manual coordinate entry
- 🧪 Config file testing
- 📤 Direct config file export

### Requirements
```bash
pip install opencv-python mss numpy
```

### Quick Start
```bash
# Interactive calibration for minimap
python cli_roi_capture.py --calibrate minimap

# List available presets
python cli_roi_capture.py --list-presets

# Test existing config
python cli_roi_capture.py --test-config minimap_config.json

# Capture screenshot and select ROI interactively
python cli_roi_capture.py --screenshot capture.png --type hp_bar --interactive
```

### Command Reference

#### Interactive Calibration
```bash
python cli_roi_capture.py --calibrate <roi_type>
```
Full workflow: capture → select → preview → save

#### Screenshot + ROI Selection
```bash
# Interactive selection
python cli_roi_capture.py --screenshot capture.png --type minimap --interactive

# Use preset coordinates
python cli_roi_capture.py --screenshot capture.png --type minimap --preset

# Manual coordinates
python cli_roi_capture.py --screenshot capture.png --type minimap --coords "1665,55,240,176"
```

#### Utility Commands
```bash
# List all available ROI presets
python cli_roi_capture.py --list-presets

# Test existing configuration
python cli_roi_capture.py --test-config minimap_config.json

# Specify custom project path
python cli_roi_capture.py --calibrate minimap --project-path /path/to/waypoint-navigator
```

---

## 📋 Available ROI Presets

Both tools support these preset ROI types:

| Type | Config File | Description | Default Coordinates |
|------|-------------|-------------|-------------------|
| `minimap` | minimap_config.json | Minimap detection area | (1665, 55) 240×176 |
| `hp_bar` | hpmp_config.json | Health points bar | (157, 56) 120×12 |
| `mp_bar` | hpmp_config.json | Mana points bar | (157, 75) 120×12 |
| `battle_list` | combat_config.json | Combat battle list | (1720, 245) 185×400 |
| `chat` | chat_config.json | Chat messages area | (8, 304) 640×356 |
| `status_icons` | condition_config.json | Status condition icons | (1665, 32) 240×20 |
| `depot` | depot_config.json | Depot container area | (1403, 152) 502×364 |
| `gm_scan` | gm_detector_config.json | GM detection scan area | (0, 0) 1920×1080 |

---

## 🔧 Integration with Bot

### Configuration Files
ROI coordinates are automatically saved to the appropriate bot config files:

- **Minimap**: `minimap_config.json` → `roi` key
- **HP/MP bars**: `hpmp_config.json` → `hp_roi`/`mp_roi` keys
- **Battle list**: `combat_config.json` → `battle_list_roi` key
- **Chat**: `chat_config.json` → `chat_roi` key
- etc.

### Coordinate Format
All ROI coordinates use this JSON format:
```json
{
  "x": 1665,
  "y": 55,
  "width": 240,
  "height": 176
}
```

### Testing ROIs
After calibration, test your ROIs by:
1. Running the bot's calibration command: `python main.py calibrate`
2. Using the CLI test: `python cli_roi_capture.py --test-config minimap_config.json`
3. Checking the bot logs for successful region detection

---

## 🚀 Quick Calibration Workflow

### For Dual Monitor Setup (OBS Projector on Monitor 2)

1. **Setup**: Ensure Tibia is on Monitor 1 and OBS projector is running on Monitor 2
2. **Capture**: Use GUI "🎥 Capture from OBS (Live)" or CLI `--calibrate minimap` to capture from OBS projector
3. **Calibrate**: Start with minimap (most critical) → HP/MP bars → battle list → depot
4. **Test**: Run bot calibration to verify all regions work correctly

⚠️ **Important**: The tools now capture directly from the OBS projector using the same frame source as the bot. This ensures pixel-perfect coordinate alignment.

### Critical ROIs (Priority Order)
1. **Minimap** (position detection) - Must be precise
2. **HP/MP bars** (healing) - Essential for survival
3. **Battle list** (combat) - Required for targeting
4. **Depot** (item management) - For banking/restocking
5. **Status icons** (condition monitoring) - For curse/poison detection

---

## ⚠️ Troubleshooting

### Common Issues

**"Frame capture not available"**
- Ensure OBS is running with projector on Monitor 2
- Check that `src.frame_capture` module is accessible
- Try screenshot capture instead

**"OpenCV window not responding"**
- Ensure X11 forwarding if using SSH
- Try running locally instead of remote session
- Use CLI manual coordinate entry as fallback

**"Config file not found"**
- Ensure you're in the waypoint-navigator directory
- Use `--project-path` to specify correct location
- Check that config files exist and are writable

**Coordinates out of bounds**
- Verify monitor resolution matches expectations (1920×1080)
- Re-capture screenshot if resolution changed
- Use relative coordinates for multi-resolution support

### Monitor 2 Setup
For dual-monitor setups, ensure OBS projector is correctly positioned:
```bash
# Test OBS projector capture
python cli_roi_capture.py --calibrate minimap
# This will capture directly from OBS projector on Monitor 2
```

**OBS Integration Notes:**
- The tools use `build_frame_getter("mss", monitor_idx=2)` - same as the bot
- Captures from Monitor 2 (x=1920 offset) where OBS projector runs
- Uses `FrameCache` for efficiency - same frame caching as bot
- Guarantees pixel-perfect coordinate alignment between ROI tool and bot
- Live preview shows exactly what the bot sees in real-time

---

## 🎯 Pro Tips

1. **OBS Sync**: Always capture from OBS projector for exact bot alignment
2. **Live Preview**: Use GUI live preview to see real-time frame updates
3. **Precision**: Use maximum zoom when selecting small ROIs like HP/MP bars
4. **Testing**: Always test ROIs with `--test-config` before running the bot
5. **Backup**: Save ROI configurations as backups before making changes
6. **Updates**: Re-calibrate ROIs after game updates or OBS changes
7. **Validation**: Use the GUI preview feature to verify ROI contents match expectations