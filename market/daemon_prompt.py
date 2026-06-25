"""Small Tk prompts used by the market daemon."""

from __future__ import annotations

import tkinter as tk


def prompt_recipe_name(
    *,
    title: str = "Craft cost",
    prompt: str = "Item name (e.g. Draconic Bow):",
    initial: str = "",
) -> str | None:
    """Blocking topmost dialog; returns stripped text or None if cancelled."""
    result: list[str | None] = [None]

    root = tk.Tk()
    root.withdraw()

    win = tk.Toplevel(root)
    win.title(title)
    win.attributes("-topmost", True)
    win.resizable(False, False)

    tk.Label(win, text=prompt, anchor="w", justify="left").pack(
        padx=14, pady=(14, 6), anchor="w"
    )

    var = tk.StringVar(value=initial)
    entry = tk.Entry(win, textvariable=var, width=42, font=("Segoe UI", 11))
    entry.pack(padx=14, pady=(0, 8))
    entry.focus_set()
    if initial:
        entry.select_range(0, tk.END)

    def finish(value: str | None) -> None:
        result[0] = value
        win.destroy()
        root.quit()

    def on_ok(_event: object | None = None) -> None:
        text = var.get().strip()
        finish(text if text else None)

    def on_cancel(_event: object | None = None) -> None:
        finish(None)

    entry.bind("<Return>", on_ok)
    entry.bind("<Escape>", on_cancel)

    buttons = tk.Frame(win)
    buttons.pack(padx=14, pady=(0, 14))
    tk.Button(buttons, text="OK", width=10, command=on_ok).pack(side=tk.LEFT, padx=(0, 6))
    tk.Button(buttons, text="Cancel", width=10, command=on_cancel).pack(side=tk.LEFT)

    win.protocol("WM_DELETE_WINDOW", on_cancel)
    win.update_idletasks()
    w, h = win.winfo_width(), win.winfo_height()
    x = (win.winfo_screenwidth() - w) // 2
    y = (win.winfo_screenheight() - h) // 3
    win.geometry(f"+{x}+{y}")

    root.mainloop()
    root.destroy()
    return result[0]
