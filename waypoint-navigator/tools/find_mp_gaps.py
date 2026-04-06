"""Find exactly which columns in the MP bar lack blue across all rows."""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.frame_capture import build_frame_getter
from src.input_controller import find_window
import numpy as np

def main():
    info = find_window("Proyector")
    if not info: print("No window"); return
    getter = build_frame_getter("wgc", hwnd=info.hwnd)
    time.sleep(0.8)
    frame = None
    for _ in range(15):
        frame = getter()
        if frame is not None: break
        time.sleep(0.2)
    if frame is None: print("No frame"); return

    fh, fw = frame.shape[:2]
    sy = fh / 1080

    # MP ROI: [788, 29, 769, 13] in ref coords
    x0, y_ref, w, h_ref = 788, 29, 769, 13
    y0 = int(y_ref * sy)
    y1 = int((y_ref + h_ref) * sy)
    x1 = x0 + w
    
    print(f"Frame: {fw}×{fh}")
    print(f"MP ROI native: x={x0}..{x1}, y={y0}..{y1}")
    
    bar = frame[y0:y1, x0:x1]
    b = bar[:, :, 0].astype(np.int32)
    g = bar[:, :, 1].astype(np.int32)
    r = bar[:, :, 2].astype(np.int32)
    
    mp_mask = (b >= 100) & ((b - r) >= 40) & ((b - g) >= 20)
    
    # Find columns with NO blue pixel in any row
    cols_any = mp_mask.any(axis=0)
    empty_cols = np.where(~cols_any)[0]
    
    print(f"\nTotal columns: {w}")
    print(f"Colored columns: {cols_any.sum()}")
    print(f"Empty columns: {len(empty_cols)}")
    
    if len(empty_cols) > 0:
        print(f"\nEmpty column positions (relative to ROI start x={x0}):")
        for c in empty_cols:
            abs_x = c + x0
            # Show what color each row has at this column
            pixel_info = []
            for row in range(bar.shape[0]):
                px = bar[row, c]
                pixel_info.append(f"({px[0]:3d},{px[1]:3d},{px[2]:3d})")
            print(f"  col={c} (x={abs_x}): {' '.join(pixel_info)}")
    
    # Also check HP bar  
    hp_x0, hp_y_ref, hp_w, hp_h_ref = 12, 29, 769, 13
    hp_y0 = int(hp_y_ref * sy)
    hp_y1 = int((hp_y_ref + hp_h_ref) * sy)
    hp_bar = frame[hp_y0:hp_y1, hp_x0:hp_x0+hp_w]
    
    hb = hp_bar[:, :, 0].astype(np.int32)
    hg = hp_bar[:, :, 1].astype(np.int32)
    hr = hp_bar[:, :, 2].astype(np.int32)
    max_rg = np.maximum(hr, hg)
    max_ch = np.maximum(max_rg, hb)
    min_ch = np.minimum(np.minimum(hr, hg), hb)
    sat = max_ch - min_ch
    hp_mask = (sat >= 30) & (max_ch >= 80) & (max_rg >= hb)
    hp_cols_any = hp_mask.any(axis=0)
    hp_empty = np.where(~hp_cols_any)[0]
    
    print(f"\nHP bar: {hp_cols_any.sum()}/{hp_w} colored ({len(hp_empty)} empty)")
    if len(hp_empty) > 0:
        for c in hp_empty:
            abs_x = c + hp_x0
            pixel_info = []
            for row in range(hp_bar.shape[0]):
                px = hp_bar[row, c]
                pixel_info.append(f"({px[0]:3d},{px[1]:3d},{px[2]:3d})")
            print(f"  col={c} (x={abs_x}): {' '.join(pixel_info)}")
    
    # Try even narrower MP widths
    print("\n=== Fine-tuning MP width ===")
    for test_w in [769, 768, 767, 766, 765, 764, 763, 762, 761, 760, 755, 750]:
        bar_t = frame[y0:y1, x0:x0+test_w]
        bt = bar_t[:, :, 0].astype(np.int32)
        gt = bar_t[:, :, 1].astype(np.int32)
        rt = bar_t[:, :, 2].astype(np.int32)
        mp_t = (bt >= 100) & ((bt - rt) >= 40) & ((bt - gt) >= 20)
        cols = mp_t.any(axis=0).sum()
        pct = min(100, int(cols * 100 // test_w))
        print(f"  w={test_w}: {cols}/{test_w} cols colored → {pct}%")

    getter.close()

if __name__ == "__main__":
    main()
