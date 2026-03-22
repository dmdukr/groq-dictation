"""Floating overlay window shown during recording with waveform visualization.

Uses the shared tk_host Toplevel — no separate Tk() or mainloop().
All tkinter operations happen on the Tk host thread via run_on_tk().
"""

import collections
import logging
import queue
import threading
import tkinter as tk

from .utils import compute_rms

logger = logging.getLogger(__name__)

WAVE_WIDTH = 300
WAVE_HEIGHT = 60
BAR_COUNT = 40
UPDATE_INTERVAL_MS = 50


class RecordingOverlay:
    """Small floating window showing audio waveform and mic name during recording."""

    def __init__(self):
        self._stop_event = threading.Event()
        self._audio_queue: queue.Queue[bytes] | None = None
        self._window: tk.Toplevel | None = None

    def show(self, mic_name: str, audio_queue: queue.Queue[bytes]) -> None:
        """Show the overlay. Thread-safe — schedules on Tk host thread."""
        if self._window is not None:
            return
        self._audio_queue = audio_queue
        self._stop_event.clear()
        from . import tk_host
        tk_host.run_on_tk(lambda: self._build(mic_name))

    def hide(self) -> None:
        """Signal the overlay to close. Thread-safe."""
        self._stop_event.set()

    def _build(self, mic_name: str) -> None:
        """Build the overlay. Runs on Tk host thread."""
        from . import tk_host

        try:
            win = tk.Toplevel(tk_host.get_root())
            self._window = win
            win.title("")
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            win.attributes("-alpha", 0.9)
            win.configure(bg="#1a1a2e")

            # Size and position (bottom-center)
            w, h = WAVE_WIDTH + 40, WAVE_HEIGHT + 70
            win.update_idletasks()
            screen_w = win.winfo_screenwidth()
            screen_h = win.winfo_screenheight()
            x = (screen_w - w) // 2
            y = screen_h - h - 80
            win.geometry(f"{w}x{h}+{x}+{y}")

            frame = tk.Frame(win, bg="#1a1a2e", padx=12, pady=8)
            frame.pack(fill="both", expand=True)

            top_frame = tk.Frame(frame, bg="#1a1a2e")
            top_frame.pack(fill="x")

            dot_canvas = tk.Canvas(top_frame, width=12, height=12, bg="#1a1a2e", highlightthickness=0)
            dot_canvas.pack(side="left", padx=(0, 6))
            dot_canvas.create_oval(2, 2, 10, 10, fill="#ff3333", outline="#ff3333")

            tk.Label(
                top_frame, text=f"  {mic_name}",
                fg="#cccccc", bg="#1a1a2e", font=("Segoe UI", 9), anchor="w",
            ).pack(side="left", fill="x", expand=True)

            tk.Label(
                top_frame, text="REC",
                fg="#ff3333", bg="#1a1a2e", font=("Segoe UI", 9, "bold"),
            ).pack(side="right")

            canvas = tk.Canvas(
                frame, width=WAVE_WIDTH, height=WAVE_HEIGHT,
                bg="#16213e", highlightthickness=0,
            )
            canvas.pack(pady=(6, 0))

            levels: collections.deque = collections.deque([0.0] * BAR_COUNT, maxlen=BAR_COUNT)
            dot_visible = [True]

            def update():
                if self._stop_event.is_set():
                    self._window = None
                    win.destroy()
                    return

                try:
                    frames_read = 0
                    rms_sum = 0.0
                    while self._audio_queue and not self._audio_queue.empty() and frames_read < 10:
                        try:
                            data = self._audio_queue.get_nowait()
                            rms_sum += compute_rms(data)
                            frames_read += 1
                        except queue.Empty:
                            break
                    level = min(1.0, (rms_sum / frames_read) / 4000.0) if frames_read > 0 else 0.0
                    levels.append(level)
                except Exception:
                    levels.append(0.0)

                try:
                    canvas.delete("all")
                    bar_w = WAVE_WIDTH / BAR_COUNT
                    for i, lv in enumerate(levels):
                        bar_h = max(2, lv * WAVE_HEIGHT * 0.9)
                        x1 = i * bar_w + 1
                        x2 = x1 + bar_w - 2
                        yc = WAVE_HEIGHT / 2
                        color = "#00d4aa" if lv < 0.3 else ("#f0c040" if lv < 0.6 else "#ff4444")
                        canvas.create_rectangle(x1, yc - bar_h / 2, x2, yc + bar_h / 2, fill=color, outline="")
                except Exception:
                    pass

                win.after(UPDATE_INTERVAL_MS, update)

            def blink():
                if self._stop_event.is_set():
                    return
                dot_visible[0] = not dot_visible[0]
                color = "#ff3333" if dot_visible[0] else "#1a1a2e"
                try:
                    dot_canvas.delete("all")
                    dot_canvas.create_oval(2, 2, 10, 10, fill=color, outline=color)
                except Exception:
                    pass
                win.after(500, blink)

            win.after(UPDATE_INTERVAL_MS, update)
            win.after(500, blink)

        except Exception as e:
            logger.error(f"Overlay error: {e}")
            self._window = None


