"""
MonitorGui
----------
Tkinter-based live monitor window for a running :class:`BotSession`.

Shows real-time stats (routes completed, heals, mana potions, loot events,
active conditions, watchdog alerts) read from the session's
:class:`~src.event_bus.EventBus` and updated via periodic polling.

Usage
-----
::

    from src.session import BotSession, SessionConfig
    from src.monitor_gui import MonitorGui

    session = BotSession(SessionConfig(route_file="routes/hunt.json"))
    gui = MonitorGui(session=session)
    gui.run()          # blocks until the window is closed

Or via the convenience helper on the session::

    session.open_monitor()

Testing
-------
Pass a :class:`unittest.mock.MagicMock` as *root* to avoid creating a real
Tk window.  All logic methods work against the internal ``_state`` dict and
never require a display.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

_log = logging.getLogger("wn.gui")


# ── Config ────────────────────────────────────────────────────────────────────

@dataclass
class MonitorConfig:
    """Appearance and update-rate settings for the monitor window."""

    title:       str = "Navigator Monitor"
    geometry:    str = "460x920"
    refresh_ms:  int = 1000   # polling interval in milliseconds


# ── Monitor window ───────────────────────────────────────────────────────────

class MonitorGui:
    """
    Real-time monitor window for a :class:`~src.session.BotSession`.

    Parameters
    ----------
    session : BotSession
        The session to observe.
    config : MonitorConfig, optional
        Window title, size and polling interval.
    root : object, optional
        Tkinter root window.  Created automatically when *None*.
        Pass a :class:`unittest.mock.MagicMock` in tests.
    """

    def __init__(
        self,
        session: Any,
        config: Optional[MonitorConfig] = None,
        root: Optional[Any] = None,
    ) -> None:
        self._session = session
        self._cfg     = config or MonitorConfig()
        self._root    = root          # None → created inside build()
        self._built   = False
        self._svars:  Dict[str, Any] = {}     # tk.StringVar instances
        self._lock    = threading.Lock()

        # Minimap canvas + PhotoImage reference (kept to avoid GC)
        self._minimap_canvas: Optional[Any] = None
        self._minimap_photo:  Optional[Any] = None

        # Toggle switches (GUI-side; also attempt to influence session subsystems)
        self._targeting_on: bool = True
        self._walking_on:   bool = True
        self._looting_on:   bool = True

        # Log widget reference + lines buffer
        self._log_text:  Optional[Any] = None
        self._log_lines: list[str] = []

        # Toggle button references (for dynamic bg colouring)
        self._btn_targeting: Optional[Any] = None
        self._btn_walking:   Optional[Any] = None
        self._btn_looting:   Optional[Any] = None
        self._btn_config:    Optional[Any] = None

        # Active config Toplevel (prevent multiple windows)
        self._config_win: Optional[Any] = None

        # Internal state — updated by both events and polling
        self._state: Dict[str, Any] = {
            "route":         "—",
            "current_wpt":   "—",
            "uptime":        "0s",
            "is_running":    False,
            "routes":        0,
            "heals":         0,
            "mana":          0,
            "loot":          0,
            "kills":         0,
            "conditions":    set(),
            "last_watchdog": "—",
            "last_depot":    "—",
            "break_info":    "—",
            "soak_mem":      "—",
        }

    # ── Read-only properties ─────────────────────────────────────────────────

    @property
    def is_built(self) -> bool:
        """True after :meth:`build` has been called."""
        return self._built

    @property
    def has_session(self) -> bool:
        """True when a session is attached."""
        return self._session is not None

    @property
    def state(self) -> Dict[str, Any]:
        """Return a shallow copy of the internal state dict (thread-safe)."""
        with self._lock:
            snap = dict(self._state)
            snap["conditions"] = set(self._state["conditions"])
            return snap

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def build(self) -> None:
        """
        Create the Tk root (when not injected), build all widgets, subscribe
        to the session's EventBus, and schedule the first poll.

        Must be called from the main thread.  Subsequent calls are no-ops.
        """
        if self._built:
            return

        try:
            import tkinter as tk
        except ImportError as exc:
            raise RuntimeError(
                "tkinter is required for MonitorGui — "
                "install it (python3-tk) or use MonitorGui(root=mock) in tests."
            ) from exc

        if self._root is None:
            self._root = tk.Tk()

        self._root.title(self._cfg.title)
        self._root.geometry(self._cfg.geometry)
        self._root.resizable(False, False)

        # Create one StringVar per display field
        for key in (
            "route", "current_wpt",
            "uptime", "is_running", "routes", "heals", "mana",
            "loot", "kills", "conditions", "last_watchdog", "last_depot",
            "break_info", "soak_mem",
            "targeting_lbl", "walker_lbl", "looting_lbl",
        ):
            self._svars[key] = tk.StringVar(master=self._root, value="…")

        self._build_layout(tk)
        self._subscribe_events()

        self._built = True
        self._root.after(self._cfg.refresh_ms, self._poll)

    def run(self) -> None:
        """Build the window and enter the Tk mainloop (blocks until closed)."""
        self.build()
        assert self._root is not None
        self._root.mainloop()

    def close(self) -> None:
        """Destroy the Tk window (safe to call before :meth:`build`)."""
        if self._root is not None:
            try:
                self._root.destroy()
            except Exception:
                _log.debug("Monitor GUI failed to destroy root window", exc_info=True)

    # ── Layout ───────────────────────────────────────────────────────────────

    #: Pixel size of the minimap preview canvas
    _MINI_W: int = 260
    _MINI_H: int = 260

    # ── Dark theme palette ────────────────────────────────────────────────────
    _BG       = "#1e1e2e"
    _FG       = "#cdd6f4"
    _BTN_BG   = "#313244"
    _BTN_ACT  = "#45475a"
    _CLR_ON   = "#a6e3a1"   # green  — feature enabled
    _CLR_OFF  = "#f38ba8"   # red    — feature disabled
    _CLR_STAT = "#89dceb"   # cyan   — stat label
    _CLR_WPT  = "#89b4fa"   # blue   — WPT label

    def _build_layout(self, tk: Any) -> None:  # noqa: C901
        if self._root is None:
            return

        BG      = self._BG
        FG      = self._FG
        BTN_BG  = self._BTN_BG
        BTN_ACT = self._BTN_ACT

        self._root.configure(bg=BG)

        # ── Header: toggle status badges ──────────────────────────────────
        hdr = tk.Frame(self._root, bg=BG, pady=4)
        hdr.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10)
        for col, (badge, var_key) in enumerate([
            ("Targeting", "targeting_lbl"),
            ("Walker",    "walker_lbl"),
            ("Looting",   "looting_lbl"),
        ]):
            tk.Label(hdr, text=badge + ":", fg=FG, bg=BG,
                     font=("TkDefaultFont", 9, "bold"),
                     ).grid(row=0, column=col * 2, sticky="w", padx=(6, 0))
            tk.Label(hdr, textvariable=self._svars[var_key], width=4,
                     fg=self._CLR_ON, bg=BG,
                     font=("TkDefaultFont", 9, "bold"),
                     ).grid(row=0, column=col * 2 + 1, sticky="w")

        # ── Current WPT label ──────────────────────────────────────────────
        wpt_frm = tk.Frame(self._root, bg=BG)
        wpt_frm.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10,
                     pady=(0, 2))
        tk.Label(wpt_frm, text="WPT actual:", fg=self._CLR_WPT, bg=BG,
                 font=("TkDefaultFont", 9, "bold")).pack(side="left")
        tk.Label(wpt_frm, textvariable=self._svars["current_wpt"], fg=FG,
                 bg=BG, font=("TkFixedFont", 9)).pack(side="left", padx=4)

        # ── Log widget ────────────────────────────────────────────────────
        log_frm = tk.Frame(self._root, bg="#181825", bd=1, relief="sunken")
        log_frm.grid(row=2, column=0, columnspan=2, sticky="ew",
                     padx=8, pady=(0, 4))
        self._log_text = tk.Text(
            log_frm, height=6, width=54,
            bg="#181825", fg="#a6adc8",
            font=("TkFixedFont", 8),
            state="disabled", relief="flat", wrap="none",
        )
        assert self._log_text is not None
        _sb = tk.Scrollbar(log_frm, command=self._log_text.yview)
        self._log_text.configure(yscrollcommand=_sb.set)
        self._log_text.pack(side="left", fill="x", expand=True)
        _sb.pack(side="right", fill="y")

        # ── Compact stats (two rows) ───────────────────────────────────────
        _stat_grid: list[list[tuple[str, str]]] = [
            [("Ruta", "route"), ("Uptime", "uptime"), ("Running", "is_running")],
            [("Routes", "routes"), ("Heals", "heals"),
             ("Mana", "mana"), ("Kills", "kills")],
            [("Breaks", "break_info"), ("Memory", "soak_mem")],
        ]
        for r_off, row_items in enumerate(_stat_grid):
            sf = tk.Frame(self._root, bg=BG)
            sf.grid(row=3 + r_off, column=0, columnspan=2, sticky="ew",
                    padx=10, pady=1)
            for ci, (lbl, key) in enumerate(row_items):
                tk.Label(sf, text=lbl + ":", fg=self._CLR_STAT, bg=BG,
                         font=("TkDefaultFont", 8),
                         ).grid(row=0, column=ci * 2, sticky="w", padx=(4, 0))
                tk.Label(sf, textvariable=self._svars[key], fg=FG, bg=BG,
                         font=("TkDefaultFont", 8), width=7,
                         ).grid(row=0, column=ci * 2 + 1, sticky="w")

        # ── Toggle buttons ────────────────────────────────────────────────
        _toggle_rows = [
            ("Toggle Targeting", self._on_toggle_targeting, "_btn_targeting"),
            ("Toggle Walking",   self._on_toggle_walking,   "_btn_walking"),
            ("Toggle Loot",      self._on_toggle_loot,      "_btn_looting"),
        ]
        for r_off, (label, command, attr) in enumerate(_toggle_rows):
            btn = tk.Button(
                self._root, text=label, command=command, width=38,
                bg=BTN_BG, fg=FG, relief="flat", activebackground=BTN_ACT,
            )
            btn.grid(
                row=6 + r_off,
                column=0,
                columnspan=2,
                padx=8,
                pady=2,
                sticky="ew",
            )
            setattr(self, attr, btn)

        # ── Action buttons ────────────────────────────────────────────────
        tk.Button(
            self._root, text="📂 Cargar ruta",
            command=self._on_load_route_click,
            width=18, bg=BTN_BG, fg=FG, relief="flat",
            activebackground=BTN_ACT,
        ).grid(row=9, column=0, padx=(8, 2), pady=2, sticky="ew")
        tk.Button(
            self._root, text="📍 Print posición",
            command=self._on_print_position,
            width=18, bg=BTN_BG, fg=FG, relief="flat",
            activebackground=BTN_ACT,
        ).grid(row=9, column=1, padx=(2, 8), pady=2, sticky="ew")

        # ── Config button (full width) ─────────────────────────────────────
        self._btn_config = tk.Button(
            self._root, text="⚙ Configurar",
            command=self._open_config_window,
            width=38, bg=BTN_BG, fg=FG, relief="flat",
            activebackground=BTN_ACT,
            font=("TkDefaultFont", 9),
        )
        assert self._btn_config is not None
        self._btn_config.grid(row=10, column=0, columnspan=2,
                              padx=8, pady=2, sticky="ew")

        tk.Button(
            self._root, text="▶ Start", command=self._on_start_click,
            width=16, bg=self._CLR_ON, fg="#1e1e2e",
            font=("TkDefaultFont", 9, "bold"), relief="flat",
        ).grid(row=11, column=0, padx=(8, 2), pady=6, sticky="ew")
        tk.Button(
            self._root, text="⏹ Stop", command=self._on_stop_click,
            width=16, bg=self._CLR_OFF, fg="#1e1e2e",
            font=("TkDefaultFont", 9, "bold"), relief="flat",
        ).grid(row=11, column=1, padx=(2, 8), pady=6, sticky="ew")

        # ── Minimap ───────────────────────────────────────────────────────
        tk.Label(
            self._root, text="Mapa de walkability (tiles)",
            font=("TkDefaultFont", 9, "bold"), anchor="center",
            fg=FG, bg=BG,
        ).grid(row=12, column=0, columnspan=2, pady=(4, 0))

        canvas = tk.Canvas(
            self._root,
            width=self._MINI_W,
            height=self._MINI_H,
            bg="#1a1a2e",
            highlightthickness=1,
            highlightbackground="#444",
        )
        canvas.grid(row=13, column=0, columnspan=2, padx=8, pady=(2, 8))
        canvas.create_text(
            self._MINI_W // 2, self._MINI_H // 2,
            text="sin mapa",
            fill="#888",
            font=("TkDefaultFont", 10),
            tags="placeholder",
        )
        self._minimap_canvas = canvas

    # ── Event subscriptions ──────────────────────────────────────────────────

    _EVENT_MAP = {
        "route_done":      "_on_route_done",
        "depot_done":      "_on_depot_done",
        "condition":       "_on_condition",
        "condition_clear": "_on_condition_clear",
        "watchdog":        "_on_watchdog",
        "heal":            "_on_heal",
        "mana":            "_on_mana",
        "kill":            "_on_kill",
    }

    def _subscribe_events(self) -> None:
        bus = self._session.event_bus
        for event, method_name in self._EVENT_MAP.items():
            bus.subscribe(event, getattr(self, method_name))

    # ── Event handlers ───────────────────────────────────────────────────────

    def _on_route_done(self, data: Any) -> None:
        with self._lock:
            self._state["routes"] = data.get("routes_completed", self._state["routes"])

    def _on_depot_done(self, data: Any) -> None:
        ok     = data.get("success", False)
        cycles = data.get("cycles", "?")
        with self._lock:
            self._state["last_depot"] = f"cycle {cycles} ({'ok' if ok else 'fail'})"

    def _on_condition(self, data: Any) -> None:
        cond = data.get("condition", "")
        if cond:
            with self._lock:
                self._state["conditions"].add(cond)

    def _on_condition_clear(self, data: Any) -> None:
        cond = data.get("condition", "")
        with self._lock:
            self._state["conditions"].discard(cond)

    def _on_watchdog(self, data: Any) -> None:
        idle = data.get("idle_seconds", data.get("idle_secs", "?"))
        with self._lock:
            self._state["last_watchdog"] = f"{idle}s idle"

    def _on_heal(self, data: Any) -> None:
        with self._lock:
            self._state["heals"] += 1

    def _on_mana(self, data: Any) -> None:
        with self._lock:
            self._state["mana"] += 1

    def _on_kill(self, data: Any) -> None:
        with self._lock:
            self._state["kills"] += 1

    # ── Button callbacks ─────────────────────────────────────────────────────

    def _on_start_click(self) -> None:
        try:
            self._session.start()
        except Exception:
            _log.debug("Monitor GUI failed to start session", exc_info=True)

    def _on_stop_click(self) -> None:
        try:
            self._session.stop()
        except Exception:
            _log.debug("Monitor GUI failed to stop session", exc_info=True)

    def _on_load_route_click(self) -> None:
        """Open a file-picker, swap the route on the session, and restart."""
        try:
            import tkinter.filedialog as fd
            from pathlib import Path
            from src.config_paths import ROUTES_DIR
            routes_dir = str(ROUTES_DIR)
            path = fd.askopenfilename(
                title="Seleccionar ruta",
                initialdir=routes_dir,
                filetypes=[("JSON routes", "*.json"), ("All files", "*.*")],
            )
            if not path:
                return
            # Stop → swap route → reset counters → restart
            try:
                self._session.stop()
            except Exception:
                _log.debug("Monitor GUI failed to stop session before route swap", exc_info=True)
            self._session.config.route_file = path
            # Reset cached position so _poll bootstraps from the new route
            try:
                self._session._position = None
            except Exception:
                _log.debug("Monitor GUI failed to clear cached position during route swap", exc_info=True)
            try:
                self._session.reset_stats()
            except Exception:
                _log.debug("Monitor GUI failed to reset stats during route swap", exc_info=True)
            with self._lock:
                self._state["routes"] = 0
                self._state["heals"]  = 0
                self._state["mana"]   = 0
                self._state["loot"]   = 0
                self._state["kills"]  = 0
                self._state["conditions"] = set()
                self._state["last_watchdog"] = "—"
                self._state["last_depot"]    = "—"
                self._state["route"] = Path(path).name
            try:
                self._session.start()
            except Exception:
                _log.debug("Monitor GUI failed to restart session after route swap", exc_info=True)
        except Exception:
            _log.debug("Monitor GUI route picker flow failed", exc_info=True)

    # ── Polling ──────────────────────────────────────────────────────────────

    def _open_config_window(self) -> None:  # noqa: C901
        """Open a secondary Toplevel window to edit and save bot configuration.

        Editable sections:
        - **Healer** — HP/MP percentage thresholds and hotkey VK codes
          (reads/writes ``heal_config.json`` via :class:`~src.healer.HealConfig`)
        - **Sesión** — step interval, start delay, loop-route toggle
          (updates the live ``SessionConfig`` on the attached session and also
          writes ``session_config.json`` for persistence)
        """
        # Guard: raise existing window instead of creating a duplicate
        if self._config_win is not None:
            try:
                self._config_win.lift()
                self._config_win.focus_force()
                return
            except Exception:
                _log.debug("Monitor GUI failed to focus existing config window", exc_info=True)
                self._config_win = None

        try:
            import tkinter as tk
            import tkinter.ttk as ttk
        except ImportError:
            return

        if self._root is None:
            return

        win = tk.Toplevel(self._root)
        self._config_win = win

        def _close_config_win() -> None:
            self._config_win = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", _close_config_win)
        win.title("⚙ Configurar Bot")
        win.geometry("420x540")
        win.resizable(False, False)
        win.configure(bg=self._BG)

        BG    = self._BG
        FG    = self._FG
        BTN_BG = self._BTN_BG
        BTN_ACT = self._BTN_ACT

        # ── Load initial values ───────────────────────────────────────────
        try:
            from src.healer import HealConfig
            heal_cfg = HealConfig.load()
        except Exception:
            from src.healer import HealConfig
            heal_cfg = HealConfig()

        sess_cfg = getattr(self._session, "config", None)
        try:
            from src.session import SessionConfig
            if sess_cfg is None or not hasattr(sess_cfg, "step_interval"):
                sess_cfg = SessionConfig()
        except Exception:
            sess_cfg = None

        # ── Tk variables ─────────────────────────────────────────────────
        v_hp_thr   = tk.IntVar(value=heal_cfg.hp_threshold_pct)
        v_hp_emer  = tk.IntVar(value=heal_cfg.hp_emergency_pct)
        v_mp_thr   = tk.IntVar(value=heal_cfg.mp_threshold_pct)
        v_heal_vk  = tk.IntVar(value=heal_cfg.heal_hotkey_vk)
        v_mana_vk  = tk.IntVar(value=heal_cfg.mana_hotkey_vk)
        v_emer_vk  = tk.IntVar(value=heal_cfg.emergency_hotkey_vk)

        v_step_int  = tk.DoubleVar(value=getattr(sess_cfg, "step_interval", 0.45))
        v_start_del = tk.DoubleVar(value=getattr(sess_cfg, "start_delay",   3.0))
        v_loop      = tk.BooleanVar(value=getattr(sess_cfg, "loop_route",   False))

        # ── Status label ──────────────────────────────────────────────────
        v_status = tk.StringVar(value="")

        # ── Helper: section header ────────────────────────────────────────
        def _section(parent: Any, label: str, row: int) -> None:
            tk.Label(parent, text=label, fg=self._CLR_STAT, bg=BG,
                     font=("TkDefaultFont", 9, "bold")).grid(
                row=row, column=0, columnspan=3, sticky="w", padx=8, pady=(8, 2))

        # ── Helper: slider row ────────────────────────────────────────────
        def _slider(parent: Any, label: str, var: Any, row: int,
                    lo: int = 0, hi: int = 100) -> None:
            tk.Label(parent, text=label, fg=FG, bg=BG,
                     font=("TkDefaultFont", 8), width=22, anchor="w").grid(
                row=row, column=0, sticky="w", padx=(12, 0))
            sc = tk.Scale(parent, from_=lo, to=hi, orient="horizontal",
                          variable=var, length=180,
                          bg=BG, fg=FG, troughcolor=BTN_BG,
                          highlightthickness=0, relief="flat",
                          font=("TkDefaultFont", 7))
            sc.grid(row=row, column=1, sticky="ew", padx=4)
            tk.Label(parent, textvariable=var, fg=self._CLR_ON, bg=BG,
                     width=4, font=("TkFixedFont", 8)).grid(
                row=row, column=2, sticky="w")

        # ── Helper: spinbox row ───────────────────────────────────────────
        def _spinbox(parent: Any, label: str, var: Any, row: int,
                     lo: float, hi: float, inc: float = 1.0) -> None:
            tk.Label(parent, text=label, fg=FG, bg=BG,
                     font=("TkDefaultFont", 8), width=22, anchor="w").grid(
                row=row, column=0, sticky="w", padx=(12, 0))
            sb = ttk.Spinbox(parent, textvariable=var,
                             from_=lo, to=hi, increment=inc, width=8)
            sb.grid(row=row, column=1, sticky="w", padx=4)

        # ── Helper: checkbox row ──────────────────────────────────────────
        def _check(parent: Any, label: str, var: Any, row: int) -> None:
            tk.Checkbutton(parent, text=label, variable=var,
                           fg=FG, bg=BG, selectcolor=BTN_BG,
                           activeforeground=FG, activebackground=BG,
                           font=("TkDefaultFont", 8)).grid(
                row=row, column=0, columnspan=3, sticky="w", padx=(12, 0))

        # ── Build rows ────────────────────────────────────────────────────
        _r = 0

        _section(win, "─── Healer ────────────────────────────────────", _r); _r += 1
        _slider(win, "HP threshold %",   v_hp_thr,  _r); _r += 1
        _slider(win, "HP emergency %",   v_hp_emer, _r); _r += 1
        _slider(win, "MP threshold %",   v_mp_thr,  _r); _r += 1
        _spinbox(win, "Heal VK (hex 0-255)",    v_heal_vk, _r, 0, 255); _r += 1
        _spinbox(win, "Mana VK (hex 0-255)",    v_mana_vk, _r, 0, 255); _r += 1
        _spinbox(win, "Emergency VK (0-255)",   v_emer_vk, _r, 0, 255); _r += 1

        _section(win, "─── Sesión ───────────────────────────────────", _r); _r += 1
        _spinbox(win, "step_interval (s)",  v_step_int,  _r, 0.1, 2.0, 0.05); _r += 1
        _spinbox(win, "start_delay (s)",    v_start_del, _r, 0.0, 30.0, 0.5); _r += 1
        _check(win, "loop_route", v_loop, _r); _r += 1

        # ── Save button ───────────────────────────────────────────────────
        def _do_save() -> None:
            try:
                import dataclasses as _dc
                new_heal = _dc.replace(
                    heal_cfg,
                    hp_threshold_pct    = int(v_hp_thr.get()),
                    hp_emergency_pct    = int(v_hp_emer.get()),
                    mp_threshold_pct    = int(v_mp_thr.get()),
                    heal_hotkey_vk      = int(v_heal_vk.get()),
                    mana_hotkey_vk      = int(v_mana_vk.get()),
                    emergency_hotkey_vk = int(v_emer_vk.get()),
                )
                new_heal.validate()
                new_heal.save()
            except Exception as exc:
                v_status.set(f"✗ Error HealConfig: {exc}")
                return

            try:
                if sess_cfg is not None:
                    sess_cfg.step_interval = round(float(v_step_int.get()), 3)
                    sess_cfg.start_delay   = round(float(v_start_del.get()), 3)
                    sess_cfg.loop_route    = bool(v_loop.get())
                    sess_cfg.save()            # persist to session_config.json
            except Exception as exc:
                v_status.set(f"✗ Error SessionConfig: {exc}")
                return

            v_status.set("✓ Guardado")
            self.append_log("⚙ Configuración guardada desde GUI")

        tk.Button(
            win, text="💾 Guardar", command=_do_save,
            width=14, bg=self._CLR_ON, fg="#1e1e2e",
            font=("TkDefaultFont", 9, "bold"), relief="flat",
        ).grid(row=_r, column=0, columnspan=2, pady=10, padx=8, sticky="w")

        tk.Label(win, textvariable=v_status, fg=self._CLR_ON, bg=BG,
                 font=("TkDefaultFont", 8)).grid(
            row=_r, column=2, sticky="w")

    def _build_polled_state(self) -> Dict[str, Any]:
        if self._session is None:
            return {}

        snap = self._read_session_snapshot()
        with self._lock:
            current = dict(self._state)

        raw_uptime = snap.get("uptime_seconds", snap.get("uptime_secs"))
        state_update: Dict[str, Any] = {
            "is_running": snap.get("is_running", current["is_running"]),
            "routes": snap.get("routes_completed", snap.get("routes", current["routes"])),
            "heals": snap.get("heal_fired", snap.get("heals", current["heals"])),
            "mana": snap.get("mana_fired", snap.get("mana", current["mana"])),
            "loot": snap.get("loot_events", snap.get("loot", current["loot"])),
            "uptime": self._format_uptime(float(raw_uptime or 0)),
        }

        route_name = snap.get("route_name") or snap.get("route") or self._read_route_name()
        if route_name is not None:
            state_update["route"] = route_name

        break_info = snap.get("break_info") or self._format_break_info(snap.get("break_scheduler"))
        if break_info is not None:
            state_update["break_info"] = break_info

        soak_mem = snap.get("soak_mem") or self._format_soak_mem(snap.get("soak_monitor"))
        if soak_mem is not None:
            state_update["soak_mem"] = soak_mem

        if "current_wpt" in snap:
            state_update["current_wpt"] = str(snap.get("current_wpt") or "—")
        else:
            self._bootstrap_session_position()
            state_update["current_wpt"] = self._read_current_wpt(
                is_running=bool(state_update["is_running"]),
                previous_wpt=str(current.get("current_wpt", "—")),
            )
        return state_update

    def _read_session_snapshot(self) -> Dict[str, Any]:
        if self._session is None:
            return {}

        monitor_snapshot = self._session_method("monitor_snapshot")
        if monitor_snapshot is not None:
            try:
                snapshot = monitor_snapshot()
                if isinstance(snapshot, dict):
                    return snapshot
            except Exception:
                _log.debug("[GUI] monitor snapshot read error", exc_info=True)

        stats_snapshot = getattr(self._session, "stats_snapshot", None)
        if callable(stats_snapshot):
            snapshot = stats_snapshot()
            if isinstance(snapshot, dict):
                return snapshot
        return {}

    def _session_method(self, name: str) -> Optional[Any]:
        if self._session is None:
            return None

        method = getattr(type(self._session), name, None)
        if not callable(method):
            return None
        return lambda *args, **kwargs: method(self._session, *args, **kwargs)

    @staticmethod
    def _format_uptime(raw_uptime: float) -> str:
        if raw_uptime >= 3600:
            return f"{raw_uptime / 3600:.1f}h"
        if raw_uptime >= 60:
            return f"{raw_uptime / 60:.0f}m"
        return f"{raw_uptime:.0f}s"

    def _read_route_name(self) -> Optional[str]:
        try:
            config = getattr(self._session, "config", None)
            route_file = getattr(config, "route_file", "")
            if not isinstance(route_file, str) or not route_file:
                return None
            route_file_str: str = route_file
            return Path(route_file_str).name
        except Exception as exc:
            _log.debug("[GUI] route name read error: %s", exc)
        return None

    @staticmethod
    def _format_break_info(break_scheduler: Any) -> Optional[str]:
        if not isinstance(break_scheduler, dict):
            return None
        if break_scheduler.get("on_break"):
            return "⏸ ON BREAK"
        next_break = break_scheduler.get("next_break_in_m", 0)
        breaks_taken = break_scheduler.get("breaks_taken", 0)
        return f"next {next_break:.0f}m | #{breaks_taken}"

    @staticmethod
    def _format_soak_mem(soak_monitor: Any) -> Optional[str]:
        if not isinstance(soak_monitor, dict):
            return None
        latest = soak_monitor.get("latest", {})
        rss_mb = latest.get("rss_mb", 0)
        peak_memory = soak_monitor.get("peak_memory_mb", 0)
        if rss_mb:
            return f"{rss_mb:.0f}MB (peak {peak_memory:.0f})"
        if peak_memory:
            return f"peak {peak_memory:.0f}MB"
        return None

    def _bootstrap_session_position(self) -> None:
        try:
            if getattr(self._session, "_position", None) is not None:
                return

            config = getattr(self._session, "config", None)
            route_file = getattr(config, "route_file", "")
            if not isinstance(route_file, str) or not route_file:
                return
            route_file_str: str = route_file

            route_path = Path(route_file_str)
            if not route_path.exists():
                from src.config_paths import ROUTES_DIR as _routes_dir

                route_path = _routes_dir / route_file_str
            if not route_path.exists():
                return

            route_data = json.loads(route_path.read_text("utf-8"))
            start_coord = self._extract_start_coord(route_data)
            if start_coord is None:
                return

            from .models import Coordinate as _coord

            self._session._position = _coord(
                x=int(start_coord["x"]),
                y=int(start_coord["y"]),
                z=int(start_coord["z"]),
            )
        except Exception as exc:
            _log.debug("[GUI] position bootstrap error: %s", exc)

    @staticmethod
    def _extract_start_coord(route_data: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(route_data, dict):
            return None

        start_coord = route_data.get("_meta", {}).get("start_coord")
        if isinstance(start_coord, dict):
            return start_coord

        start_coord = route_data.get("start")
        if isinstance(start_coord, dict):
            return start_coord

        waypoints = route_data.get("waypoints", [])
        if waypoints and isinstance(waypoints[0], dict):
            return waypoints[0]
        return None

    def _read_current_wpt(self, is_running: bool, previous_wpt: str) -> str:
        try:
            executor = getattr(self._session, "_executor", None)
            if executor is not None:
                live_pos = getattr(executor, "_current_pos", None)
                if live_pos is not None:
                    self._session._position = live_pos

            instruction = getattr(executor, "_current_instr", None) if executor else None
            if instruction is None:
                return previous_wpt if is_running else "—"

            movement_label = self._format_movement_wpt(instruction)
            if movement_label is not None:
                return movement_label
            return self._format_action_wpt(instruction)
        except Exception as exc:
            _log.debug("[GUI] executor sync error: %s", exc)
            return previous_wpt if is_running else "—"

    @staticmethod
    def _format_movement_wpt(instruction: Any) -> Optional[str]:
        coord = getattr(instruction, "coord", None)
        if coord is None:
            return None

        kind = getattr(instruction, "kind", "?")
        return (
            f"[{kind}  "
            f"{getattr(coord, 'x', '')},"
            f"{getattr(coord, 'y', '')},"
            f"{getattr(coord, 'z', '')}]"
        )

    @staticmethod
    def _format_action_wpt(instruction: Any) -> str:
        kind = getattr(instruction, "kind", "?")
        action = getattr(instruction, "action", None) or kind
        extra = ""
        if kind == "wait":
            extra = f" {getattr(instruction, 'wait_secs', '')}s"
        elif kind == "goto":
            extra = f" → {getattr(instruction, 'label_jump', '')}"
        elif kind == "label":
            extra = f" :{getattr(instruction, 'label', '')}"
        elif kind == "if_stat":
            extra = (
                f" {getattr(instruction, 'stat', '')}"
                f"{getattr(instruction, 'op', '')}"
                f"{getattr(instruction, 'threshold', '')}"
            )
        elif kind == "use_item":
            extra = f" {getattr(instruction, 'item_name', '')}"
        elif kind == "talk_npc":
            words = getattr(instruction, "words", [])
            extra = f" {words[0]!r}" if words else ""
        return f"[{action}{extra}]"

    def _current_position(self) -> Any:
        current_position = self._session_method("current_position")
        if current_position is not None:
            try:
                pos = current_position(allow_route_seed=True)
                if pos is not None:
                    return pos
            except Exception:
                _log.debug("[GUI] current_position read error", exc_info=True)

        executor = getattr(self._session, "_executor", None)
        live_pos = getattr(executor, "_current_pos", None) if executor else None
        return live_pos or getattr(self._session, "_position", None)

    def _session_loader(self) -> Any:
        monitor_loader = self._session_method("monitor_loader")
        if monitor_loader is not None:
            try:
                return monitor_loader()
            except Exception:
                _log.debug("[GUI] monitor_loader read error", exc_info=True)

        loader = getattr(self._session, "_loader", None)
        if loader is not None:
            return loader
        try:
            from .map_loader import TibiaMapLoader as _TML

            loader = _TML()
            self._session._loader = loader
            return loader
        except Exception:
            return None

    def _route_instructions(self) -> list[Any]:
        active_instructions = self._session_method("active_instructions")
        if active_instructions is not None:
            try:
                return list(active_instructions())
            except Exception:
                _log.debug("[GUI] active_instructions read error", exc_info=True)

        executor = getattr(self._session, "_executor", None)
        return list(getattr(executor, "_instructions", None) or [])

    # ── Polling ──────────────────────────────────────────────────────────────

    def _poll(self) -> None:
        """
        Read stats from the session and refresh the internal state dict.
        Then push state into StringVars and schedule the next call.
        """
        try:
            state_update = self._build_polled_state()
            with self._lock:
                self._state.update(state_update)
        except Exception as _e:
            _log.debug("[GUI] poll state-build error: %s", _e)

        if self._built and self._root is not None:
            self._flush_svars()
            self._update_minimap()
            try:
                self._root.after(self._cfg.refresh_ms, self._poll)
            except Exception as _e:
                _log.debug("[GUI] reschedule error: %s", _e)

    def _update_minimap(self) -> None:
        """Render walkability tiles from TibiaMapLoader centred on the player.

        Green = walkable, Red = non-walkable, Black dot = player position.
        Yellow dots mark route waypoints on the same floor.
        Falls back to a text placeholder when no position / map is available.
        """
        if self._minimap_canvas is None:
            return
        try:
            import numpy as np
            from PIL import Image, ImageTk
        except ImportError:
            return

        try:
            # Prefer executor's live position (updates each step) over the
            # session-level cached position (only updated by MinimapRadar).
            pos = self._current_position()

            loader = self._session_loader()

            if pos is None or loader is None:
                self._minimap_canvas.delete("all")
                self._minimap_canvas.create_text(
                    self._MINI_W // 2, self._MINI_H // 2,
                    text="sin posición", fill="#888",
                    font=("TkDefaultFont", 10),
                )
                return

            # Auto-load floor on first sight (best-effort, may block briefly)
            if not loader.floor_loaded(pos.z):
                try:
                    loader.preload_floor(pos.z)
                except Exception:
                    _log.debug("Monitor GUI failed to preload minimap floor %s", pos.z, exc_info=True)

            if not loader.floor_loaded(pos.z):
                self._minimap_canvas.delete("all")
                self._minimap_canvas.create_text(
                    self._MINI_W // 2, self._MINI_H // 2,
                    text=f"cargando piso {pos.z}…", fill="#aaa",
                    font=("TkDefaultFont", 9),
                )
                return

            # Real map image from tibiamaps.io (1 px = 1 tile, RGBA)
            map_rgba = loader.get_map_image(pos.z)   # shape (H, W, 4)
            map_rgb  = map_rgba[:, :, :3]            # drop alpha
            px, py   = pos.to_pixel()
            R    = 25          # tile radius around player
            side = R * 2 + 1   # 51 × 51 tile area total
            H, W = map_rgb.shape[:2]

            # Dark background for out-of-bounds areas
            rgb = np.full((side, side, 3), 20, dtype=np.uint8)

            # Source range in the full map array
            sr0, sc0 = py - R, px - R

            # Clamped (valid) sub-range
            cr0, cc0 = max(sr0, 0), max(sc0, 0)
            cr1, cc1 = min(sr0 + side, H), min(sc0 + side, W)

            # Corresponding destination offsets
            dr0, dc0 = cr0 - sr0, cc0 - sc0
            dr1, dc1 = dr0 + (cr1 - cr0), dc0 + (cc1 - cc0)

            if cr1 > cr0 and cc1 > cc0:
                rgb[dr0:dr1, dc0:dc1] = map_rgb[cr0:cr1, cc0:cc1]

            # Draw route waypoints as small yellow dots
            try:
                from src.models import Coordinate as _Coord
                for ins in self._route_instructions():
                    # ins.coord is a ScriptCoord — convert to Coordinate
                    _sc = getattr(ins, "coord", None)
                    if _sc is None:
                        continue
                    try:
                        _c = _Coord(
                            int(getattr(_sc, "x", 0)),
                            int(getattr(_sc, "y", 0)),
                            int(getattr(_sc, "z", 0)),
                        )
                    except Exception:
                        continue
                    if _c.z != pos.z:
                        continue
                    _wpx, _wpy = _c.to_pixel()
                    _dr = _wpy - sr0
                    _dc = _wpx - sc0
                    if 0 <= _dr < side and 0 <= _dc < side:
                        r0_ = max(_dr - 1, 0);  r1_ = min(_dr + 2, side)
                        c0_ = max(_dc - 1, 0);  c1_ = min(_dc + 2, side)
                        rgb[r0_:r1_, c0_:c1_] = (255, 200, 0)
            except Exception:
                _log.debug("Monitor GUI failed to overlay route waypoints on minimap", exc_info=True)

            # Player dot — white border 5×5, black centre 3×3
            cr = cc = R
            rgb[max(cr-2, 0):min(cr+3, side), max(cc-2, 0):min(cc+3, side)] = (255, 255, 255)
            rgb[max(cr-1, 0):min(cr+2, side), max(cc-1, 0):min(cc+2, side)] = (0,   0,   0)

            # Scale to canvas with smooth interpolation
            img   = Image.fromarray(rgb, "RGB")
            img   = img.resize((self._MINI_W, self._MINI_H), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(img)

            self._minimap_photo = photo
            c = self._minimap_canvas
            c.delete("all")
            c.create_image(0, 0, anchor="nw", image=photo)

            # Coordinate overlay (top-left)
            c.create_text(
                4, 4, anchor="nw",
                text=f"{pos.x},{pos.y},{pos.z}",
                fill="#00ff88",
                font=("TkFixedFont", 8),
            )
        except Exception:
            _log.debug("Monitor GUI failed to refresh minimap preview", exc_info=True)

    def _flush_svars(self) -> None:
        """Push internal state into the Tk StringVars (main-thread only)."""
        if not self._svars:
            return
        with self._lock:
            s = dict(self._state)
            conds: set[str] = set(s["conditions"])

        def _set(key: str, val: str) -> None:
            sv = self._svars.get(key)
            if sv is not None:
                try:
                    sv.set(val)
                except Exception:
                    _log.debug("Monitor GUI failed to set StringVar %s", key, exc_info=True)

        try:
            _set("route",         s["route"])
            _set("current_wpt",   s.get("current_wpt", "—"))
            _set("uptime",        s["uptime"])
            _set("is_running",    "YES" if s["is_running"] else "no")
            _set("routes",        str(s["routes"]))
            _set("heals",         str(s["heals"]))
            _set("mana",          str(s["mana"]))
            _set("loot",          str(s["loot"]))
            _set("kills",         str(s["kills"]))
            _set("conditions",
                 ", ".join(sorted(conds)) if conds else "—")
            _set("last_watchdog", s["last_watchdog"])
            _set("last_depot",    s["last_depot"])
            _set("break_info",    s.get("break_info", "—"))
            _set("soak_mem",      s.get("soak_mem", "—"))

            # Toggle status badges
            _set("targeting_lbl", "ON" if self._targeting_on else "OFF")
            _set("walker_lbl",    "ON" if self._walking_on   else "OFF")
            _set("looting_lbl",   "ON" if self._looting_on   else "OFF")

            # Recolour toggle buttons to reflect ON/OFF state
            for btn, flag in [
                (self._btn_targeting, self._targeting_on),
                (self._btn_walking,   self._walking_on),
                (self._btn_looting,   self._looting_on),
            ]:
                if btn is not None:
                    try:
                        btn.configure(
                            bg=self._CLR_ON if flag else self._CLR_OFF,
                            fg="#1e1e2e",
                        )
                    except Exception:
                        _log.debug("Monitor GUI failed to recolor toggle button", exc_info=True)
        except Exception:
            _log.debug("Monitor GUI failed to flush StringVars", exc_info=True)

    # ── Toggle handlers ──────────────────────────────────────────────

    def _on_toggle_targeting(self) -> None:
        self._targeting_on = not self._targeting_on
        try:
            set_targeting_enabled = self._session_method("set_targeting_enabled")
            if set_targeting_enabled is not None:
                set_targeting_enabled(self._targeting_on)
            else:
                cm = getattr(self._session, "_combat_mgr",
                             getattr(self._session, "_combat", None))
                if cm is not None:
                    meth = "resume" if self._targeting_on else "pause"
                    getattr(cm, meth, lambda: None)()
        except Exception:
            _log.debug("Monitor GUI failed to toggle targeting state", exc_info=True)
        self.append_log(
            f"Targeting {'ON' if self._targeting_on else 'OFF'}")
        self._flush_svars()

    def _on_toggle_walking(self) -> None:
        self._walking_on = not self._walking_on
        try:
            set_walking_enabled = self._session_method("set_walking_enabled")
            if set_walking_enabled is not None:
                set_walking_enabled(self._walking_on)
            else:
                ex = getattr(self._session, "_executor", None)
                if ex is not None:
                    ex._walking_paused = not self._walking_on
        except Exception:
            _log.debug("Monitor GUI failed to toggle walking state", exc_info=True)
        self.append_log(
            f"Walker {'ON' if self._walking_on else 'OFF'}")
        self._flush_svars()

    def _on_toggle_loot(self) -> None:
        self._looting_on = not self._looting_on
        try:
            set_looting_enabled = self._session_method("set_looting_enabled")
            if set_looting_enabled is not None:
                set_looting_enabled(self._looting_on)
            else:
                looter = getattr(self._session, "_looter", None)
                if looter is not None:
                    meth = "resume" if self._looting_on else "pause"
                    getattr(looter, meth, lambda: None)()
        except Exception:
            _log.debug("Monitor GUI failed to toggle looter state", exc_info=True)
        self.append_log(
            f"Looting {'ON' if self._looting_on else 'OFF'}")
        self._flush_svars()

    # ── Action button handlers ────────────────────────────────────────

    def _on_print_position(self) -> None:
        try:
            pos = self._current_position()
            if pos is not None:
                msg = f"Posición: {pos.x}, {pos.y}, {pos.z}"
            else:
                msg = "Posición: desconocida"
            self.append_log(msg)
            _log.info(msg)
        except Exception:
            _log.debug("Monitor GUI failed to print current position", exc_info=True)

    # ── Public log helper ─────────────────────────────────────────────────

    def append_log(self, msg: str) -> None:
        """Append a timestamped line to the live log widget."""
        import datetime
        ts   = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}\n"
        self._log_lines.append(line)
        # Cap in-memory buffer
        if len(self._log_lines) > 200:
            self._log_lines = self._log_lines[-200:]
        if self._log_text is not None:
            try:
                self._log_text.configure(state="normal")
                self._log_text.insert("end", line)
                self._log_text.see("end")
                self._log_text.configure(state="disabled")
            except Exception:
                _log.debug("Monitor GUI failed to append to log widget", exc_info=True)
