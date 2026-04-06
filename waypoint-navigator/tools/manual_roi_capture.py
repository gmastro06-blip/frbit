"""
Manual ROI (Region of Interest) Capture Tool for Waypoint Navigator

This module provides an interactive GUI for manually capturing and calibrating
screen regions from the OBS projector (Monitor 2) using the same frame sources as the bot.

Features:
- OBS projector frame capture using bot's frame system (Monitor 2)
- Interactive rectangle selection with mouse
- Live preview mode for real-time OBS frames
- Export to appropriate config JSON files
- Support for all bot vision modules (minimap, HP/MP, combat, etc.)
- Pixel-perfect coordinate alignment with bot vision
"""

import json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from typing import Callable, Dict, List, Optional, Any, TYPE_CHECKING
import cv2
import numpy as np
from dataclasses import dataclass, asdict
from PIL import Image, ImageTk
import mss
import time
import threading

# For frame capture integration - use the same system as the bot
if TYPE_CHECKING:
    from src.frame_cache import FrameCache as _FrameCacheType
    from src.models import Coordinate as _CoordinateType

build_frame_getter: Callable[..., Callable[[], Optional[np.ndarray]]] | None
FrameCache: type[Any] | None
Coordinate: type[Any] | None
ROICoords = dict[str, int]
ROIPreset = dict[str, str | ROICoords]

try:
    from src.frame_capture import build_frame_getter
    from src.models import Coordinate
    from src.frame_cache import FrameCache
    FRAME_CAPTURE_AVAILABLE = True
except ImportError:
    build_frame_getter = None
    FrameCache = None
    Coordinate = None
    FRAME_CAPTURE_AVAILABLE = False


@dataclass
class ROI:
    """Region of Interest definition."""
    name: str
    x: int
    y: int
    width: int
    height: int
    description: str = ""
    config_file: str = ""

    @property
    def x2(self) -> int:
        return self.x + self.width

    @property
    def y2(self) -> int:
        return self.y + self.height

    def to_dict(self) -> Dict[str, Any]:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height
        }


class ROISelector:
    """Interactive ROI selection widget with zoom functionality."""

    def __init__(self, parent: tk.Widget, image: np.ndarray) -> None:
        self.parent = parent
        self.original_image = image.copy()
        self.display_image = image.copy()

        # Zoom state
        self.zoom_level = 1.0
        self.zoom_levels = [0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0]
        self.zoom_index = 3  # Start at 100%
        self.pan_x = 0.0
        self.pan_y = 0.0

        # Selection state
        self.start_x = 0.0
        self.start_y = 0.0
        self.current_roi: Optional[ROI] = None
        self.is_selecting = False
        self.is_panning = False
        self.last_pan_x = 0
        self.last_pan_y = 0

        # Create frame for canvas and controls
        self.frame = tk.Frame(parent)
        self.frame.pack(fill="both", expand=True)

        # Zoom controls
        zoom_frame = tk.Frame(self.frame)
        zoom_frame.pack(fill="x", pady=2)

        tk.Label(zoom_frame, text="🔍 Zoom:").pack(side="left")
        tk.Button(zoom_frame, text="➖", command=self._zoom_out, width=3).pack(side="left", padx=2)
        tk.Button(zoom_frame, text="➕", command=self._zoom_in, width=3).pack(side="left", padx=2)
        tk.Button(zoom_frame, text="100%", command=self._zoom_fit, width=6).pack(side="left", padx=2)

        self.zoom_label = tk.Label(zoom_frame, text="100%")
        self.zoom_label.pack(side="left", padx=5)

        tk.Label(zoom_frame, text="| Middle-click: Pan | Wheel: Zoom | Right-click: Reset selection").pack(side="left", padx=10)

        # Create canvas with scrollbars
        canvas_frame = tk.Frame(self.frame)
        canvas_frame.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(canvas_frame, cursor="crosshair", bg="gray")

        # Scrollbars
        v_scroll = tk.Scrollbar(canvas_frame, orient="vertical", command=self.canvas.yview)
        h_scroll = tk.Scrollbar(canvas_frame, orient="horizontal", command=self.canvas.xview)

        self.canvas.configure(yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set)

        # Grid layout
        self.canvas.grid(row=0, column=0, sticky="nsew")
        v_scroll.grid(row=0, column=1, sticky="ns")
        h_scroll.grid(row=1, column=0, sticky="ew")

        canvas_frame.grid_rowconfigure(0, weight=1)
        canvas_frame.grid_columnconfigure(0, weight=1)

        # Bind events
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-2>", self._on_middle_click)  # Middle click for pan
        self.canvas.bind("<B2-Motion>", self._on_pan)
        self.canvas.bind("<ButtonRelease-2>", self._on_pan_end)
        self.canvas.bind("<Button-3>", self._on_right_click)  # Right click reset
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)  # Zoom with wheel
        self.canvas.focus_set()  # Allow keyboard focus

        self._update_display()

    def _zoom_in(self) -> None:
        """Increase zoom level."""
        if self.zoom_index < len(self.zoom_levels) - 1:
            self.zoom_index += 1
            self._apply_zoom()

    def _zoom_out(self) -> None:
        """Decrease zoom level."""
        if self.zoom_index > 0:
            self.zoom_index -= 1
            self._apply_zoom()

    def _zoom_fit(self) -> None:
        """Reset zoom to 100%."""
        self.zoom_index = 3  # 100% zoom
        self.pan_x = 0
        self.pan_y = 0
        self._apply_zoom()

    def _apply_zoom(self) -> None:
        """Apply current zoom level and update display."""
        self.zoom_level = self.zoom_levels[self.zoom_index]
        self.zoom_label.config(text=f"{int(self.zoom_level * 100)}%")
        self._update_display()

    def _on_mousewheel(self, event: Any) -> None:
        """Handle mouse wheel zoom."""
        if event.delta > 0:
            self._zoom_in()
        else:
            self._zoom_out()

    def _on_middle_click(self, event: Any) -> None:
        """Start panning mode."""
        self.is_panning = True
        self.last_pan_x = event.x
        self.last_pan_y = event.y
        self.canvas.config(cursor="fleur")

    def _on_pan(self, event: Any) -> None:
        """Handle panning."""
        if self.is_panning:
            dx = event.x - self.last_pan_x
            dy = event.y - self.last_pan_y

            self.pan_x -= dx / self.zoom_level
            self.pan_y -= dy / self.zoom_level

            self.last_pan_x = event.x
            self.last_pan_y = event.y

            self._update_display()

    def _on_pan_end(self, event: Any) -> None:
        """End panning mode."""
        self.is_panning = False
        self.canvas.config(cursor="crosshair")

    def _on_right_click(self, event: Any) -> None:
        """Reset current selection."""
        self.current_roi = None
        self.canvas.delete("roi_rect")

    def _screen_to_image_coords(self, screen_x: float, screen_y: float) -> tuple[float, float]:
        """Convert screen coordinates to image coordinates considering zoom and pan."""
        # Convert canvas coords to image coords
        img_x = (screen_x / self.zoom_level) + self.pan_x
        img_y = (screen_y / self.zoom_level) + self.pan_y
        return img_x, img_y

    def _image_to_screen_coords(self, img_x: float, img_y: float) -> tuple[float, float]:
        """Convert image coordinates to screen coordinates considering zoom and pan."""
        screen_x = (img_x - self.pan_x) * self.zoom_level
        screen_y = (img_y - self.pan_y) * self.zoom_level
        return screen_x, screen_y

    def _on_click(self, event: Any) -> None:
        """Start ROI selection."""
        if self.is_panning:
            return

        self.start_x, self.start_y = self._screen_to_image_coords(
            self.canvas.canvasx(event.x),
            self.canvas.canvasy(event.y)
        )
        self.is_selecting = True

    def _on_drag(self, event: Any) -> None:
        """Update ROI rectangle during drag."""
        if not self.is_selecting or self.is_panning:
            return

        current_x, current_y = self._screen_to_image_coords(
            self.canvas.canvasx(event.x),
            self.canvas.canvasy(event.y)
        )

        # Convert back to screen coords for display
        start_screen_x, start_screen_y = self._image_to_screen_coords(self.start_x, self.start_y)
        current_screen_x, current_screen_y = self._image_to_screen_coords(current_x, current_y)

        # Clear previous rectangle
        self.canvas.delete("roi_rect")

        # Draw new rectangle
        self.canvas.create_rectangle(
            start_screen_x, start_screen_y, current_screen_x, current_screen_y,
            outline="red", width=2, tags="roi_rect"
        )

        # Add size info
        w = abs(current_x - self.start_x)
        h = abs(current_y - self.start_y)
        size_text = f"{int(w)}×{int(h)}"
        text_x = min(start_screen_x, current_screen_x) + 5
        text_y = min(start_screen_y, current_screen_y) - 20

        self.canvas.delete("size_text")
        self.canvas.create_text(
            text_x, text_y, text=size_text, fill="red",
            font=("Arial", 10, "bold"), tags="size_text", anchor="w"
        )

    def _on_release(self, event: Any) -> None:
        """Finalize ROI selection."""
        if not self.is_selecting or self.is_panning:
            return

        end_x, end_y = self._screen_to_image_coords(
            self.canvas.canvasx(event.x),
            self.canvas.canvasy(event.y)
        )

        # Calculate ROI bounds in image coordinates
        x1 = min(self.start_x, end_x)
        y1 = min(self.start_y, end_y)
        x2 = max(self.start_x, end_x)
        y2 = max(self.start_y, end_y)

        roi_x = int(x1)
        roi_y = int(y1)
        roi_width = int(x2 - x1)
        roi_height = int(y2 - y1)

        if roi_width > 5 and roi_height > 5:  # Minimum size check
            self.current_roi = ROI(
                name="", x=roi_x, y=roi_y,
                width=roi_width, height=roi_height
            )

            # Show final selection with green border
            self.canvas.delete("roi_rect", "size_text")
            start_screen_x, start_screen_y = self._image_to_screen_coords(x1, y1)
            end_screen_x, end_screen_y = self._image_to_screen_coords(x2, y2)

            self.canvas.create_rectangle(
                start_screen_x, start_screen_y, end_screen_x, end_screen_y,
                outline="lime", width=3, tags="roi_final"
            )

            # Final size display
            final_text = f"✓ {roi_width}×{roi_height} @ ({roi_x},{roi_y})"
            self.canvas.create_text(
                start_screen_x + 5, start_screen_y - 20, text=final_text,
                fill="lime", font=("Arial", 10, "bold"), tags="roi_final", anchor="w"
            )

        self.is_selecting = False

    def _update_display(self) -> None:
        """Update canvas with current image, zoom, and pan."""
        if self.display_image is None:
            return

        # Apply zoom to image
        img_height, img_width = self.original_image.shape[:2]

        # Calculate zoomed size
        zoomed_width = int(img_width * self.zoom_level)
        zoomed_height = int(img_height * self.zoom_level)

        # Constrain pan to image bounds
        max_pan_x = max(0, img_width - (800 / self.zoom_level))
        max_pan_y = max(0, img_height - (600 / self.zoom_level))
        self.pan_x = max(0, min(self.pan_x, max_pan_x))
        self.pan_y = max(0, min(self.pan_y, max_pan_y))

        # Create display region
        if self.zoom_level != 1.0:
            # Calculate crop region
            crop_x1 = int(self.pan_x)
            crop_y1 = int(self.pan_y)
            crop_x2 = int(min(img_width, self.pan_x + (800 / self.zoom_level)))
            crop_y2 = int(min(img_height, self.pan_y + (600 / self.zoom_level)))

            # Crop and resize
            cropped = self.original_image[crop_y1:crop_y2, crop_x1:crop_x2]

            if cropped.size > 0:
                display_width = int((crop_x2 - crop_x1) * self.zoom_level)
                display_height = int((crop_y2 - crop_y1) * self.zoom_level)

                if display_width > 0 and display_height > 0:
                    display_img = cv2.resize(cropped, (display_width, display_height),
                                           interpolation=cv2.INTER_NEAREST if self.zoom_level > 2.0 else cv2.INTER_LINEAR)
                else:
                    display_img = self.original_image
            else:
                display_img = self.original_image
        else:
            display_img = self.original_image

        # Convert to PIL and display
        if len(display_img.shape) == 3:
            image = cv2.cvtColor(display_img, cv2.COLOR_BGR2RGB)
        else:
            image = display_img

        pil_image = Image.fromarray(image)
        self.photo = ImageTk.PhotoImage(pil_image)

        # Update canvas
        self.canvas.delete("image")
        self.canvas.create_image(0, 0, anchor="nw", image=self.photo, tags="image")

        # Update scroll region
        self.canvas.config(scrollregion=(0, 0, pil_image.width, pil_image.height))


class ManualROICapture:
    """Main ROI capture application."""

    # Predefined ROI types for common bot modules
    ROI_PRESETS: dict[str, ROIPreset] = {
        "Minimap": {
            "config_file": "minimap_config.json",
            "description": "Minimap detection area",
            "default_roi": {"x": 1665, "y": 55, "width": 240, "height": 176}
        },
        "HP Bar": {
            "config_file": "hpmp_config.json",
            "key": "hp_roi",
            "description": "Health points bar region",
            "default_roi": {"x": 157, "y": 56, "width": 120, "height": 12}
        },
        "MP Bar": {
            "config_file": "hpmp_config.json",
            "key": "mp_roi",
            "description": "Mana points bar region",
            "default_roi": {"x": 157, "y": 75, "width": 120, "height": 12}
        },
        "Battle List": {
            "config_file": "combat_config.json",
            "key": "battle_list_roi",
            "description": "Combat battle list area",
            "default_roi": {"x": 1720, "y": 245, "width": 185, "height": 400}
        },
        "Chat": {
            "config_file": "chat_config.json",
            "key": "chat_roi",
            "description": "Chat messages area",
            "default_roi": {"x": 8, "y": 304, "width": 640, "height": 356}
        },
        "Status Icons": {
            "config_file": "condition_config.json",
            "key": "status_roi",
            "description": "Status condition icons",
            "default_roi": {"x": 1665, "y": 32, "width": 240, "height": 20}
        },
        "Depot": {
            "config_file": "depot_config.json",
            "key": "depot_roi",
            "description": "Depot container area",
            "default_roi": {"x": 1403, "y": 152, "width": 502, "height": 364}
        },
        "GM Warning": {
            "config_file": "gm_detector_config.json",
            "key": "scan_roi",
            "description": "GM detection scan area",
            "default_roi": {"x": 0, "y": 0, "width": 1920, "height": 1080}
        }
    }

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Manual ROI Capture Tool - Waypoint Navigator")
        self.root.geometry("1200x800")

        # Configure project path
        self.project_path = Path("c:/Users/gmast/Documents/frbit/waypoint-navigator")

        # Current state
        self.current_image: Optional[np.ndarray] = None
        self.current_rois: List[ROI] = []
        self.roi_selector: Optional[ROISelector] = None

        # Frame capture configuration (same as bot)
        self.frame_getter: Optional[Any] = None
        self.frame_cache: Optional[Any] = None

        # Live preview thread
        self._preview_thread: Optional[threading.Thread] = None
        self._preview_running = False

        self._init_ui()
        self._init_frame_capture()

    def _init_ui(self) -> None:
        """Initialize the user interface."""
        # Create main panes
        main_paned = ttk.PanedWindow(self.root, orient="horizontal")
        main_paned.pack(fill="both", expand=True, padx=5, pady=5)

        # Left panel - controls
        left_frame = ttk.Frame(main_paned)
        main_paned.add(left_frame, weight=1)

        # Right panel - image display
        right_frame = ttk.Frame(main_paned)
        main_paned.add(right_frame, weight=3)

        self._init_controls(left_frame)
        self._init_display(right_frame)

    def _init_controls(self, parent: ttk.Frame) -> None:
        """Initialize control panel."""
        # Capture section
        capture_frame = ttk.LabelFrame(parent, text="Image Capture from OBS Projector")
        capture_frame.pack(fill="x", padx=5, pady=5)

        ttk.Button(capture_frame, text="🎥 Capture from OBS (Live)",
                  command=self._capture_obs_frame).pack(fill="x", padx=5, pady=2)
        ttk.Button(capture_frame, text="📷 Screenshot Monitor 2",
                  command=self._capture_screenshot).pack(fill="x", padx=5, pady=2)
        ttk.Button(capture_frame, text="📁 Load Image File",
                  command=self._load_image_file).pack(fill="x", padx=5, pady=2)
        ttk.Button(capture_frame, text="🎬 Start Live Preview",
                  command=self._toggle_live_preview).pack(fill="x", padx=5, pady=2)

        # ROI Type selection
        roi_frame = ttk.LabelFrame(parent, text="ROI Type")
        roi_frame.pack(fill="x", padx=5, pady=5)

        self.roi_type_var = tk.StringVar(value="Custom")
        roi_types = ["Custom"] + list(self.ROI_PRESETS.keys())

        for roi_type in roi_types:
            ttk.Radiobutton(roi_frame, text=roi_type, variable=self.roi_type_var,
                           value=roi_type, command=self._on_roi_type_change).pack(anchor="w")

        # ROI Details
        details_frame = ttk.LabelFrame(parent, text="ROI Details")
        details_frame.pack(fill="x", padx=5, pady=5)

        # Name entry
        ttk.Label(details_frame, text="Name:").pack(anchor="w")
        self.roi_name_var = tk.StringVar()
        ttk.Entry(details_frame, textvariable=self.roi_name_var).pack(fill="x", padx=5)

        # Coordinates
        coords_frame = ttk.Frame(details_frame)
        coords_frame.pack(fill="x", padx=5, pady=2)

        ttk.Label(coords_frame, text="X:").grid(row=0, column=0)
        self.roi_x_var = tk.IntVar()
        ttk.Entry(coords_frame, textvariable=self.roi_x_var, width=8).grid(row=0, column=1, padx=2)

        ttk.Label(coords_frame, text="Y:").grid(row=0, column=2, padx=(10,0))
        self.roi_y_var = tk.IntVar()
        ttk.Entry(coords_frame, textvariable=self.roi_y_var, width=8).grid(row=0, column=3, padx=2)

        ttk.Label(coords_frame, text="W:").grid(row=1, column=0)
        self.roi_w_var = tk.IntVar()
        ttk.Entry(coords_frame, textvariable=self.roi_w_var, width=8).grid(row=1, column=1, padx=2)

        ttk.Label(coords_frame, text="H:").grid(row=1, column=2, padx=(10,0))
        self.roi_h_var = tk.IntVar()
        ttk.Entry(coords_frame, textvariable=self.roi_h_var, width=8).grid(row=1, column=3, padx=2)

        # Description
        ttk.Label(details_frame, text="Description:").pack(anchor="w", pady=(10,0))
        self.roi_desc_var = tk.StringVar()
        ttk.Entry(details_frame, textvariable=self.roi_desc_var).pack(fill="x", padx=5)

        # Actions
        actions_frame = ttk.LabelFrame(parent, text="Actions")
        actions_frame.pack(fill="x", padx=5, pady=5)

        ttk.Button(actions_frame, text="➕ Add ROI",
                  command=self._add_roi).pack(fill="x", padx=5, pady=2)
        ttk.Button(actions_frame, text="✏️ Update ROI",
                  command=self._update_roi).pack(fill="x", padx=5, pady=2)
        ttk.Button(actions_frame, text="🗑️ Delete ROI",
                  command=self._delete_roi).pack(fill="x", padx=5, pady=2)
        ttk.Button(actions_frame, text="🧪 Test ROI",
                  command=self._test_roi).pack(fill="x", padx=5, pady=2)

        # ROI List
        list_frame = ttk.LabelFrame(parent, text="Current ROIs")
        list_frame.pack(fill="both", expand=True, padx=5, pady=5)

        self.roi_listbox = tk.Listbox(list_frame)
        self.roi_listbox.pack(fill="both", expand=True, padx=5, pady=2)
        self.roi_listbox.bind("<Double-Button-1>", self._on_roi_select)

        # Save/Load
        file_frame = ttk.LabelFrame(parent, text="File Operations")
        file_frame.pack(fill="x", padx=5, pady=5)

        ttk.Button(file_frame, text="💾 Save Config",
                  command=self._save_config).pack(fill="x", padx=5, pady=2)
        ttk.Button(file_frame, text="📂 Load Config",
                  command=self._load_config).pack(fill="x", padx=5, pady=2)
        ttk.Button(file_frame, text="📤 Export All",
                  command=self._export_all).pack(fill="x", padx=5, pady=2)

    def _init_display(self, parent: ttk.Frame) -> None:
        """Initialize image display panel."""
        self.display_frame = ttk.LabelFrame(parent, text="OBS Projector View - Click and drag to select ROI")
        self.display_frame.pack(fill="both", expand=True, padx=5, pady=5)

        # Instructions
        instructions = ttk.Label(self.display_frame,
                               text="🎯 OBS ROI Capture: 1) Ensure OBS projector is on Monitor 2  2) Capture from OBS  3) Select ROI type  4) Click & drag  5) Add to list")
        instructions.pack(pady=5)

    def _init_frame_capture(self) -> None:
        """Initialize frame capture using the same system as the bot."""
        if not FRAME_CAPTURE_AVAILABLE:
            print("Warning: Bot frame capture system not available")
            return

        try:
            # Use Monitor 2 (OBS projector) - same as bot configuration
            # Monitor 2 starts at x=1920 for dual monitor setup
            assert build_frame_getter is not None
            assert FrameCache is not None
            self.frame_getter = build_frame_getter("mss", monitor_idx=2)

            # Wrap with frame cache for efficiency
            self.frame_cache = FrameCache(self.frame_getter, ttl_ms=50)

            print("✅ Frame capture initialized for Monitor 2 (OBS projector)")

        except Exception as e:
            print(f"⚠️ Frame capture initialization failed: {e}")
            print("Falling back to manual screenshot capture")
            self.frame_getter = None
            self.frame_cache = None

    def _capture_obs_frame(self) -> None:
        """Capture frame from OBS projector using bot's frame capture system."""
        if not self.frame_cache:
            messagebox.showerror("Error", "Bot frame capture not available")
            return

        try:
            # Use frame cache for efficient capture
            frame = self.frame_cache.get_frame()
            if frame is not None:
                self._load_image(frame)
                messagebox.showinfo("Success", "📹 Frame captured from OBS projector (Monitor 2)")
            else:
                messagebox.showerror("Error", "No frame available from OBS projector")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to capture OBS frame: {e}")

    def _capture_screenshot(self) -> None:
        """Capture screenshot from Monitor 2 (OBS projector)."""
        try:
            with mss.mss() as sct:
                # Capture Monitor 2 specifically (where OBS projector runs)
                # Monitor 2 starts at x=1920 in dual monitor setup
                monitor2 = {
                    "top": 0,
                    "left": 1920,  # Monitor 2 offset
                    "width": 1920,  # Assuming 1920x1080
                    "height": 1080
                }
                screenshot = sct.grab(monitor2)

                # Convert to numpy array
                img_array = np.array(screenshot)
                img_bgr = cv2.cvtColor(img_array, cv2.COLOR_BGRA2BGR)

                self._load_image(img_bgr)
                messagebox.showinfo("Success", "📷 Screenshot captured from Monitor 2 (OBS projector)")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to capture screenshot: {e}")

    def _load_image_file(self) -> None:
        """Load image from file."""
        file_path = filedialog.askopenfilename(
            title="Select Image File",
            filetypes=[
                ("Image files", "*.png *.jpg *.jpeg *.bmp *.tiff"),
                ("All files", "*.*")
            ]
        )

        if file_path:
            try:
                img = cv2.imread(file_path)
                if img is None:
                    raise ValueError("Could not load image")

                self._load_image(img)
                messagebox.showinfo("Success", f"Loaded image: {Path(file_path).name}")

            except Exception as e:
                messagebox.showerror("Error", f"Failed to load image: {e}")

    def _load_image(self, image: np.ndarray) -> None:
        """Load image into the display."""
        self.current_image = image.copy()

        # Clear existing selector
        if self.roi_selector:
            self.roi_selector.frame.destroy()

        # Create new selector
        self.roi_selector = ROISelector(self.display_frame, image)

    def _toggle_live_preview(self) -> None:
        """Toggle live preview mode."""
        if self._preview_running:
            self._stop_live_preview()
        else:
            self._start_live_preview()

    def _start_live_preview(self) -> None:
        """Start live preview thread from OBS projector."""
        if not self.frame_cache:
            messagebox.showerror("Error", "OBS frame capture not available")
            return

        self._preview_running = True
        self._preview_thread = threading.Thread(target=self._preview_worker, daemon=True)
        self._preview_thread.start()

        # Update button text
        parent_widget = self.display_frame.nametowidget(self.display_frame.winfo_parent())
        for widget in parent_widget.winfo_children():
            for child in widget.winfo_children():
                if isinstance(child, ttk.Button) and "🎬" in child["text"]:
                    child.config(text="⏹️ Stop Live Preview")
                    break

    def _stop_live_preview(self) -> None:
        """Stop live preview."""
        self._preview_running = False
        if self._preview_thread:
            self._preview_thread.join(timeout=1.0)

        # Update button text (safely, in case widgets are destroyed)
        try:
            if hasattr(self, 'display_frame') and self.display_frame.winfo_exists():
                parent = self.display_frame.winfo_parent()
                if parent:
                    parent_widget = self.display_frame.nametowidget(parent)
                    for widget in parent_widget.winfo_children():
                        for child in widget.winfo_children():
                            if isinstance(child, ttk.Button) and "⏹️" in child["text"]:
                                child.config(text="🎬 Start Live Preview")
                                break
        except (tk.TclError, AttributeError):
            # Widgets already destroyed during app shutdown - ignore
            pass

    def _preview_worker(self) -> None:
        """Live preview worker thread - captures from OBS projector."""
        while self._preview_running:
            try:
                if self.frame_cache:
                    frame = self.frame_cache.get_frame()
                    if frame is not None:
                        # Update display in main thread
                        self.root.after(0, lambda: self._load_image(frame))

                time.sleep(0.1)  # 10 FPS preview

            except Exception as e:
                print(f"Live preview error: {e}")
                break

    def _on_roi_type_change(self) -> None:
        """Handle ROI type selection change."""
        roi_type = self.roi_type_var.get()

        if roi_type in self.ROI_PRESETS:
            preset = self.ROI_PRESETS[roi_type]
            self.roi_name_var.set(roi_type)
            self.roi_desc_var.set(str(preset["description"]))

            # Load default coordinates if available
            if "default_roi" in preset:
                default = preset["default_roi"]
                assert isinstance(default, dict)
                self.roi_x_var.set(default["x"])
                self.roi_y_var.set(default["y"])
                self.roi_w_var.set(default["width"])
                self.roi_h_var.set(default["height"])
        else:
            # Custom ROI
            self.roi_name_var.set("")
            self.roi_desc_var.set("")

    def _add_roi(self) -> None:
        """Add current ROI to the list."""
        if self.roi_selector and self.roi_selector.current_roi:
            roi = self.roi_selector.current_roi
        else:
            # Create ROI from manual input
            roi = ROI(
                name="",
                x=self.roi_x_var.get(),
                y=self.roi_y_var.get(),
                width=self.roi_w_var.get(),
                height=self.roi_h_var.get()
            )

        # Update with UI values
        roi.name = self.roi_name_var.get() or f"ROI_{len(self.current_rois)+1}"
        roi.description = self.roi_desc_var.get()

        # Set config file based on ROI type
        roi_type = self.roi_type_var.get()
        if roi_type in self.ROI_PRESETS:
            roi.config_file = str(self.ROI_PRESETS[roi_type]["config_file"])

        self.current_rois.append(roi)
        self._update_roi_list()
        messagebox.showinfo("Success", f"Added ROI: {roi.name}")

    def _update_roi(self) -> None:
        """Update selected ROI."""
        selection = self.roi_listbox.curselection()
        if not selection:
            messagebox.showwarning("Warning", "No ROI selected")
            return

        idx = selection[0]
        roi = self.current_rois[idx]

        # Update with current values
        roi.name = self.roi_name_var.get() or roi.name
        roi.x = self.roi_x_var.get()
        roi.y = self.roi_y_var.get()
        roi.width = self.roi_w_var.get()
        roi.height = self.roi_h_var.get()
        roi.description = self.roi_desc_var.get()

        self._update_roi_list()
        messagebox.showinfo("Success", f"Updated ROI: {roi.name}")

    def _delete_roi(self) -> None:
        """Delete selected ROI."""
        selection = self.roi_listbox.curselection()
        if not selection:
            messagebox.showwarning("Warning", "No ROI selected")
            return

        idx = selection[0]
        roi = self.current_rois[idx]

        result = messagebox.askyesno("Confirm", f"Delete ROI '{roi.name}'?")
        if result:
            del self.current_rois[idx]
            self._update_roi_list()

    def _test_roi(self) -> None:
        """Test current ROI against live frame."""
        selection = self.roi_listbox.curselection()
        if not selection:
            if self.roi_selector and self.roi_selector.current_roi:
                roi = self.roi_selector.current_roi
            else:
                messagebox.showwarning("Warning", "No ROI to test")
                return
        else:
            idx = selection[0]
            roi = self.current_rois[idx]

        if self.current_image is None:
            messagebox.showwarning("Warning", "No image loaded")
            return

        # Extract ROI region
        roi_region = self.current_image[roi.y:roi.y+roi.height, roi.x:roi.x+roi.width]

        # Show in popup window
        self._show_roi_popup(roi, roi_region)

    def _show_roi_popup(self, roi: ROI, roi_image: np.ndarray) -> None:
        """Show ROI test popup window."""
        popup = tk.Toplevel(self.root)
        popup.title(f"ROI Test: {roi.name}")
        popup.geometry("400x300")

        # Info
        info_text = f"Name: {roi.name}\nBounds: ({roi.x}, {roi.y}) {roi.width}x{roi.height}\n{roi.description}"
        ttk.Label(popup, text=info_text).pack(pady=10)

        # Image display
        if roi_image.size > 0:
            # Convert for display
            if len(roi_image.shape) == 3:
                roi_rgb = cv2.cvtColor(roi_image, cv2.COLOR_BGR2RGB)
            else:
                roi_rgb = roi_image

            pil_image = Image.fromarray(roi_rgb)

            # Scale if needed
            max_size = 300
            if pil_image.width > max_size or pil_image.height > max_size:
                pil_image.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)

            photo = ImageTk.PhotoImage(pil_image)

            label = ttk.Label(popup, image=photo)
            setattr(label, "image", photo)
            label.pack(pady=10)
        else:
            ttk.Label(popup, text="❌ Empty ROI region").pack(pady=10)

    def _on_roi_select(self, event: Any) -> None:
        """Handle ROI list selection."""
        selection = self.roi_listbox.curselection()
        if not selection:
            return

        idx = selection[0]
        roi = self.current_rois[idx]

        # Update UI fields
        self.roi_name_var.set(roi.name)
        self.roi_x_var.set(roi.x)
        self.roi_y_var.set(roi.y)
        self.roi_w_var.set(roi.width)
        self.roi_h_var.set(roi.height)
        self.roi_desc_var.set(roi.description)

    def _update_roi_list(self) -> None:
        """Update ROI listbox."""
        self.roi_listbox.delete(0, tk.END)
        for roi in self.current_rois:
            display_text = f"{roi.name} ({roi.x},{roi.y}) {roi.width}x{roi.height}"
            self.roi_listbox.insert(tk.END, display_text)

    def _save_config(self) -> None:
        """Save current ROIs to JSON file."""
        if not self.current_rois:
            messagebox.showwarning("Warning", "No ROIs to save")
            return

        file_path = filedialog.asksaveasfilename(
            title="Save ROI Configuration",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )

        if file_path:
            try:
                config_data = {
                    "rois": [asdict(roi) for roi in self.current_rois],
                    "timestamp": time.time(),
                    "image_info": {
                        "width": self.current_image.shape[1] if self.current_image is not None else 0,
                        "height": self.current_image.shape[0] if self.current_image is not None else 0
                    }
                }

                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(config_data, f, indent=2)

                messagebox.showinfo("Success", f"Saved configuration to {Path(file_path).name}")

            except Exception as e:
                messagebox.showerror("Error", f"Failed to save config: {e}")

    def _load_config(self) -> None:
        """Load ROI configuration from JSON file."""
        file_path = filedialog.askopenfilename(
            title="Load ROI Configuration",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )

        if file_path:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    config_data = json.load(f)

                self.current_rois = []
                for roi_data in config_data.get("rois", []):
                    roi = ROI(**roi_data)
                    self.current_rois.append(roi)

                self._update_roi_list()
                messagebox.showinfo("Success", f"Loaded {len(self.current_rois)} ROIs")

            except Exception as e:
                messagebox.showerror("Error", f"Failed to load config: {e}")

    def _export_all(self) -> None:
        """Export ROIs to appropriate bot config files."""
        if not self.current_rois:
            messagebox.showwarning("Warning", "No ROIs to export")
            return

        exported_configs = set()

        try:
            for roi in self.current_rois:
                if not roi.config_file:
                    continue

                config_path = self.project_path / roi.config_file

                if config_path.exists():
                    # Load existing config
                    with open(config_path, 'r', encoding='utf-8') as f:
                        config = json.load(f)
                else:
                    # Create new config
                    config = {}

                # Determine config key
                roi_type = roi.name
                if roi_type in self.ROI_PRESETS:
                    preset = self.ROI_PRESETS[roi_type]
                    config_key = preset.get("key", "roi")
                else:
                    config_key = "roi"

                # Update config
                config[config_key] = roi.to_dict()

                # Save config
                with open(config_path, 'w', encoding='utf-8') as f:
                    json.dump(config, f, indent=2)

                exported_configs.add(roi.config_file)

            if exported_configs:
                exported_list = ", ".join(exported_configs)
                messagebox.showinfo("Success", f"Exported to: {exported_list}")
            else:
                messagebox.showinfo("Info", "No ROIs had config files specified")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to export configs: {e}")

    def run(self) -> None:
        """Start the application."""
        # Load example image if available
        example_path = self.project_path / "example_screenshot.png"
        if example_path.exists():
            try:
                img = cv2.imread(str(example_path))
                if img is not None:
                    self._load_image(img)
            except Exception:
                pass

        self.root.mainloop()

        # Cleanup
        self._stop_live_preview()


def main() -> None:
    """Launch the manual ROI capture tool."""
    app = ManualROICapture()
    app.run()


if __name__ == "__main__":
    main()