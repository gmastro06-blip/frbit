"""
Save individual zoomed crops of each text detected by EasyOCR in the right panel.
User will verify each detection is correct.
"""
import sys, time, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import numpy as np
import easyocr
from src.frame_capture import build_frame_getter
from src.input_controller import find_window

def main():
    info = find_window("Proyector")
    if not info:
        print("Window not found"); return
    getter = build_frame_getter("wgc", hwnd=info.hwnd)
    time.sleep(0.8)
    frame = None
    for _ in range(15):
        frame = getter()
        if frame is not None: break
        time.sleep(0.2)
    if frame is None:
        print("No frame"); return
    
    fh, fw = frame.shape[:2]
    print(f"Frame: {fw}x{fh}")
    
    out = Path("output/ocr_texts")
    out.mkdir(parents=True, exist_ok=True)
    
    # Save raw frame
    cv2.imwrite(str(out / "full_frame.png"), frame)
    
    # OCR the right panel
    reader = easyocr.Reader(['en'], gpu=False, verbose=False)
    
    # Scan right panel y=0..600 at 4x
    panel = frame[0:600, 1695:fw]
    ph, pw = panel.shape[:2]
    scale = 4
    panel_big = cv2.resize(panel, (pw*scale, ph*scale), interpolation=cv2.INTER_CUBIC)
    
    results = reader.readtext(panel_big)
    
    # Create master annotated image
    vis = frame.copy()
    
    print(f"\n{'#':>3} {'Text':>20} {'Conf':>6} {'Position':>25} {'Size':>10}")
    print("-" * 75)
    
    for i, (bbox, text, conf) in enumerate(results):
        # Convert from scaled coords back to frame coords
        pts_panel = [(int(p[0]/scale), int(p[1]/scale)) for p in bbox]
        pts_frame = [(p[0] + 1695, p[1]) for p in pts_panel]
        
        x1 = min(p[0] for p in pts_frame)
        y1 = min(p[1] for p in pts_frame)
        x2 = max(p[0] for p in pts_frame)
        y2 = max(p[1] for p in pts_frame)
        
        w = x2 - x1
        h = y2 - y1
        
        print(f"{i+1:3d} {text:>20} {conf:6.2f}   ({x1},{y1})-({x2},{y2})   {w}x{h}")
        
        # Crop with padding
        pad = 5
        cx1 = max(0, x1 - pad)
        cy1 = max(0, y1 - pad)
        cx2 = min(fw, x2 + pad)
        cy2 = min(fh, y2 + pad)
        
        crop = frame[cy1:cy2, cx1:cx2]
        
        # Save at 8x zoom for easy viewing
        zoom = 8
        crop_big = cv2.resize(crop, (crop.shape[1]*zoom, crop.shape[0]*zoom),
                              interpolation=cv2.INTER_NEAREST)
        
        # Add label
        label = f"#{i+1}: '{text}' conf={conf:.2f} @ ({x1},{y1})"
        cv2.putText(crop_big, label, (5, 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        
        fname = f"{i+1:02d}_{text.replace(' ', '_').replace(':', '')[:15]}.png"
        cv2.imwrite(str(out / fname), crop_big)
        
        # Draw on master image
        cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 255, 0), 1)
        cv2.putText(vis, f"#{i+1}:{text}", (x1, max(0, y1-3)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 0), 1)
    
    # Save master annotated
    cv2.imwrite(str(out / "00_annotated_full.png"), vis)
    
    # Also save zoomed right panel with annotations
    vis_panel = vis[0:600, 1680:fw]
    vis_panel_3x = cv2.resize(vis_panel, (vis_panel.shape[1]*3, vis_panel.shape[0]*3),
                               interpolation=cv2.INTER_NEAREST)
    cv2.imwrite(str(out / "00_annotated_panel_3x.png"), vis_panel_3x)
    
    print(f"\n{len(results)} text regions saved to {out}/")
    print(f"  00_annotated_full.png     — full frame with all boxes")
    print(f"  00_annotated_panel_3x.png — right panel zoomed 3x")
    print(f"  01_xxx.png ... {len(results):02d}_xxx.png — individual crops at 8x")
    
    getter.close()

if __name__ == "__main__":
    main()
