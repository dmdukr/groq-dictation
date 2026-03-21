"""Floating overlay window shown during recording with waveform visualization.

Uses a dedicated tkinter instance in its own thread with proper lifecycle management.
All tkinter operations happen exclusively on the overlay thread via root.after().
"""

import collections
import logging
import queue
import threading

from .utils import compute_rms

logger = logging.getLogger(__name__)

WAVE_WIDTH = 300
WAVE_HEIGHT = 60
BAR_COUNT = 40
UPDATE_INTERVAL_MS = 50


class RecordingOverlay:
    """Small floating window showing audio waveform and mic name during recording."""

    def __init__(self):
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._started_event = threading.Event()
        self._audio_queue: queue.Queue[bytes] | None = None

    def show(self, mic_name: str, audio_queue: queue.Queue[bytes]) -> None:
        """Show the overlay. Thread-safe -- can be called from any thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._audio_queue = audio_queue
        self._stop_event.clear()
        self._started_event.clear()
        self._thread = threading.Thread(
            target=self._run_overlay,
            args=(mic_name,),
            name="RecordingOverlay",
            daemon=True,
        )
        self._thread.start()
        # Wait briefly for the tkinter mainloop to start so hide() works immediately
        self._started_event.wait(timeout=2.0)

    def hide(self) -> None:
        """Signal the overlay to close. Thread-safe -- can be called from any thread."""
        self._stop_event.set()

    def _run_overlay(self, mic_name: str) -> None:
        """Run the overlay in its own tkinter mainloop (runs entirely on this thread)."""
        try:
            import tkinter as tk
        except Exception as e:
            logger.error(f"Cannot import tkinter for overlay: {e}")
            self._started_event.set()
            return

        root = None
        try:
            root = tk.Tk()
            root.title("")
            root.overrideredirect(True)
            root.attributes("-topmost", True)
            root.attributes("-alpha", 0.9)
            root.configure(bg="#1a1a2e")

            # Size and position (bottom-center)
            w, h = WAVE_WIDTH + 40, WAVE_HEIGHT + 70
            root.update_idletasks()
            screen_w = root.winfo_screenwidth()
            screen_h = root.winfo_screenheight()
            x = (screen_w - w) // 2
            y = screen_h - h - 80
            root.geometry(f"{w}x{h}+{x}+{y}")

            frame = tk.Frame(root, bg="#1a1a2e", padx=12, pady=8)
            frame.pack(fill="both", expand=True)

            # Top row
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
                    root.quit()
                    return

                # Read audio levels from queue
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

                # Draw bars
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

                root.after(UPDATE_INTERVAL_MS, update)

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
                root.after(500, blink)

            self._started_event.set()
            root.after(UPDATE_INTERVAL_MS, update)
            root.after(500, blink)
            root.mainloop()

        except Exception as e:
            logger.error(f"Overlay error: {e}")
        finally:
            self._started_event.set()  # Ensure show() never blocks forever
            if root is not None:
                try:
                    root.destroy()
                except Exception:
                    pass


