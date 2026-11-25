"""
AirDude - VS Code Inspired Desktop UI
Modern dark-theme professional desktop application.
Preserves all existing networking engine and transfer logic.
"""

import argparse
import os
import queue
import sys
import threading
import time
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox
from typing import Dict, List, Optional

import customtkinter as ctk

from network import AirDudeNetwork
from utils import (
    calculate_file_hash,
    format_bytes,
    get_local_ip,
    load_history,
    save_history,
    add_history_entry,
    clear_history_file,
    load_settings,
    save_settings,
    notify_transfer_complete,
    enable_drag_and_drop,
    get_asset_path,
    get_brand_image,
    clear_brand_image_cache,
)

# ==========================================
# VS Code Dark Theme Color Palette
# ==========================================
C_BG          = "#1E1E1E"   # Main background
C_SIDEBAR     = "#252526"   # Sidebar background
C_ACTBAR      = "#333333"   # Activity bar (far left)
C_PANEL       = "#181818"   # Panel / sub-panel background
C_CARD        = "#2D2D30"   # Card / item background
C_CARD_HOVER  = "#37373D"   # Card hover
C_BORDER      = "#3C3C3C"   # Borders & dividers
C_ACCENT      = "#007ACC"   # Primary accent (VS Code blue)
C_ACCENT_DIM  = "#005A9E"   # Hover / pressed accent
C_PURPLE      = "#7048E8"   # Secondary purple (AirDude logo gradient)
C_HOVER       = "#2A2D2E"   # Subtle hover overlay
C_TEXT        = "#CCCCCC"   # Main text
C_TEXT_SUB    = "#858585"   # Secondary / muted text
C_SUCCESS     = "#4EC9B0"   # Teal success
C_WARN        = "#CCA700"   # Warning amber
C_ERROR       = "#F44747"   # Error red
C_ACTIVE_NAV  = "#37373D"   # Active nav item bg
C_ACTBAR_SEL  = "#007ACC"   # Activity bar selected indicator

FONT = "Segoe UI" if sys.platform == "win32" else "Helvetica"

def _font(size=12, weight="normal"):
    return ctk.CTkFont(family=FONT, size=size, weight=weight)

def _btn(parent, text, command, width=None, height=28, fg=C_CARD, hover=C_CARD_HOVER,
         text_color=C_TEXT, border=C_BORDER, bw=1, radius=2, font_size=12, font_w="normal", **kw):
    kwargs = dict(
        text=text, command=command, height=height,
        fg_color=fg, hover_color=hover,
        text_color=text_color, border_width=bw, border_color=border,
        corner_radius=radius, font=_font(font_size, font_w),
    )
    if width is not None:
        kwargs["width"] = width
    return ctk.CTkButton(parent, **kwargs, **kw)

def _label(parent, text, size=12, weight="normal", color=C_TEXT, **kw):
    return ctk.CTkLabel(parent, text=text, font=_font(size, weight), text_color=color, **kw)

def _divider(parent, color=C_BORDER, padx=0, pady=0):
    f = tk.Frame(parent, height=1, bg=color, bd=0, highlightthickness=0)
    f.pack(fill="x", padx=padx, pady=pady)
    return f

def _brand_label(parent, img, width, height, **pack_kw):
    """
    Creates a CTkLabel with a brand image, gracefully returning None on TclError.
    TclError can happen in test environments where multiple Tk roots are created
    sequentially — the photo image from the previous root no longer exists.
    """
    if img is None:
        return None
    try:
        lbl = ctk.CTkLabel(parent, image=img, text="", width=width, height=height)
        lbl.pack(**pack_kw)
        return lbl
    except Exception:
        return None


# ==========================================
# Reusable Flat VS Code List Components
# ==========================================

class SidebarListItem(ctk.CTkFrame):
    """Flat, minimal VS Code sidebar list item with fixed 36px height and hover."""

    def __init__(self, master, text: str, right_text: str = "", right_color: str = C_TEXT_SUB,
                 active: bool = False, on_click=None, height: int = 36, **kw):
        bg = C_ACTIVE_NAV if active else "transparent"
        super().__init__(
            master,
            fg_color=bg,
            corner_radius=0,
            height=height,
            cursor="hand2" if on_click else "",
            **kw,
        )
        self.pack_propagate(False)
        self._active = active
        self._on_click = on_click

        inner = ctk.CTkFrame(self, fg_color="transparent", height=height)
        inner.pack(side="left", fill="both", expand=True, padx=(20, 14))

        self.lbl = _label(inner, text, 12, "bold" if active else "normal", C_TEXT)
        self.lbl.pack(side="left", anchor="w")

        if right_text:
            self.right_lbl = _label(inner, right_text, 11, "bold", right_color)
            self.right_lbl.pack(side="right")

        self._bind_events(self)

    def _bind_events(self, widget):
        widget.bind("<Enter>", self._on_enter, add="+")
        widget.bind("<Leave>", self._on_leave, add="+")
        if self._on_click:
            widget.bind("<Button-1>", lambda e: self._on_click(), add="+")
        for child in widget.winfo_children():
            self._bind_events(child)

    def _on_enter(self, e=None):
        if not self._active:
            try:
                self.configure(fg_color=C_HOVER)
            except Exception:
                pass

    def _on_leave(self, e=None):
        if not self._active:
            try:
                x, y = self.winfo_pointerxy()
                rx, ry = self.winfo_rootx(), self.winfo_rooty()
                rw, rh = self.winfo_width(), self.winfo_height()
                if not (rx <= x <= rx + rw and ry <= y <= ry + rh):
                    self.configure(fg_color="transparent")
            except Exception:
                pass

    def set_active(self, active: bool):
        self._active = active
        try:
            self.configure(fg_color=C_ACTIVE_NAV if active else "transparent")
            self.lbl.configure(font=_font(12, "bold" if active else "normal"))
        except Exception:
            pass


class SettingsRow(ctk.CTkFrame):
    """Antigravity-style grouped settings row with grid alignment and crisp 1px bottom divider."""

    def __init__(self, master, title: str, description: str = "", show_divider: bool = True, **kw):
        super().__init__(master, fg_color="transparent", corner_radius=0, **kw)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="x", padx=16, pady=12)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=0)

        left = ctk.CTkFrame(body, fg_color="transparent")
        left.grid(row=0, column=0, sticky="w", padx=(0, 16))
        _label(left, title, 12, "bold", "#E2E2E2").pack(anchor="w")
        if description:
            _label(left, description, 10, color=C_TEXT_SUB).pack(anchor="w", pady=(2, 0))

        self.right_slot = ctk.CTkFrame(body, fg_color="transparent")
        self.right_slot.grid(row=0, column=1, sticky="e")

        if show_divider:
            tk.Frame(self, height=1, bg="#343438", bd=0, highlightthickness=0).pack(fill="x", padx=1)


# ==========================================
# Startup Splash Screen
# ==========================================

class SplashScreen(ctk.CTkToplevel):
    """
    Sleek professional startup splash screen featuring the official AirDude shield logo.
    Automatically closes and reveals the main workspace once initialization finishes.
    """
    def __init__(self, parent, duration_ms: int = 1200, on_ready=None, **kw):
        super().__init__(parent, **kw)
        self.duration_ms = duration_ms
        self.on_ready = on_ready
        self.overrideredirect(True)
        self.configure(fg_color=C_PANEL)

        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = 420, 290
        x, y = max(0, (sw - w) // 2), max(0, (sh - h) // 2)
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.attributes("-topmost", True)

        card = ctk.CTkFrame(self, fg_color=C_CARD, corner_radius=8, border_width=1, border_color=C_BORDER)
        card.pack(fill="both", expand=True, padx=2, pady=2)

        content = ctk.CTkFrame(card, fg_color="transparent")
        content.pack(expand=True, fill="both", padx=24, pady=20)

        # Brand logo
        logo_img = get_brand_image("logo_shield.png", (88, 88))
        if logo_img and _brand_label(content, logo_img, 88, 88, pady=(6, 10)):
            pass
        else:
            badge = ctk.CTkFrame(content, width=64, height=64, fg_color=C_ACCENT, corner_radius=6)
            badge.pack(pady=(6, 10))
            badge.pack_propagate(False)
            _label(badge, "AirDude", 13, "bold", "#FFFFFF").pack(expand=True)

        _label(content, "AirDude", 22, "bold", "#FFFFFF").pack()
        _label(content, "Peer-to-Peer Local Network Sharing", 11, color=C_TEXT_SUB).pack(pady=(2, 14))

        self.prog = ctk.CTkProgressBar(content, height=3, corner_radius=2, progress_color=C_ACCENT, fg_color=C_HOVER)
        self.prog.set(0)
        self.prog.pack(fill="x", padx=36, pady=(0, 8))
        self.prog.configure(mode="indeterminate")
        self.prog.start()

        self.status_lbl = _label(content, "Starting UDP discovery & LAN engine...", 10, color=C_TEXT_SUB)
        self.status_lbl.pack()

        self.after(self.duration_ms, self._finish)

    def _finish(self):
        try:
            self.prog.stop()
            self.destroy()
        except Exception:
            pass
        if self.on_ready:
            try:
                self.on_ready()
            except Exception:
                pass

# ==========================================
# Activity Bar (Far Left Icon Bar)
# ==========================================

class ActivityBar(ctk.CTkFrame):
    """Narrow 48px icon bar on the far left, VS Code style."""

    ITEMS = [
        ("devices",   "⬡",  "Nearby Devices"),
        ("transfers", "⇄",  "Transfers"),
        ("history",   "☰",  "History"),
        ("settings",  "⚙",  "Settings"),
    ]

    def __init__(self, master, on_select, **kw):
        super().__init__(master, width=48, fg_color=C_ACTBAR, corner_radius=0, **kw)
        self.on_select = on_select
        self._active = None
        self._buttons = {}
        self.pack_propagate(False)
        self._build()

    def _build(self):
        # Top: official AirDude brand mark (Antigravity style)
        logo_frame = ctk.CTkFrame(self, fg_color="transparent", width=48, height=48)
        logo_frame.pack(fill="x")
        logo_frame.pack_propagate(False)
        logo_img = get_brand_image("logo_64.png", (26, 26))
        if logo_img:
            self._actbar_logo_ref = logo_img
            if not _brand_label(logo_frame, logo_img, 26, 26, expand=True, pady=(10, 4)):
                ctk.CTkFrame(logo_frame, height=10, fg_color="transparent").pack()
        else:
            ctk.CTkFrame(logo_frame, height=10, fg_color="transparent").pack()

        _divider(self, padx=8, pady=(0, 6))

        for key, icon, tooltip in self.ITEMS:
            frame = ctk.CTkFrame(self, fg_color="transparent", width=48, height=44)
            frame.pack(fill="x")
            frame.pack_propagate(False)

            btn = ctk.CTkButton(
                frame,
                text=icon,
                width=36,
                height=36,
                fg_color="transparent",
                hover_color=C_HOVER,
                text_color=C_TEXT_SUB,
                font=_font(18),
                corner_radius=6,
                command=lambda k=key: self.select(k),
            )
            btn.pack(expand=True)

            self._buttons[key] = btn

        # Bottom: Send Files quick action
        ctk.CTkFrame(self, fg_color="transparent").pack(fill="both", expand=True)
        send_btn = ctk.CTkButton(
            self,
            text="↗",
            width=36,
            height=36,
            fg_color=C_ACCENT,
            hover_color=C_ACCENT_DIM,
            text_color="#FFFFFF",
            font=_font(16, "bold"),
            corner_radius=4,
            command=lambda: self.on_select("send"),
        )
        send_btn.pack(pady=(0, 16))

    def select(self, key: str):
        self.set_selected(key)
        self.on_select(key)

    def set_selected(self, key: str):
        self._active = key
        for k, btn in self._buttons.items():
            if k == key:
                btn.configure(
                    fg_color="#45454A",
                    hover_color="#45454A",
                    text_color="#FFFFFF",
                )
            else:
                btn.configure(
                    fg_color="transparent",
                    hover_color=C_HOVER,
                    text_color=C_TEXT_SUB,
                )


# ==========================================
# Sidebar Panel
# ==========================================

class SidebarPanel(ctk.CTkFrame):
    """230px sidebar showing title + sub-navigation/content for the active section."""

    def __init__(self, master, **kw):
        super().__init__(master, width=230, fg_color=C_SIDEBAR, corner_radius=0, **kw)
        self.pack_propagate(False)
        self.app_ref = master
        self._content_frame = None
        self._current_section = ""
        ctk.CTkFrame(self, height=10, fg_color="transparent").pack()

    def set_section(self, section_key: str, title: str):
        """Update sidebar content for the active section."""
        self._current_section = section_key
        if self._content_frame:
            self._content_frame.destroy()

        self._content_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._content_frame.pack(fill="both", expand=True)

        # Section title header
        title_row = ctk.CTkFrame(self._content_frame, fg_color="transparent")
        title_row.pack(fill="x", padx=12, pady=(10, 6))
        _label(title_row, title.upper(), 10, "bold", C_TEXT_SUB).pack(anchor="w")

        self.body_frame = ctk.CTkFrame(self._content_frame, fg_color="transparent")
        self.body_frame.pack(fill="both", expand=True)

        self.refresh()

    def refresh(self):
        """Refreshes sidebar dynamic content based on the active section."""
        if not hasattr(self, "body_frame") or not self.body_frame.winfo_exists():
            return

        for w in self.body_frame.winfo_children():
            w.destroy()

        sec = self._current_section

        if sec == "devices":
            devices = self.app_ref.active_devices if self.app_ref else {}
            count = len(devices)
            
            sub_hdr = ctk.CTkFrame(self.body_frame, fg_color="transparent")
            sub_hdr.pack(fill="x", padx=12, pady=(0, 6))
            _label(sub_hdr, f"DISCOVERED ({count})", 10, "bold", C_TEXT_SUB).pack(side="left")

            scroll = ctk.CTkScrollableFrame(self.body_frame, fg_color="transparent", corner_radius=0)
            scroll.pack(fill="both", expand=True, padx=4)

            if not devices:
                _label(scroll, "Scanning LAN...\nMake sure other PCs are open.", 10, color=C_TEXT_SUB).pack(anchor="w", padx=8, pady=8)
            else:
                for key, dev in devices.items():
                    item = ctk.CTkFrame(scroll, fg_color="transparent", corner_radius=4)
                    item.pack(fill="x", pady=1)

                    inner = ctk.CTkFrame(item, fg_color="transparent")
                    inner.pack(fill="x", padx=6, pady=4)

                    _label(inner, "⬡", 10, color=C_ACCENT).pack(side="left", padx=(0, 6))
                    
                    name_box = ctk.CTkFrame(inner, fg_color="transparent")
                    name_box.pack(side="left", fill="x", expand=True)
                    _label(name_box, dev.get("hostname", "Peer"), 11, "bold").pack(anchor="w")
                    _label(name_box, dev.get("ip", ""), 9, color=C_TEXT_SUB).pack(anchor="w")

                    _btn(inner, "↗", command=lambda d=dev: self.app_ref.navigate_to("send", target_device=d),
                         width=24, height=22, fg=C_CARD, hover=C_ACCENT, text_color="#FFFFFF", font_size=10).pack(side="right")

            # Quick action at bottom
            bot = ctk.CTkFrame(self.body_frame, fg_color=C_HOVER, corner_radius=4)
            bot.pack(fill="x", padx=10, pady=10)
            _btn(bot, "+ Send Files", command=lambda: self.app_ref.navigate_to("send"),
                 height=26, fg=C_ACCENT, hover=C_ACCENT_DIM, text_color="#FFFFFF", bw=0, font_size=11).pack(fill="x", padx=6, pady=6)

        elif sec == "transfers":
            cards = getattr(self.app_ref.current_page, "transfer_cards", {}) if isinstance(getattr(self.app_ref, "current_page", None), TransfersPage) else {}
            n_active = sum(1 for c in cards.values() if getattr(c, "status", "") in ("in_progress", "paused", "waiting"))
            n_done = sum(1 for c in cards.values() if getattr(c, "status", "") == "completed")
            n_fail = sum(1 for c in cards.values() if getattr(c, "status", "") in ("failed", "declined"))

            stats = [
                ("Active", str(n_active), C_TEXT_SUB),
                ("Completed", str(n_done), C_TEXT_SUB),
                ("Failed", str(n_fail), C_TEXT_SUB),
            ]
            for label, count_val, col in stats:
                SidebarListItem(self.body_frame, label, right_text=count_val, right_color=col).pack(fill="x")

            _label(self.body_frame, "ACTIVE SESSIONS", 10, "bold", C_TEXT_SUB).pack(anchor="w", padx=16, pady=(14, 4))
            scroll = ctk.CTkScrollableFrame(self.body_frame, fg_color="transparent", corner_radius=0)
            scroll.pack(fill="both", expand=True)

            active_items = [c for c in cards.values() if getattr(c, "status", "") in ("in_progress", "paused", "waiting")]
            if not active_items:
                _label(scroll, "No active transfers.", 10, color=C_TEXT_SUB).pack(anchor="w", padx=16, pady=4)
            else:
                for c in active_items:
                    SidebarListItem(
                        scroll, c.filename[:18], right_text=format_bytes(c.total_size), right_color=C_TEXT_SUB
                    ).pack(fill="x")

        elif sec == "history":
            records = self.app_ref.history_records if self.app_ref else []
            total = len(records)
            n_sent = sum(1 for r in records if r.get("direction") == "SEND")
            n_recv = sum(1 for r in records if r.get("direction") == "RECEIVE")

            stats = [
                ("Total Records", f"{total} / 100"),
                ("Outgoing Sent", str(n_sent)),
                ("Incoming Received", str(n_recv)),
            ]
            for label, count_val in stats:
                SidebarListItem(self.body_frame, label, right_text=count_val, right_color=C_TEXT_SUB).pack(fill="x")

            _divider_padded(self.body_frame, padx=16, pady=(10, 10))

            _btn(self.body_frame, "Clear History",
                 command=lambda: self.app_ref.clear_all_history() if self.app_ref else None,
                 height=28, fg="transparent", hover=C_HOVER, border=C_BORDER, bw=1,
                 text_color=C_TEXT, radius=4, font_size=11).pack(fill="x", padx=16)

        elif sec == "settings":
            self._settings_nav_items = {}
            active_sec = getattr(self, "_active_settings_sec", "identity")
            items = [
                ("identity", "Device Identity"),
                ("appearance", "Appearance"),
                ("storage", "File Storage"),
                ("network", "Network Ports"),
                ("about", "About"),
            ]
            for key, title_item in items:
                item_w = SidebarListItem(
                    self.body_frame,
                    title_item,
                    active=(key == active_sec),
                    on_click=lambda k=key: self._select_settings_section(k),
                )
                item_w.pack(fill="x")
                self._settings_nav_items[key] = item_w

        elif sec == "send":
            _label(self.body_frame, "SEND WORKFLOW", 10, "bold", C_TEXT_SUB).pack(anchor="w", padx=16, pady=(0, 4))
            steps = [
                ("Select Device", "Pick LAN peer"),
                ("Select Files", "Browse or drop"),
                ("Review & Send", "Direct TCP"),
            ]
            for s_num, s_desc in steps:
                SidebarListItem(self.body_frame, s_num, right_text=s_desc, right_color=C_TEXT_SUB).pack(fill="x")

    def _select_settings_section(self, key: str):
        self._active_settings_sec = key
        for k, item in getattr(self, "_settings_nav_items", {}).items():
            item.set_active(k == key)
        if self.app_ref and isinstance(getattr(self.app_ref, "current_page", None), SettingsPage):
            self.app_ref.current_page.scroll_to_section(key)


# ==========================================
# Top Status Bar
# ==========================================

class TopBar(ctk.CTkFrame):
    """Thin top bar with breadcrumb path and network status."""

    def __init__(self, master, **kw):
        super().__init__(master, height=36, fg_color=C_PANEL, corner_radius=0, **kw)
        self.grid_propagate(False)

        self.path_lbl = _label(self, "Nearby Devices", 11, color=C_TEXT_SUB)
        self.path_lbl.pack(side="left", padx=14)

        status_frame = ctk.CTkFrame(self, fg_color="transparent")
        status_frame.pack(side="right", padx=14)
        _label(status_frame, "●", 10, color=C_SUCCESS).pack(side="left", padx=(0, 4))
        self.status_lbl = _label(status_frame, "Local network", 11, color=C_TEXT_SUB)
        self.status_lbl.pack(side="left")

    def set_path(self, path: str):
        self.path_lbl.configure(text=path)


# ==========================================
# Devices Page
# ==========================================

class DeviceRow(ctk.CTkFrame):
    """Compact single-row device item, VS Code list-item style."""

    def __init__(self, master, device_info: dict, on_send=None, **kw):
        super().__init__(master, fg_color="transparent", corner_radius=0, **kw)
        self.device_info = device_info
        self.on_send = on_send
        self._hovered = False
        self._build()
        self._bind_hover(self)

    def _build(self):
        self.inner = ctk.CTkFrame(self, fg_color="transparent")
        self.inner.pack(fill="x", padx=0, pady=0)

        # OS icon
        os_name = self.device_info.get("os", "").lower()
        if "win" in os_name:
            icon = "⊞"
        elif "darwin" in os_name or "mac" in os_name:
            icon = ""
        elif "linux" in os_name:
            icon = "🐧"
        else:
            icon = "⬡"

        icon_frame = ctk.CTkFrame(self.inner, width=28, height=28, fg_color=C_HOVER, corner_radius=2)
        icon_frame.pack(side="left", padx=(20, 10), pady=7)
        icon_frame.pack_propagate(False)
        _label(icon_frame, icon, 13).pack(expand=True)

        # Device info
        info = ctk.CTkFrame(self.inner, fg_color="transparent")
        info.pack(side="left", fill="x", expand=True, pady=7)

        hostname = self.device_info.get("hostname", "Unknown")
        _label(info, hostname, 12, "normal").pack(anchor="w")

        os_str = self.device_info.get("os", "Unknown")
        ip_str = self.device_info.get("ip", "")
        _label(info, f"{os_str}  ·  {ip_str}", 10, color=C_TEXT_SUB).pack(anchor="w")

        # Right: status dot + send button (hidden by default)
        right = ctk.CTkFrame(self.inner, fg_color="transparent")
        right.pack(side="right", padx=20)

        self.send_btn = _btn(
            right, "↗ Send", command=self._on_send,
            width=64, height=26, fg=C_ACCENT, hover=C_ACCENT_DIM,
            text_color="#FFFFFF", bw=0, radius=2, font_size=11, font_w="bold",
        )
        # hidden until hover

        status = ctk.CTkFrame(right, fg_color="transparent")
        status.pack(side="right", padx=(8, 0))
        _label(status, "●", 9, color=C_SUCCESS).pack(side="left", padx=(0, 3))
        self.status_lbl = _label(status, "Available", 10, color=C_TEXT_SUB)
        self.status_lbl.pack(side="left")

        # Thin bottom separator
        _divider(self)

    def _bind_hover(self, widget):
        widget.bind("<Enter>", self._on_enter, add="+")
        widget.bind("<Leave>", self._on_leave, add="+")
        for child in widget.winfo_children():
            self._bind_hover(child)

    def _on_enter(self, e=None):
        self.inner.configure(fg_color=C_HOVER)
        self.send_btn.pack(side="left", padx=(0, 8))

    def _on_leave(self, e=None):
        try:
            x, y = self.winfo_pointerxy()
            rx, ry = self.winfo_rootx(), self.winfo_rooty()
            rw, rh = self.winfo_width(), self.winfo_height()
            if not (rx <= x <= rx + rw and ry <= y <= ry + rh):
                self.inner.configure(fg_color="transparent")
                self.send_btn.pack_forget()
        except Exception:
            pass

    def _on_send(self):
        if self.on_send:
            self.on_send(self.device_info)

    def update_last_seen(self, ts: float):
        pass  # Could show last seen delta if desired


class DevicesPage(ctk.CTkFrame):
    """VS Code Explorer-style devices list."""

    def __init__(self, master, local_info: dict, on_refresh=None, on_send_file=None, **kw):
        super().__init__(master, fg_color=C_BG, corner_radius=0, **kw)
        self.local_info = local_info
        self.on_refresh = on_refresh
        self.on_send_file = on_send_file
        self.device_rows: Dict[str, DeviceRow] = {}
        self._build()

    def _build(self):
        self.pack_propagate(False)

        # Header
        hdr = ctk.CTkFrame(self, fg_color=C_PANEL, height=56, corner_radius=0)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        inner_hdr = ctk.CTkFrame(hdr, fg_color="transparent")
        inner_hdr.pack(fill="x", padx=20, pady=0)
        inner_hdr.pack_configure(anchor="center")

        left = ctk.CTkFrame(inner_hdr, fg_color="transparent")
        left.pack(side="left", pady=12)
        _label(left, "Nearby Devices", 16, "bold").pack(anchor="w")

        right_btns = ctk.CTkFrame(inner_hdr, fg_color="transparent")
        right_btns.pack(side="right", pady=12)

        self.refresh_btn = _btn(
            right_btns, "⟳  Refresh", command=self._do_refresh,
            width=84, height=28, font_size=11,
        )
        self.refresh_btn.pack(side="left")

        # Scan status bar
        self.scan_bar = ctk.CTkFrame(self, fg_color=C_HOVER, height=28, corner_radius=0)
        self.scan_bar.pack(fill="x")
        self.scan_bar.pack_propagate(False)

        scan_inner = ctk.CTkFrame(self.scan_bar, fg_color="transparent")
        scan_inner.pack(fill="x", padx=16)

        _label(scan_inner, "UDP Discovery active on port 45454", 10, color=C_TEXT_SUB).pack(side="left", pady=5)
        self.count_lbl = _label(scan_inner, "0 devices", 10, color=C_TEXT_SUB)
        self.count_lbl.pack(side="right", pady=5)

        # Scrollable device list
        self.scroll = ctk.CTkScrollableFrame(self, fg_color=C_BG, corner_radius=0)
        self.scroll.pack(fill="both", expand=True)

        # Empty state
        self.empty_frame = ctk.CTkFrame(self.scroll, fg_color="transparent")
        self.empty_frame.pack(fill="both", expand=True, pady=60)
        # Mini brand logo in empty state
        empty_logo = get_brand_image("logo_shield.png", (56, 56))
        if empty_logo:
            self._empty_logo_ref = empty_logo
            _brand_label(self.empty_frame, empty_logo, 56, 56)
        _label(self.empty_frame, "No devices discovered", 14, "bold", C_TEXT_SUB).pack(pady=(12, 0))
        _label(self.empty_frame,
               "Make sure AirDude is open on other computers on the same network.",
               11, color=C_TEXT_SUB).pack(pady=(4, 0))

        # This device info at the bottom
        self._build_this_pc_footer()

    def _build_this_pc_footer(self):
        footer = ctk.CTkFrame(self, fg_color=C_PANEL, corner_radius=0)
        footer.pack(fill="x", side="bottom")
        _divider(footer)

        inner = ctk.CTkFrame(footer, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=8)

        _label(inner, "This Computer", 10, "bold", C_TEXT_SUB).pack(anchor="w")

        row1 = ctk.CTkFrame(inner, fg_color="transparent")
        row1.pack(fill="x", pady=(4, 0))
        _label(row1, self.local_info.get("name", "My PC"), 11, color=C_TEXT).pack(side="left")
        _label(row1, self.local_info.get("ip", ""), 10, color=C_TEXT_SUB).pack(side="right")

        row2 = ctk.CTkFrame(inner, fg_color="transparent")
        row2.pack(fill="x")
        _label(row2, f"UDP {self.local_info.get('udp_port', 45454)}  ·  TCP {self.local_info.get('tcp_port', 45455)}",
               10, color=C_TEXT_SUB).pack(side="left")

    def _do_refresh(self):
        self.refresh_btn.configure(text="⟳  Scanning...")
        self.after(2000, lambda: self.refresh_btn.configure(text="⟳  Refresh"))
        if self.on_refresh:
            self.on_refresh()

    def sync_devices(self, active_devices: Dict[str, dict]):
        current_keys = set(active_devices.keys())
        existing_keys = set(self.device_rows.keys())

        for key in (existing_keys - current_keys):
            row = self.device_rows.pop(key)
            try:
                row.destroy()
            except Exception:
                pass

        for key, info in active_devices.items():
            if key in self.device_rows:
                self.device_rows[key].update_last_seen(info.get("last_seen", time.time()))
            else:
                row = DeviceRow(
                    self.scroll,
                    device_info=info,
                    on_send=self.on_send_file,
                )
                row.pack(fill="x")
                self.device_rows[key] = row

        count = len(self.device_rows)
        self.count_lbl.configure(text=f"{count} {'device' if count == 1 else 'devices'}")

        if count > 0:
            self.empty_frame.pack_forget()
        else:
            self.empty_frame.pack(fill="both", expand=True, pady=80)


# ==========================================
# Send Files Page
# ==========================================

class SendFilesPage(ctk.CTkFrame):
    """3-step send flow: Select Device → Select Files → Send."""

    def __init__(self, master, network: AirDudeNetwork, active_devices: Dict[str, dict],
                 target_device: Optional[dict] = None, **kw):
        super().__init__(master, fg_color=C_BG, corner_radius=0, **kw)
        self.network = network
        self.active_devices = active_devices
        self.selected_device = target_device or (next(iter(active_devices.values())) if active_devices else None)
        self.selected_files: List[str] = []
        self._wait_after_id = None
        self._wait_remaining = 30
        self._sending_peer = ""
        self._sending_count = 0
        self.pack_propagate(False)
        self._build()

        try:
            self.after(300, lambda: enable_drag_and_drop(self.winfo_toplevel(), self._on_drop))
        except Exception:
            pass

    def destroy(self):
        self._cancel_wait_timer()
        super().destroy()

    def _on_drop(self, paths: list):
        for p in paths:
            if os.path.exists(p) and p not in self.selected_files:
                self.selected_files.append(p)
        self._refresh_file_list()
        self._refresh_review()

    def sync_devices(self, devices: dict):
        self.active_devices = devices
        if self.selected_device:
            match = None
            for d in devices.values():
                if d.get("ip") == self.selected_device.get("ip") and d.get("tcp_port") == self.selected_device.get("tcp_port"):
                    match = d
                    break
            self.selected_device = match
        elif devices:
            self.selected_device = next(iter(devices.values()))
        self._render_device_selector()
        self._refresh_review()

    def _build(self):
        # Header
        hdr = ctk.CTkFrame(self, fg_color=C_PANEL, corner_radius=0)
        hdr.pack(fill="x")
        inner_hdr = ctk.CTkFrame(hdr, fg_color="transparent")
        inner_hdr.pack(fill="x", padx=20, pady=14)
        _label(inner_hdr, "Send Files", 16, "bold").pack(side="left", anchor="w")

        # Main scrollable content
        self.scroll = ctk.CTkScrollableFrame(self, fg_color=C_BG, corner_radius=0)
        self.scroll.pack(fill="both", expand=True)

        pad = {"padx": 24, "pady": (16, 0)}

        # Select Device
        _label(self.scroll, "Select Device", 12, "bold", "#E2E2E2").pack(anchor="w", **pad)
        self.devices_frame = ctk.CTkFrame(self.scroll, fg_color="transparent")
        self.devices_frame.pack(fill="x", padx=24, pady=(8, 0))
        self._render_device_selector()

        _divider_padded(self.scroll)

        # Step 2 + 3 side by side
        columns = ctk.CTkFrame(self.scroll, fg_color="transparent")
        columns.pack(fill="both", expand=True, padx=24, pady=(16, 20))
        columns.grid_columnconfigure(0, weight=1)
        columns.grid_columnconfigure(1, weight=0, minsize=300)

        # Left: file picker
        left = ctk.CTkFrame(columns, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 20))
        self._build_file_picker(left)

        # Right: review panel
        right = ctk.CTkFrame(columns, fg_color=C_CARD, corner_radius=4, border_width=1, border_color=C_BORDER, width=300)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_propagate(False)
        self._build_review_panel(right)

    def _render_device_selector(self):
        for w in self.devices_frame.winfo_children():
            w.destroy()

        if not self.active_devices:
            _label(self.devices_frame,
                   "No devices found. Keep AirDude open on other PCs.",
                   11, color=C_TEXT_SUB).pack(anchor="w", pady=4)
            return

        for key, dev in self.active_devices.items():
            is_sel = (self.selected_device is not None and
                      self.selected_device.get("ip") == dev.get("ip") and
                      self.selected_device.get("tcp_port") == dev.get("tcp_port"))

            border_col = C_ACCENT if is_sel else C_BORDER
            bg_col = C_HOVER if is_sel else C_CARD

            card = ctk.CTkFrame(self.devices_frame, fg_color=bg_col, border_width=1,
                                border_color=border_col, corner_radius=4, cursor="hand2")
            card.pack(side="left", padx=(0, 10), pady=2)

            inner = ctk.CTkFrame(card, fg_color="transparent")
            inner.pack(padx=14, pady=10)

            top = ctk.CTkFrame(inner, fg_color="transparent")
            top.pack(fill="x")

            check = "✓ " if is_sel else ""
            _label(top, check + dev.get("hostname", "Peer"), 12, "bold").pack(side="left")
            _label(top, dev.get("os", ""), 10, color=C_TEXT_SUB).pack(side="left", padx=(8, 0))

            _label(inner, f"{dev.get('ip')}:{dev.get('tcp_port')}", 10, color=C_TEXT_SUB).pack(anchor="w", pady=(4, 0))

            for widget in [card, inner, top]:
                widget.bind("<Button-1>", lambda e, d=dev: self._select_device(d))

    def _select_device(self, dev: dict):
        self.selected_device = dev
        self._render_device_selector()
        self._refresh_review()

    def _build_file_picker(self, parent):
        _label(parent, "Select Files", 12, "bold", "#E2E2E2").pack(anchor="w")

        # Drop zone
        dz = ctk.CTkFrame(parent, fg_color=C_CARD, border_width=1, border_color=C_BORDER, corner_radius=4)
        dz.pack(fill="x", pady=(8, 12), ipady=12)

        _label(dz, "Drop files here", 13, color=C_TEXT_SUB).pack(pady=(10, 2))
        _label(dz, "or browse from your computer", 10, color=C_TEXT_SUB).pack()

        _btn(dz, "Browse Files", command=self._browse,
             width=100, height=26, font_size=11).pack(pady=(10, 10))

        # File list
        self.file_list_frame = ctk.CTkFrame(parent, fg_color="transparent")
        self.file_list_frame.pack(fill="x")

        self.files_summary_lbl = _label(parent, "0 files selected", 10, color=C_TEXT_SUB)
        self.files_summary_lbl.pack(anchor="w", pady=(6, 0))

    def _build_review_panel(self, parent):
        inner = ctk.CTkFrame(parent, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=16, pady=14)

        _label(inner, "Review & Send", 12, "bold", "#E2E2E2").pack(anchor="w", pady=(0, 10))

        # Target device
        dev_row = ctk.CTkFrame(inner, fg_color=C_HOVER, corner_radius=4)
        dev_row.pack(fill="x", pady=(0, 10))
        dev_inner = ctk.CTkFrame(dev_row, fg_color="transparent")
        dev_inner.pack(fill="x", padx=10, pady=8)

        _label(dev_inner, "To", 10, color=C_TEXT_SUB).pack(anchor="w")
        self.review_target_lbl = _label(dev_inner, "No device selected", 12, "bold")
        self.review_target_lbl.pack(anchor="w")
        self.review_ip_lbl = _label(dev_inner, "-", 10, color=C_TEXT_SUB)
        self.review_ip_lbl.pack(anchor="w")

        _divider(inner)

        # Stats grid
        stats = ctk.CTkFrame(inner, fg_color="transparent")
        stats.pack(fill="x", pady=8)
        stats.grid_columnconfigure(0, weight=1)
        stats.grid_columnconfigure(1, weight=1)

        for i, (lbl, attr) in enumerate([("Files", "stat_files_lbl"), ("Size", "stat_size_lbl")]):
            _label(stats, lbl, 10, color=C_TEXT_SUB).grid(row=i, column=0, sticky="w", pady=2)
            lbl_widget = _label(stats, "-", 11, "bold")
            lbl_widget.grid(row=i, column=1, sticky="e", pady=2)
            setattr(self, attr, lbl_widget)

        _label(stats, "Protocol", 10, color=C_TEXT_SUB).grid(row=2, column=0, sticky="w", pady=2)
        _label(stats, "TCP/LAN", 11, "bold").grid(row=2, column=1, sticky="e", pady=2)

        _divider(inner)

        # Send button
        self.send_btn = ctk.CTkButton(
            inner,
            text="↗  Send Files",
            height=36,
            fg_color=C_ACCENT,
            hover_color=C_ACCENT_DIM,
            text_color="#FFFFFF",
            font=_font(13, "bold"),
            corner_radius=4,
            state="disabled",
            command=self._start_transfer,
        )
        self.send_btn.pack(fill="x", pady=(10, 6))

        _btn(inner, "Clear Selection", command=self._clear,
             height=26, fg="transparent", hover=C_HOVER, bw=0, font_size=11).pack(fill="x")

        # Progress area (hidden until transfer)
        self.prog_frame = ctk.CTkFrame(inner, fg_color=C_HOVER, corner_radius=4,
                                       border_width=1, border_color=C_BORDER)
        self.prog_lbl = _label(self.prog_frame, "Sending...", 10, color=C_TEXT_SUB,
                               wraplength=256, justify="left")
        self.prog_lbl.pack(padx=10, pady=(8, 2), anchor="w")
        self.prog_bar = ctk.CTkProgressBar(self.prog_frame, height=4, corner_radius=2, progress_color=C_ACCENT)
        self.prog_bar.pack(fill="x", padx=10, pady=(2, 8))
        self.prog_bar.set(0)

        _label(inner, "🔒 Direct P2P · no cloud upload", 10, color=C_TEXT_SUB).pack(anchor="w", pady=(8, 0))

        self._refresh_review()

    def _browse(self):
        files = filedialog.askopenfilenames(title="Select Files to Send")
        for f in files:
            if f not in self.selected_files:
                self.selected_files.append(f)
        self._refresh_file_list()
        self._refresh_review()

    def _refresh_file_list(self):
        for w in self.file_list_frame.winfo_children():
            w.destroy()

        total = sum(os.path.getsize(f) for f in self.selected_files if os.path.isfile(f))
        self.files_summary_lbl.configure(
            text=f"{len(self.selected_files)} file{'s' if len(self.selected_files) != 1 else ''} · {format_bytes(total)}"
        )

        for fpath in self.selected_files:
            fname = os.path.basename(fpath)
            fsize = os.path.getsize(fpath) if os.path.isfile(fpath) else 0

            row = ctk.CTkFrame(self.file_list_frame, fg_color=C_CARD, corner_radius=4,
                               border_width=1, border_color=C_BORDER)
            row.pack(fill="x", pady=2, ipady=4)

            _label(row, "📄", 13).pack(side="left", padx=(10, 8))

            info = ctk.CTkFrame(row, fg_color="transparent")
            info.pack(side="left", fill="x", expand=True)
            _label(info, fname, 11, "bold").pack(anchor="w")

            _label(row, format_bytes(fsize), 10, color=C_TEXT_SUB).pack(side="right", padx=(0, 8))

            _btn(row, "✕", command=lambda p=fpath: self._remove(p),
                 width=22, height=22, fg="transparent", hover=C_HOVER, bw=0, font_size=10).pack(side="right", padx=(0, 4))

    def _remove(self, path: str):
        if path in self.selected_files:
            self.selected_files.remove(path)
        self._refresh_file_list()
        self._refresh_review()

    def _clear(self):
        self.selected_files.clear()
        self._refresh_file_list()
        self._refresh_review()

    def _refresh_review(self):
        if self.selected_device:
            self.review_target_lbl.configure(text=self.selected_device.get("hostname", "Peer"))
            self.review_ip_lbl.configure(
                text=f"{self.selected_device.get('ip')}:{self.selected_device.get('tcp_port')}")
        else:
            self.review_target_lbl.configure(text="No device selected")
            self.review_ip_lbl.configure(text="-")

        n = len(self.selected_files)
        total = sum(os.path.getsize(f) for f in self.selected_files if os.path.isfile(f))
        self.stat_files_lbl.configure(text=f"{n} file{'s' if n != 1 else ''}")
        self.stat_size_lbl.configure(text=format_bytes(total) if n > 0 else "-")

        can_send = bool(self.selected_device and self.selected_files)
        self.send_btn.configure(state="normal" if can_send else "disabled",
                                fg_color=C_ACCENT if can_send else C_BORDER)

    def _cancel_wait_timer(self):
        if self._wait_after_id:
            try:
                self.after_cancel(self._wait_after_id)
            except Exception:
                pass
            self._wait_after_id = None

    def _wait_tick(self):
        self._wait_after_id = None
        if self._wait_remaining > 0:
            self._wait_remaining -= 1
            try:
                self.prog_lbl.configure(
                    text=f"Waiting for {self._sending_peer} to accept... ({self._wait_remaining}s)",
                    text_color=C_TEXT,
                )
                self._wait_after_id = self.after(1000, self._wait_tick)
            except Exception:
                pass

    def _start_transfer(self):
        if not self.selected_device or not self.selected_files:
            return
        self._cancel_wait_timer()
        peer = self.selected_device.get("hostname", "device")
        self._sending_peer = peer
        self._sending_count = len(self.selected_files)
        self._wait_remaining = 30

        self.send_btn.configure(state="disabled", text=f"Waiting for {peer}...")
        self.prog_frame.configure(border_color=C_ACCENT)
        self.prog_frame.pack(fill="x", pady=(10, 0))
        self.prog_lbl.configure(text=f"Waiting for {peer} to accept... (30s)", text_color=C_TEXT)
        self.prog_bar.configure(mode="indeterminate", progress_color=C_ACCENT)
        self.prog_bar.start()
        self._wait_after_id = self.after(1000, self._wait_tick)
        threading.Thread(target=self._worker, daemon=True).start()

    def _on_progress_ui(self, bsent: int, fsize: int, fname: str, fidx: int, total_files: int):
        self._cancel_wait_timer()
        try:
            if self.prog_bar.cget("mode") == "indeterminate":
                self.prog_bar.stop()
                self.prog_bar.configure(mode="determinate", progress_color=C_ACCENT)
                self.send_btn.configure(text="Sending...")
            frac = bsent / fsize if fsize > 0 else 1.0
            self.prog_bar.set(frac)
            self.prog_lbl.configure(
                text=f"[{fidx}/{total_files}] {fname} · {int(frac * 100)}%",
                text_color=C_TEXT,
            )
        except Exception:
            pass

    def _on_transfer_done(self, status: str, message: str = "", peer: str = "", count: int = 1):
        self._cancel_wait_timer()
        try:
            self.prog_bar.stop()
            self.prog_bar.configure(mode="determinate")
        except Exception:
            pass

        try:
            if status == "SUCCESS":
                self.prog_frame.configure(border_color=C_SUCCESS)
                self.prog_bar.configure(progress_color=C_SUCCESS)
                self.prog_bar.set(1.0)
                label = (
                    f"✓ File Sent Successfully to {peer}! (SHA-256 Verified)"
                    if count == 1
                    else f"✓ {count} Files Sent Successfully to {peer}! (SHA-256 Verified)"
                )
                self.prog_lbl.configure(text=label, text_color=C_SUCCESS)
                self.send_btn.configure(state="normal", text="↗  Send Files", fg_color=C_ACCENT)
            elif status == "DECLINED":
                self.prog_frame.configure(border_color=C_WARN)
                self.prog_bar.set(0)
                self.prog_lbl.configure(text=f"Declined by {peer}.", text_color=C_WARN)
                self.send_btn.configure(state="normal", text="↗  Send Files", fg_color=C_ACCENT)
            elif status == "TIMEOUT":
                self.prog_frame.configure(border_color=C_WARN)
                self.prog_bar.set(0)
                self.prog_lbl.configure(
                    text=f"⏱ {peer} did not respond within 30s. Make sure AirDude is open on their screen.",
                    text_color=C_WARN,
                )
                self.send_btn.configure(state="normal", text="⟳  Retry Send", fg_color=C_ACCENT)
            elif status == "ERROR":
                self.prog_frame.configure(border_color=C_ERROR)
                self.prog_bar.set(0)
                msg = message if message.startswith("Error") else f"Error: {message}"
                self.prog_lbl.configure(text=msg, text_color=C_ERROR)
                self.send_btn.configure(state="normal", text="↗  Send Files", fg_color=C_ACCENT)
            else:
                self.prog_frame.configure(border_color=C_BORDER)
                self.prog_lbl.configure(text=f"Status: {message or 'Done'}", text_color=C_TEXT)
                self.send_btn.configure(state="normal", text="↗  Send Files", fg_color=C_ACCENT)
        except Exception:
            pass

    def _worker(self):
        tip = self.selected_device.get("ip")
        tport = self.selected_device.get("tcp_port", 45455)
        peer = self.selected_device.get("hostname", "Peer")
        count = len(self.selected_files)

        def on_prog(bsent, fsize, fname, fidx, total_files):
            try:
                self.after(0, lambda b=bsent, s=fsize, n=fname, i=fidx, t=total_files:
                           self._on_progress_ui(b, s, n, i, t))
            except Exception:
                pass

        try:
            res = self.network.send_files(
                target_ip=tip,
                filepaths=self.selected_files,
                port=tport,
                sender_name=self.network.hostname,
                progress_callback=on_prog,
                peer_name=peer,
            )
            st = res.get("status", "UNKNOWN")
            msg = res.get("message", "Done")
            self.after(0, lambda s=st, m=msg, p=peer, c=count:
                       self._on_transfer_done(s, m, p, c))

        except Exception as e:
            err_str = str(e)
            is_timeout = "timed out" in err_str.lower() or isinstance(e, TimeoutError)
            st = "TIMEOUT" if is_timeout else "ERROR"
            self.after(0, lambda s=st, m=err_str, p=peer, c=count:
                       self._on_transfer_done(s, m, p, c))


def _divider_padded(parent, padx=24, pady=(12, 12)):
    return _divider(parent, color=C_BORDER, padx=padx, pady=pady)


# ==========================================
# Transfer Card (VS Code style)
# ==========================================

class TransferCard(ctk.CTkFrame):
    """Single transfer item — compact, dark, VS Code list style."""

    STATUS_COLORS = {
        "waiting":     C_TEXT_SUB,
        "in_progress": C_ACCENT,
        "paused":      C_WARN,
        "completed":   C_SUCCESS,
        "failed":      C_ERROR,
        "declined":    C_TEXT_SUB,
    }
    STATUS_LABELS = {
        "waiting":     "Waiting",
        "in_progress": "In Progress",
        "paused":      "Paused",
        "completed":   "Completed",
        "failed":      "Failed",
        "declined":    "Declined",
    }

    def __init__(self, master, transfer_id: str, filename: str, total_size: int,
                 direction: str, peer_name: str, on_pause=None, on_resume=None,
                 on_cancel=None, on_retry=None, saved_path: str = "", file_path: str = "",
                 target_ip: str = "", target_port: int = 45455, **kw):
        super().__init__(master, fg_color="transparent", corner_radius=0,
                         border_width=0, **kw)
        self.transfer_id = transfer_id
        self.filename = filename
        self.total_size = total_size
        self.direction = direction
        self.peer_name = peer_name
        self.on_pause = on_pause
        self.on_resume = on_resume
        self.on_cancel = on_cancel
        self.on_retry = on_retry
        self.saved_path = saved_path
        self.file_path = file_path
        self.target_ip = target_ip
        self.target_port = target_port
        self.bytes_done = 0
        self.status = "in_progress"
        self._is_paused = False
        self._last_bytes = 0
        self._last_time = time.time()
        self._speed = 0.0
        self._wait_after_id = None
        self._wait_remaining = 30
        self._wait_peer = ""
        self._build()

    def _build(self):
        self.outer = ctk.CTkFrame(self, fg_color="transparent")
        self.outer.pack(fill="x", padx=4, pady=8)

        # Row 1: icon + name + status pill
        row1 = ctk.CTkFrame(self.outer, fg_color="transparent")
        row1.pack(fill="x", pady=(0, 5))

        dir_icon = "↗" if self.direction == "SEND" else "↙"
        icon_bg = C_ACCENT if self.direction == "SEND" else C_SUCCESS
        icon_frame = ctk.CTkFrame(row1, width=28, height=28, fg_color=icon_bg, corner_radius=2)
        icon_frame.pack(side="left", padx=(0, 10))
        icon_frame.pack_propagate(False)
        _label(icon_frame, dir_icon, 12, "bold", "#FFFFFF").pack(expand=True)

        name_box = ctk.CTkFrame(row1, fg_color="transparent")
        name_box.pack(side="left", fill="x", expand=True)

        self.name_lbl = _label(name_box, self.filename, 12, "normal")
        self.name_lbl.pack(anchor="w")

        sub = f"Sending to {self.peer_name}" if self.direction == "SEND" else f"From {self.peer_name}"
        self.sub_lbl = _label(name_box, sub, 10, color=C_TEXT_SUB)
        self.sub_lbl.pack(anchor="w")

        # Status pill
        self.status_lbl = _label(row1, "● In Progress", 10, "bold", C_ACCENT)
        self.status_lbl.pack(side="right")

        # Row 2: bytes + percent
        self.row2 = ctk.CTkFrame(self.outer, fg_color="transparent")
        self.row2.pack(fill="x", pady=(0, 3))

        self.bytes_lbl = _label(self.row2, f"0 B / {format_bytes(self.total_size)}", 10, color=C_TEXT_SUB)
        self.bytes_lbl.pack(side="left")

        self.pct_lbl = _label(self.row2, "0%", 10, "bold", C_ACCENT)
        self.pct_lbl.pack(side="right")

        # Row 3: Progress bar
        self.prog_bar = ctk.CTkProgressBar(self.outer, height=3, corner_radius=1,
                                           progress_color=C_ACCENT, fg_color=C_HOVER)
        self.prog_bar.set(0)
        self.prog_bar.pack(fill="x", pady=(0, 5))

        # Row 4: speed + actions
        self.row4 = ctk.CTkFrame(self.outer, fg_color="transparent")
        self.row4.pack(fill="x")

        self.speed_lbl = _label(self.row4, "Waiting...", 10, color=C_TEXT_SUB)
        self.speed_lbl.pack(side="left")

        self.actions = ctk.CTkFrame(self.row4, fg_color="transparent")
        self.actions.pack(side="right")

        if self.direction == "SEND":
            self.pause_btn = _btn(
                self.actions, "⏸", command=self._toggle_pause,
                width=26, height=24, font_size=10, radius=2,
            )
            self.pause_btn.pack(side="left", padx=(0, 4))

            self.cancel_btn = _btn(
                self.actions, "✕", command=self._do_cancel,
                width=26, height=24, font_size=10, fg=C_HOVER,
                hover="#4A1A1A", text_color=C_ERROR, radius=2,
            )
            self.cancel_btn.pack(side="left")

        _divider(self)

    def _toggle_pause(self):
        if not self._is_paused:
            if self.on_pause:
                self.on_pause()
            self.mark_paused()
        else:
            if self.on_resume:
                self.on_resume()
            self.mark_resumed()

    def _do_cancel(self):
        if self.on_cancel:
            self.on_cancel()
        self.mark_failed("Cancelled by user")

    def _do_retry(self):
        if self.on_retry:
            self.on_retry()

    def _cancel_wait_timer(self):
        if getattr(self, "_wait_after_id", None):
            try:
                self.after_cancel(self._wait_after_id)
            except Exception:
                pass
            self._wait_after_id = None

    def _wait_tick(self):
        self._wait_after_id = None
        if self.status != "waiting":
            return
        if self._wait_remaining > 0:
            self._wait_remaining -= 1
            try:
                wait_text = (
                    f"Waiting for {self._wait_peer} to accept... ({self._wait_remaining}s)"
                    if self._wait_peer
                    else f"Waiting... ({self._wait_remaining}s)"
                )
                self.speed_lbl.configure(text=wait_text)
                self._wait_after_id = self.after(1000, self._wait_tick)
            except Exception:
                pass

    def mark_waiting(self, peer_name: str = "", timeout_sec: int = 30):
        self._cancel_wait_timer()
        self.status = "waiting"
        self._wait_remaining = timeout_sec
        self._wait_peer = peer_name
        try:
            self.status_lbl.configure(text="⏳ Waiting", text_color=C_WARN)
            wait_text = (
                f"Waiting for {peer_name} to accept... ({timeout_sec}s)"
                if peer_name
                else f"Waiting... ({timeout_sec}s)"
            )
            self.speed_lbl.configure(text=wait_text)
            self.prog_bar.configure(mode="indeterminate")
            self.prog_bar.start()
            if hasattr(self, "pause_btn"):
                self.pause_btn.configure(state="disabled")
            self._wait_after_id = self.after(1000, self._wait_tick)
        except Exception:
            pass

    def mark_paused(self):
        self._is_paused = True
        self.status = "paused"
        try:
            self.status_lbl.configure(text="● Paused", text_color=C_WARN)
            if hasattr(self, "pause_btn"):
                self.pause_btn.configure(text="▶")
            self.speed_lbl.configure(text="Paused")
        except Exception:
            pass

    def mark_resumed(self):
        self._is_paused = False
        self.status = "in_progress"
        try:
            self.status_lbl.configure(text="● In Progress", text_color=C_ACCENT)
            if hasattr(self, "pause_btn"):
                self.pause_btn.configure(text="⏸", state="normal")
            self.speed_lbl.configure(text="Resuming...")
        except Exception:
            pass

    def update_progress(self, bytes_done: int, total_size: int):
        self._cancel_wait_timer()
        self.bytes_done = bytes_done
        self.total_size = total_size
        frac = bytes_done / total_size if total_size > 0 else 0.0
        pct = int(frac * 100)

        now = time.time()
        dt = now - self._last_time
        if dt >= 0.5:
            self._speed = (bytes_done - self._last_bytes) / dt
            self._last_bytes = bytes_done
            self._last_time = now

        try:
            if self.prog_bar.cget("mode") == "indeterminate":
                self.prog_bar.stop()
                self.prog_bar.configure(mode="determinate")
                self.status = "in_progress"
                self.status_lbl.configure(text="● In Progress", text_color=C_ACCENT)
                if hasattr(self, "pause_btn"):
                    self.pause_btn.configure(state="normal")

            self.prog_bar.set(frac)
            self.bytes_lbl.configure(text=f"{format_bytes(bytes_done)} / {format_bytes(total_size)}")
            self.pct_lbl.configure(text=f"{pct}%", text_color=C_ACCENT)
            speed_str = f"{format_bytes(int(self._speed))}/s" if self._speed > 0 else "Calculating..."
            self.speed_lbl.configure(text=speed_str)
        except Exception:
            pass

    def mark_complete(self, hash_ok: bool = True, message: str = "", saved_path: str = ""):
        self._cancel_wait_timer()
        self.status = "completed"
        if saved_path:
            self.saved_path = saved_path
        try:
            for child in self.winfo_children():
                child.destroy()

            row = ctk.CTkFrame(self, fg_color="transparent")
            row.pack(fill="x", padx=4, pady=8)

            icon_frame = ctk.CTkFrame(row, width=28, height=28, fg_color=C_SUCCESS, corner_radius=2)
            icon_frame.pack(side="left", padx=(0, 10))
            icon_frame.pack_propagate(False)
            _label(icon_frame, "✓", 12, "bold", "#FFFFFF").pack(expand=True)

            info = ctk.CTkFrame(row, fg_color="transparent")
            info.pack(side="left", fill="x", expand=True)
            _label(info, self.filename, 12, "normal").pack(anchor="w")

            if self.direction == "SEND":
                role = f"Sent Successfully to {self.peer_name}"
            else:
                role = f"Received from {self.peer_name}"
            _label(info, f"{role}  ·  {format_bytes(self.total_size)}", 10, color=C_TEXT_SUB).pack(anchor="w")

            right = ctk.CTkFrame(row, fg_color="transparent")
            right.pack(side="right")

            tgt = self.saved_path
            if tgt and os.path.exists(tgt):
                _btn(right, "Open", command=lambda p=tgt: _open_path(p),
                     width=48, height=24, fg=C_ACCENT, hover=C_ACCENT_DIM,
                     text_color="#FFFFFF", bw=0, radius=2, font_size=10).pack(side="left", padx=(0, 4))
                _btn(right, "Folder", command=lambda p=os.path.dirname(tgt): _open_path(p),
                     width=48, height=24, radius=2, font_size=10).pack(side="left", padx=(0, 8))

            if hash_ok:
                _label(right, "✓ SHA-256 Verified", 10, "bold", C_SUCCESS).pack(side="left", padx=(0, 8))

            _label(right, "● Completed", 10, "bold", C_SUCCESS).pack(side="left")
            _divider(self)
        except Exception:
            pass

    def mark_failed(self, message: str = ""):
        self._cancel_wait_timer()
        self.status = "failed"
        try:
            is_timeout = "timed out" in (message or "").lower()
            try:
                self.prog_bar.stop()
                self.prog_bar.configure(mode="determinate")
            except Exception:
                pass

            if is_timeout:
                self.status_lbl.configure(text="⏱ Timed Out", text_color=C_WARN)
            else:
                self.status_lbl.configure(text="● Failed", text_color=C_ERROR)

            if self.bytes_done == 0 or self.total_size <= 0:
                try:
                    self.prog_bar.pack_forget()
                    self.row2.pack_forget()
                except Exception:
                    pass
            else:
                self.prog_bar.configure(progress_color=C_WARN if is_timeout else C_ERROR, height=3)

            if is_timeout:
                self.speed_lbl.configure(
                    text="No response after 30s. Click Retry when ready",
                    text_color=C_WARN,
                    font=_font(10, "bold"),
                )
            else:
                err = message or "Transfer failed."
                if not err.lower().startswith("error"):
                    err = f"Error: {err}"
                self.speed_lbl.configure(text=err[:80], text_color=C_ERROR,
                                         font=_font(10, "bold"))

            for child in self.actions.winfo_children():
                child.destroy()

            if self.on_retry:
                _btn(self.actions, "⟳ Retry", command=self._do_retry,
                     width=64, height=24,
                     fg=C_HOVER,
                     hover="#3D3214" if is_timeout else "#4A1A1A",
                     text_color=C_WARN if is_timeout else C_ERROR,
                     font_size=10).pack(side="right")
        except Exception as e:
            print(f"[TransferCard] mark_failed error: {e}")

    def mark_declined(self):
        self._cancel_wait_timer()
        self.status = "declined"
        try:
            self.status_lbl.configure(text="● Declined", text_color=C_TEXT_SUB)
            if self.bytes_done == 0:
                try:
                    self.prog_bar.pack_forget()
                    self.row2.pack_forget()
                except Exception:
                    pass
            self.speed_lbl.configure(text="Recipient declined.")
            for child in self.actions.winfo_children():
                child.destroy()
        except Exception:
            pass


def _open_path(path: str):
    try:
        if hasattr(os, "startfile"):
            os.startfile(path)
        else:
            import subprocess
            subprocess.Popen(["xdg-open", path])
    except Exception as e:
        print(f"[AirDude] Could not open: {path}: {e}")


# ==========================================
# Transfers Page
# ==========================================

class TransfersPage(ctk.CTkFrame):
    """Active + History transfer list with VS Code tab styling."""

    def __init__(self, master, network=None, on_send_files=None, app_ref=None, **kw):
        super().__init__(master, fg_color=C_BG, corner_radius=0, **kw)
        self.network = network
        self.on_send_files = on_send_files
        self.app_ref = app_ref
        self.transfer_cards: Dict[str, TransferCard] = {}
        self._local_history: List[dict] = []
        self._active_tab = "active"
        self.pack_propagate(False)
        self._build()

    @property
    def history_records(self) -> List[dict]:
        if self.app_ref and hasattr(self.app_ref, "history_records"):
            return self.app_ref.history_records
        return self._local_history

    @history_records.setter
    def history_records(self, val):
        if self.app_ref and hasattr(self.app_ref, "history_records"):
            self.app_ref.history_records = val
        else:
            self._local_history = val

    def _build(self):
        # Header
        hdr = ctk.CTkFrame(self, fg_color=C_PANEL, corner_radius=0)
        hdr.pack(fill="x")
        inner_hdr = ctk.CTkFrame(hdr, fg_color="transparent")
        inner_hdr.pack(fill="x", padx=20, pady=14)

        left = ctk.CTkFrame(inner_hdr, fg_color="transparent")
        left.pack(side="left")
        _label(left, "Transfers", 16, "bold").pack(anchor="w")
        _label(left, "Monitor active and completed file transfers.", 11, color=C_TEXT_SUB).pack(anchor="w", pady=(2, 0))

        _btn(inner_hdr, "+ Send Files", command=self._on_send,
             width=90, height=28, fg=C_ACCENT, hover=C_ACCENT_DIM,
             text_color="#FFFFFF", bw=0, font_size=11, font_w="bold").pack(side="right", anchor="n", pady=2)

        # Tab / Filter bar
        tab_bar = ctk.CTkFrame(self, fg_color=C_HOVER, corner_radius=0, height=36)
        tab_bar.pack(fill="x")
        tab_bar.pack_propagate(False)

        inner_tab = ctk.CTkFrame(tab_bar, fg_color="transparent")
        inner_tab.pack(side="left", padx=14, pady=0, fill="y")

        self._filter = "all"
        self.filter_btns = {}
        for f_key, f_name in [("all", "All Transfers"), ("incoming", "Incoming"), ("outgoing", "Outgoing"), ("completed", "Completed"), ("history", "History Logs")]:
            btn = _btn(
                inner_tab, f_name, command=lambda k=f_key: self._switch_filter(k),
                height=28, fg=C_CARD if f_key == "all" else "transparent",
                hover=C_HOVER, bw=0, font_size=11, font_w="bold" if f_key == "all" else "normal",
                text_color=C_TEXT if f_key == "all" else C_TEXT_SUB,
            )
            btn.pack(side="left", padx=(0, 4), pady=4)
            self.filter_btns[f_key] = btn

        self.clear_btn = _btn(
            tab_bar, "Clear History", command=self._clear_history,
            width=96, height=24, fg="transparent", hover=C_HOVER,
            border=C_BORDER, bw=1, text_color=C_TEXT, radius=4, font_size=10,
        )

        # Scrollable content
        self.scroll = ctk.CTkScrollableFrame(self, fg_color=C_BG, corner_radius=0)
        self.scroll.pack(fill="both", expand=True)

        # Active container
        self.active_cont = ctk.CTkFrame(self.scroll, fg_color="transparent")
        self.active_cont.pack(fill="x", padx=20)

        self.empty_active_lbl = _label(
            self.active_cont,
            "No transfers in this view.\nSend or receive files to see progress.",
            13, color=C_TEXT_SUB,
        )
        self.empty_active_lbl.pack(pady=60)

        # History container
        self.history_cont = ctk.CTkFrame(self.scroll, fg_color="transparent")

    def _switch_filter(self, filter_key: str):
        self._filter = filter_key
        for k, btn in self.filter_btns.items():
            if k == filter_key:
                btn.configure(fg_color=C_CARD, text_color=C_TEXT, font=_font(11, "bold"))
            else:
                btn.configure(fg_color="transparent", text_color=C_TEXT_SUB, font=_font(11))

        if filter_key == "history":
            self.active_cont.pack_forget()
            self.history_cont.pack(fill="x", padx=20)
            self.clear_btn.pack(side="right", padx=14, pady=6)
            self._rebuild_history()
        else:
            self.clear_btn.pack_forget()
            self.history_cont.pack_forget()
            self.active_cont.pack(fill="x", padx=20)
            self._apply_filter()

    def _apply_filter(self):
        visible_count = 0
        for card in self.transfer_cards.values():
            show = False
            if self._filter == "all":
                show = True
            elif self._filter == "incoming":
                show = (card.direction == "RECEIVE")
            elif self._filter == "outgoing":
                show = (card.direction == "SEND")
            elif self._filter == "completed":
                show = (card.status == "completed")

            if show:
                card.pack(fill="x", pady=(0, 4))
                visible_count += 1
            else:
                card.pack_forget()

        if visible_count == 0:
            self.empty_active_lbl.pack(pady=60)
        else:
            self.empty_active_lbl.pack_forget()

    def _on_send(self):
        if self.on_send_files:
            self.on_send_files()

    def _clear_history(self):
        if not self.history_records:
            return
        if messagebox.askyesno("Clear History", "Clear all transfer history? This cannot be undone."):
            if self.app_ref and hasattr(self.app_ref, "clear_all_history"):
                self.app_ref.clear_all_history()
            else:
                clear_history_file()
                self.history_records = []
                self._rebuild_history()

    def _get_base_tid(self, tid: str) -> str:
        return tid.rsplit("_", 1)[0] if "_" in tid else tid

    def _pack_card_top(self, card):
        self.empty_active_lbl.pack_forget()
        children = [w for w in self.active_cont.winfo_children()
                    if w != self.empty_active_lbl and w != card]
        if children:
            card.pack(fill="x", pady=(0, 4), before=children[0])
        else:
            card.pack(fill="x", pady=(0, 4))

    def handle_transfer_event(self, event: dict, replay: bool = False):
        tid = event.get("transfer_id", "")
        base_tid = self._get_base_tid(tid)
        etype = event.get("type", "")
        filename = event.get("filename", "file")
        total_size = event.get("total_size", 0)
        direction = event.get("direction", "SEND")
        peer_name = event.get("peer_name", "Peer")
        status = event.get("status", "in_progress")
        saved_path = event.get("saved_path", "")
        file_path = event.get("file_path", "")
        target_ip = event.get("target_ip", "")
        target_port = event.get("target_port", 45455)

        def do_pause():
            if self.network:
                self.network.pause_transfer(base_tid)

        def do_resume():
            if self.network:
                self.network.resume_transfer(base_tid)

        def do_cancel():
            if self.network:
                self.network.cancel_transfer(base_tid)

        def do_retry(fp=file_path, tip=target_ip, tport=target_port, pname=peer_name):
            if fp and tip and self.network:
                threading.Thread(
                    target=lambda: self.network.send_files(
                        target_ip=tip,
                        filepaths=[fp] if isinstance(fp, str) else list(fp),
                        port=tport,
                        sender_name=self.network.hostname,
                        peer_name=pname,
                    ),
                    daemon=True,
                ).start()
            elif self.on_send_files:
                self.on_send_files()

        if etype == "TRANSFER_START":
            if tid not in self.transfer_cards:
                card = TransferCard(
                    self.active_cont,
                    transfer_id=tid, filename=filename, total_size=total_size,
                    direction=direction, peer_name=peer_name,
                    on_pause=do_pause, on_resume=do_resume,
                    on_cancel=do_cancel, on_retry=do_retry,
                    saved_path=saved_path, file_path=file_path,
                    target_ip=target_ip, target_port=target_port,
                )
                self._pack_card_top(card)
                self.transfer_cards[tid] = card
            else:
                card = self.transfer_cards[tid]

            if event.get("waiting", False):
                card.mark_waiting(peer_name=peer_name)
            else:
                if card.status == "waiting":
                    card._cancel_wait_timer()
                    try:
                        card.prog_bar.stop()
                        card.prog_bar.configure(mode="determinate")
                        card.status = "in_progress"
                        card.status_lbl.configure(text="● In Progress", text_color=C_ACCENT)
                        card.speed_lbl.configure(text="Starting...")
                        if hasattr(card, "pause_btn"):
                            card.pause_btn.configure(state="normal")
                    except Exception:
                        pass
            self._update_tab_count()

        elif etype == "TRANSFER_PROGRESS":
            if tid in self.transfer_cards:
                self.transfer_cards[tid].update_progress(
                    event.get("bytes_done", 0),
                    event.get("total_size", self.transfer_cards[tid].total_size),
                )
            else:
                card = TransferCard(
                    self.active_cont,
                    transfer_id=tid, filename=filename, total_size=total_size,
                    direction=direction, peer_name=peer_name,
                    on_pause=do_pause, on_resume=do_resume,
                    on_cancel=do_cancel, on_retry=do_retry,
                    saved_path=saved_path, file_path=file_path,
                )
                self._pack_card_top(card)
                self.transfer_cards[tid] = card
                card.update_progress(event.get("bytes_done", 0), total_size)
            self._update_tab_count()

        elif etype == "TRANSFER_PAUSED":
            if tid in self.transfer_cards:
                self.transfer_cards[tid].mark_paused()

        elif etype == "TRANSFER_RESUMED":
            if tid in self.transfer_cards:
                self.transfer_cards[tid].mark_resumed()

        elif etype == "TRANSFER_COMPLETE":
            if tid not in self.transfer_cards:
                card = TransferCard(
                    self.active_cont,
                    transfer_id=tid, filename=filename, total_size=total_size,
                    direction=direction, peer_name=peer_name,
                    on_retry=do_retry, saved_path=saved_path, file_path=file_path,
                )
                self._pack_card_top(card)
                self.transfer_cards[tid] = card

            card = self.transfer_cards[tid]
            card.update_progress(total_size, total_size)
            raw_hash_ok = event.get("hash_ok", True)
            hash_verified = True if raw_hash_ok is None else bool(raw_hash_ok)
            card.mark_complete(
                hash_ok=hash_verified,
                message=event.get("message", ""),
                saved_path=saved_path or card.saved_path,
            )
            if not replay and not self.app_ref:
                rec = {
                    "transfer_id": tid, "filename": filename, "total_size": total_size,
                    "direction": direction, "peer_name": peer_name, "status": "completed",
                    "hash_ok": hash_verified,
                    "saved_path": saved_path or card.saved_path,
                    "elapsed": event.get("elapsed", 0),
                    "timestamp": time.strftime("%H:%M:%S"),
                }
                self.history_records = add_history_entry(rec)
            self._update_tab_count()

        elif etype == "TRANSFER_FAILED":
            if tid not in self.transfer_cards:
                card = TransferCard(
                    self.active_cont,
                    transfer_id=tid, filename=filename, total_size=total_size or 1,
                    direction=direction, peer_name=peer_name,
                    on_retry=do_retry, file_path=file_path,
                    target_ip=target_ip, target_port=target_port,
                )
                self._pack_card_top(card)
                self.transfer_cards[tid] = card

            card = self.transfer_cards[tid]
            card.on_retry = do_retry
            if file_path:
                card.file_path = file_path
            if target_ip:
                card.target_ip = target_ip
            if target_port:
                card.target_port = target_port

            if status == "declined":
                card.mark_declined()
            else:
                card.mark_failed(message=event.get("message", ""))

            if not replay and not self.app_ref:
                rec = {
                    "transfer_id": tid, "filename": filename, "total_size": total_size,
                    "direction": direction, "peer_name": peer_name, "status": status,
                    "hash_ok": False, "message": event.get("message", ""),
                    "timestamp": time.strftime("%H:%M:%S"),
                }
                self.history_records = add_history_entry(rec)
            self._update_tab_count()

    def _update_tab_count(self):
        total = len(self.transfer_cards)
        n_in = sum(1 for c in self.transfer_cards.values() if c.direction == "RECEIVE")
        n_out = sum(1 for c in self.transfer_cards.values() if c.direction == "SEND")
        n_done = sum(1 for c in self.transfer_cards.values() if c.status == "completed")
        try:
            if "all" in self.filter_btns:
                self.filter_btns["all"].configure(text=f"All Transfers ({total})")
            if "incoming" in self.filter_btns:
                self.filter_btns["incoming"].configure(text=f"Incoming ({n_in})")
            if "outgoing" in self.filter_btns:
                self.filter_btns["outgoing"].configure(text=f"Outgoing ({n_out})")
            if "completed" in self.filter_btns:
                self.filter_btns["completed"].configure(text=f"Completed ({n_done})")
            self._apply_filter()
        except Exception:
            pass

    def _rebuild_history(self):
        for w in self.history_cont.winfo_children():
            w.destroy()

        records = self.history_records
        if not records:
            _label(self.history_cont,
                   "No transfer history yet.\nCompleted and failed transfers appear here.",
                   13, color=C_TEXT_SUB).pack(pady=60)
            return

        for rec in records:
            self._build_history_row(rec)

    def _build_history_row(self, rec: dict):
        is_ok = rec.get("status") == "completed"
        fg_col = C_SUCCESS if is_ok else C_ERROR

        row = ctk.CTkFrame(self.history_cont, fg_color="transparent", corner_radius=0)
        row.pack(fill="x", pady=0)

        inner = ctk.CTkFrame(row, fg_color="transparent")
        inner.pack(fill="x", padx=4, pady=7)

        # Icon
        sym = "✓" if is_ok else "✕"
        icon_frame = ctk.CTkFrame(inner, width=26, height=26, fg_color=C_HOVER, corner_radius=2)
        icon_frame.pack(side="left", padx=(0, 10))
        icon_frame.pack_propagate(False)
        _label(icon_frame, sym, 11, "bold", fg_col).pack(expand=True)

        # Info
        info = ctk.CTkFrame(inner, fg_color="transparent")
        info.pack(side="left", fill="x", expand=True)
        _label(info, rec.get("filename", "file"), 12, "normal").pack(anchor="w")

        role = f"Sent to {rec.get('peer_name')}" if rec.get("direction") == "SEND" else f"From {rec.get('peer_name')}"
        sub = f"{role}  ·  {format_bytes(rec.get('total_size', 0))}  ·  {rec.get('timestamp', '')}"
        _label(info, sub, 10, color=C_TEXT_SUB).pack(anchor="w")

        # Right actions
        right = ctk.CTkFrame(inner, fg_color="transparent")
        right.pack(side="right")

        sp = rec.get("saved_path")
        if sp and os.path.exists(sp):
            _btn(right, "Open", command=lambda p=sp: _open_path(p),
                 width=46, height=22, fg=C_ACCENT, hover=C_ACCENT_DIM,
                 text_color="#FFFFFF", bw=0, radius=2, font_size=10).pack(side="left", padx=(0, 4))
            _btn(right, "Folder", command=lambda p=os.path.dirname(sp): _open_path(p),
                 width=46, height=22, radius=2, font_size=10).pack(side="left", padx=(0, 8))

        if is_ok and rec.get("hash_ok"):
            _label(right, "✓ SHA-256", 10, "bold", C_SUCCESS).pack(side="left", padx=(0, 8))
        elif rec.get("message"):
            _label(right, rec["message"][:30], 10, color=C_ERROR).pack(side="left", padx=(0, 8))

        _label(right, "● " + ("Completed" if is_ok else rec.get("status", "Failed").capitalize()),
               10, "bold", fg_col).pack(side="left")
        _divider(row)


# ==========================================
# History Page
# ==========================================

class HistoryPage(ctk.CTkFrame):
    """Dedicated History page — mirrors history tab content."""

    def __init__(self, master, app_ref, **kw):
        super().__init__(master, fg_color=C_BG, corner_radius=0, **kw)
        self.app_ref = app_ref
        self.pack_propagate(False)
        self._build()

    def _build(self):
        hdr = ctk.CTkFrame(self, fg_color=C_PANEL, corner_radius=0)
        hdr.pack(fill="x")
        inner = ctk.CTkFrame(hdr, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=14)

        left = ctk.CTkFrame(inner, fg_color="transparent")
        left.pack(side="left")
        _label(left, "Transfer History", 16, "bold").pack(anchor="w")
        _label(left, "All completed and failed transfers.", 11, color=C_TEXT_SUB).pack(anchor="w")

        _btn(inner, "Clear All", command=self._clear,
             width=84, height=28, fg=C_CARD, hover=C_CARD_HOVER,
             border=C_BORDER, bw=1, text_color=C_TEXT, radius=4, font_size=11).pack(side="right", anchor="n")

        self.scroll = ctk.CTkScrollableFrame(self, fg_color=C_BG, corner_radius=0)
        self.scroll.pack(fill="both", expand=True)

        self.refresh()

    def refresh(self):
        for w in self.scroll.winfo_children():
            w.destroy()

        records = self.app_ref.history_records if self.app_ref else []
        if not records:
            _label(self.scroll,
                   "No transfer history yet.\nCompleted and failed transfers appear here.",
                   13, color=C_TEXT_SUB).pack(pady=80)
            return

        for rec in records:
            self._build_row(rec)

    def _build_row(self, rec: dict):
        is_ok = rec.get("status") == "completed"
        fg_col = C_SUCCESS if is_ok else C_ERROR

        row = ctk.CTkFrame(self.scroll, fg_color="transparent", corner_radius=0)
        row.pack(fill="x", padx=20, pady=0)

        inner = ctk.CTkFrame(row, fg_color="transparent")
        inner.pack(fill="x", padx=4, pady=7)

        sym = "✓" if is_ok else "✕"
        icon_frame = ctk.CTkFrame(inner, width=26, height=26, fg_color=C_HOVER, corner_radius=2)
        icon_frame.pack(side="left", padx=(0, 10))
        icon_frame.pack_propagate(False)
        _label(icon_frame, sym, 11, "bold", fg_col).pack(expand=True)

        info = ctk.CTkFrame(inner, fg_color="transparent")
        info.pack(side="left", fill="x", expand=True)
        _label(info, rec.get("filename", "file"), 12, "normal").pack(anchor="w")

        role = f"Sent to {rec.get('peer_name')}" if rec.get("direction") == "SEND" else f"From {rec.get('peer_name')}"
        sub = f"{role}  ·  {format_bytes(rec.get('total_size', 0))}  ·  {rec.get('timestamp', '')}"
        _label(info, sub, 10, color=C_TEXT_SUB).pack(anchor="w")

        right = ctk.CTkFrame(inner, fg_color="transparent")
        right.pack(side="right")

        sp = rec.get("saved_path")
        if sp and os.path.exists(sp):
            _btn(right, "Open", command=lambda p=sp: _open_path(p),
                 width=46, height=22, fg=C_ACCENT, hover=C_ACCENT_DIM,
                 text_color="#FFFFFF", bw=0, radius=2, font_size=10).pack(side="left", padx=(0, 4))
            _btn(right, "Folder", command=lambda p=os.path.dirname(sp): _open_path(p),
                 width=46, height=22, radius=2, font_size=10).pack(side="left", padx=(0, 8))

        if is_ok and rec.get("hash_ok"):
            _label(right, "✓ SHA-256", 10, "bold", C_SUCCESS).pack(side="left", padx=(0, 8))

        _label(right, "● " + ("Completed" if is_ok else rec.get("status", "Failed").capitalize()),
               10, "bold", fg_col).pack(side="left")
        _divider(row)

    def _clear(self):
        if not self.app_ref or not self.app_ref.history_records:
            return
        if messagebox.askyesno("Clear History", "Clear all transfer history? This cannot be undone."):
            if self.app_ref:
                self.app_ref.clear_all_history()
            self.refresh()


# ==========================================
# Settings Page
# ==========================================

class SettingsPage(ctk.CTkFrame):
    """VS Code flat list-based settings page."""

    def __init__(self, master, app_ref, **kw):
        super().__init__(master, fg_color=C_BG, corner_radius=0, **kw)
        self.app_ref = app_ref
        self._section_widgets: Dict[str, ctk.CTkBaseClass] = {}
        self.pack_propagate(False)
        self._build()

    def _build(self):
        hdr = ctk.CTkFrame(self, fg_color=C_PANEL, corner_radius=0)
        hdr.pack(fill="x")
        inner_hdr = ctk.CTkFrame(hdr, fg_color="transparent")
        inner_hdr.pack(fill="x", padx=28, pady=12)
        _label(inner_hdr, "Settings", 14, "bold", C_TEXT).pack(side="left", anchor="w")

        self.scroll = ctk.CTkScrollableFrame(self, fg_color=C_BG, corner_radius=0)
        self.scroll.pack(fill="both", expand=True)

        self.content = ctk.CTkFrame(self.scroll, fg_color="transparent")
        self.content.pack(fill="x", padx=28, pady=(8, 24))

        # ── Device Identity ──
        id_card = self._section("identity", "Device Identity")

        name_row = SettingsRow(
            id_card,
            "Device Name",
            "Name visible to other AirDude devices on your local network.",
        )
        name_row.pack(fill="x")

        self.name_feedback = _label(name_row.right_slot, "", 10, color=C_SUCCESS)
        self.name_feedback.pack(side="left", padx=(0, 8))

        self.name_entry = ctk.CTkEntry(
            name_row.right_slot, width=180, height=28, corner_radius=4,
            fg_color=C_HOVER, border_color=C_BORDER, border_width=1,
            text_color=C_TEXT, font=_font(11),
        )
        self.name_entry.insert(0, self.app_ref.device_name)
        self.name_entry.pack(side="left", padx=(0, 6))

        _btn(name_row.right_slot, "Save", command=self._save_name,
             width=54, height=28, fg=C_ACCENT, hover=C_ACCENT_DIM,
             text_color="#FFFFFF", bw=0, radius=4, font_size=11, font_w="bold").pack(side="left")

        os_row = SettingsRow(id_card, "Operating System")
        os_row.pack(fill="x")
        _label(os_row.right_slot, self.app_ref.network.os_name, 11, color=C_TEXT_SUB).pack(side="right")

        ip_row = SettingsRow(id_card, "Local IP Address", show_divider=False)
        ip_row.pack(fill="x")
        _label(ip_row.right_slot, self.app_ref.network.local_ip, 11, color=C_TEXT_SUB).pack(side="right")

        # ── Appearance ──
        app_card = self._section("appearance", "Appearance")

        theme_row = SettingsRow(
            app_card,
            "Theme Mode",
            "Choose Dark, Light, or follow your OS preference.",
            show_divider=False,
        )
        theme_row.pack(fill="x")
        cur_mode = self.app_ref.settings.get("appearance_mode", "Dark").capitalize()
        self.theme_menu = ctk.CTkOptionMenu(
            theme_row.right_slot,
            values=["System", "Light", "Dark"],
            command=self._change_theme,
            width=110, height=28, corner_radius=4,
            fg_color=C_HOVER, button_color=C_HOVER,
            button_hover_color=C_CARD_HOVER,
            text_color=C_TEXT,
            dropdown_fg_color=C_CARD,
            dropdown_text_color=C_TEXT,
            dropdown_hover_color=C_HOVER,
            font=_font(11), dropdown_font=_font(11),
        )
        self.theme_menu.set(cur_mode)
        self.theme_menu.pack(side="right")

        # ── File Storage ──
        storage_card = self._section("storage", "File Storage")

        save_row = SettingsRow(
            storage_card,
            "Default Save Directory",
            os.path.abspath(self.app_ref.save_dir),
            show_divider=False,
        )
        save_row.pack(fill="x")
        # Keep reference to description label if we need to update it
        for child in save_row.winfo_children():
            for sub in child.winfo_children():
                if isinstance(sub, ctk.CTkFrame):
                    lbls = [w for w in sub.winfo_children() if isinstance(w, ctk.CTkLabel)]
                    if len(lbls) >= 2:
                        self.save_lbl = lbls[1]
                        break
        if not hasattr(self, "save_lbl"):
            self.save_lbl = _label(save_row.right_slot, os.path.abspath(self.app_ref.save_dir), 10, color=C_TEXT_SUB)
            self.save_lbl.pack(side="right", padx=(0, 8))

        _btn(save_row.right_slot, "Change", command=self._change_dir,
             width=66, height=28, fg=C_ACCENT, hover=C_ACCENT_DIM,
             text_color="#FFFFFF", bw=0, radius=4, font_size=11).pack(side="right")

        # ── Network ──
        net_card = self._section("network", "Network Ports")

        port_items = [
            ("UDP Discovery Port", "Broadcast discovery listener port", str(self.app_ref.udp_port), True),
            ("TCP Transfer Port", "Incoming file stream port", str(self.app_ref.tcp_port), True),
            ("Broadcast Port", "LAN peer announcement target port", str(self.app_ref.broadcast_port), False),
        ]
        for label, desc, val, div in port_items:
            r = SettingsRow(net_card, label, desc, show_divider=div)
            r.pack(fill="x")
            _label(r.right_slot, val, 11, "bold", C_TEXT_SUB).pack(side="right")

        _label(self.content, "⚠  Port changes require an application restart.", 10, color=C_WARN).pack(
            anchor="w", pady=(8, 0))

        # ── About / Brand section ──
        about_card = self._section("about", "About")

        about_row = ctk.CTkFrame(about_card, fg_color="transparent")
        about_row.pack(fill="x", padx=16, pady=12)
        about_logo = get_brand_image("logo_shield.png", (42, 42))
        if about_logo:
            self._settings_logo_ref = about_logo
            _brand_label(about_row, about_logo, 42, 42, side="left", padx=(0, 14))
        about_txt = ctk.CTkFrame(about_row, fg_color="transparent")
        about_txt.pack(side="left")
        _label(about_txt, "AirDude", 14, "bold", "#E2E2E2").pack(anchor="w")
        _label(about_txt, "Version 1.0.0  ·  Local P2P file sharing over LAN", 10, color=C_TEXT_SUB).pack(
            anchor="w", pady=(2, 0))
        tk.Frame(about_card, height=1, bg="#343438", bd=0, highlightthickness=0).pack(fill="x", padx=1)

        for idx, (label_txt, val_txt) in enumerate([
            ("Protocol", "UDP Discovery + TCP Transfer"),
            ("Encryption", "LAN only (no external routing)"),
            ("Integrity", "SHA-256 per-file hash verification"),
            ("License", "Open Source"),
        ]):
            r = SettingsRow(about_card, label_txt, show_divider=(idx < 3))
            r.pack(fill="x")
            _label(r.right_slot, val_txt, 11, color=C_TEXT_SUB).pack(side="right")

    def _section(self, key: str, title: str) -> ctk.CTkFrame:
        lbl = _label(self.content, title, 13, "bold", "#E2E2E2")
        lbl.pack(anchor="w", pady=(18, 8))
        card = ctk.CTkFrame(
            self.content,
            fg_color="#232326",
            corner_radius=8,
            border_width=1,
            border_color="#343438",
        )
        card.pack(fill="x")
        self._section_widgets[key] = lbl
        return card

    def scroll_to_section(self, key: str):
        w = self._section_widgets.get(key)
        if not w:
            return
        try:
            self.scroll.update_idletasks()
            canvas = getattr(self.scroll, "_parent_canvas", None)
            if canvas:
                total_h = max(1, self.content.winfo_height())
                y = max(0, w.winfo_y() - 12)
                canvas.yview_moveto(min(1.0, y / total_h))
        except Exception:
            pass

    def _save_name(self):
        name = self.name_entry.get().strip()
        if name:
            self.app_ref.update_device_name(name)
            self.name_feedback.configure(text="✓ Saved")
            self.after(3000, lambda: self.name_feedback.configure(text=""))

    def _change_theme(self, choice: str):
        ctk.set_appearance_mode(choice.lower())
        self.app_ref.settings["appearance_mode"] = choice
        save_settings(self.app_ref.settings)

    def _change_dir(self):
        chosen = filedialog.askdirectory(title="Select Save Directory")
        if chosen:
            self.app_ref.save_dir = chosen
            self.app_ref.network.save_directory = chosen
            os.makedirs(chosen, exist_ok=True)
            self.save_lbl.configure(text=os.path.abspath(chosen))
            self.app_ref.settings["save_dir"] = chosen
            save_settings(self.app_ref.settings)


# ==========================================
# Incoming Transfer Modal
# ==========================================

class IncomingTransferModal(ctk.CTkToplevel):
    """Dark VS Code-style incoming transfer dialog."""

    TIMEOUT = 30

    def __init__(self, parent, request_payload: dict, default_save_dir: str = "received_files", **kw):
        super().__init__(parent, **kw)
        self.request = request_payload
        self.default_save_dir = default_save_dir
        self._auto_decline_job = None
        self._remaining = self.TIMEOUT
        self.custom_folder = None
        self.save_opt = ctk.StringVar(value="Downloads / AirDude")

        self.title("Incoming Transfer - AirDude")
        self.configure(fg_color=C_CARD)
        self.resizable(False, False)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._on_decline)

        self._build()
        self.update_idletasks()
        try:
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            x, y = max(0, (sw - 480) // 2), max(0, (sh - 560) // 2)
            self.geometry(f"480x560+{x}+{y}")
        except Exception:
            self.geometry("480x560")

        try:
            self.grab_set()
        except Exception:
            pass
        try:
            self.lift()
            self.focus_force()
        except Exception:
            pass
        self._tick()

    def _build(self):
        # Always-visible bottom bar (pack side=bottom first)
        bottom = ctk.CTkFrame(self, fg_color=C_HOVER, corner_radius=0)
        bottom.pack(side="bottom", fill="x")
        bot_inner = ctk.CTkFrame(bottom, fg_color="transparent")
        bot_inner.pack(fill="x", padx=20, pady=12)

        _btn(bot_inner, "Decline", command=self._on_decline,
             width=90, height=34, font_size=12).pack(side="left")

        self.countdown_lbl = _label(bot_inner, f"Auto-declining in {self.TIMEOUT}s", 10, color=C_TEXT_SUB)
        self.countdown_lbl.pack(side="left", padx=(12, 0))

        _btn(bot_inner, "✓  Accept Transfer", command=self._on_accept,
             width=140, height=34, fg=C_ACCENT, hover=C_ACCENT_DIM,
             text_color="#FFFFFF", bw=0, font_size=12, font_w="bold").pack(side="right")

        # Main content
        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=24, pady=(20, 12))

        # Header row
        hdr = ctk.CTkFrame(content, fg_color="transparent")
        hdr.pack(fill="x", pady=(0, 14))

        # Official brand logo or fallback badge
        logo_img = get_brand_image("logo_shield.png", (40, 40))
        if logo_img:
            self._modal_logo_ref = logo_img
            if not _brand_label(hdr, logo_img, 40, 40, side="left"):
                # TclError fallback
                badge = ctk.CTkFrame(hdr, width=40, height=40, fg_color=C_ACCENT, corner_radius=4)
                badge.pack(side="left")
                badge.pack_propagate(False)
        else:
            badge = ctk.CTkFrame(hdr, width=40, height=40, fg_color=C_ACCENT, corner_radius=4)
            badge.pack(side="left")
            badge.pack_propagate(False)
            _label(badge, "↙", 18, "bold", "#FFFFFF").pack(expand=True)

        _btn(hdr, "✕", command=self._on_decline,
             width=28, height=28, fg="transparent", hover=C_HOVER, bw=0, font_size=12).pack(side="right")

        # Title
        _label(content, "Incoming Transfer", 18, "bold").pack(anchor="w")

        sender = self.request.get("sender_name", "Remote Device")
        total_files = self.request.get("total_files", 1)
        total_size = self.request.get("total_size", self.request.get("filesize", 0))

        sub = f"{sender} wants to send you {total_files} file{'s' if total_files > 1 else ''}."
        _label(content, sub, 12, color=C_TEXT_SUB).pack(anchor="w", pady=(4, 14))

        # Sender card
        sc = ctk.CTkFrame(content, fg_color=C_HOVER, corner_radius=4)
        sc.pack(fill="x", pady=(0, 10))
        sci = ctk.CTkFrame(sc, fg_color="transparent")
        sci.pack(fill="x", padx=12, pady=8)
        _label(sci, "⬡", 16).pack(side="left", padx=(0, 10))
        sb = ctk.CTkFrame(sci, fg_color="transparent")
        sb.pack(side="left")
        _label(sb, sender, 12, "bold").pack(anchor="w")
        _label(sb, f"LAN  ·  {self.request.get('sender_ip', '')}", 10, color=C_TEXT_SUB).pack(anchor="w")

        # File card
        fname = self.request.get("filename", "file")
        fc = ctk.CTkFrame(content, fg_color=C_HOVER, corner_radius=4)
        fc.pack(fill="x", pady=(0, 12))
        fci = ctk.CTkFrame(fc, fg_color="transparent")
        fci.pack(fill="x", padx=12, pady=8)
        _label(fci, "📦" if total_files > 1 else "📄", 16).pack(side="left", padx=(0, 10))
        fb = ctk.CTkFrame(fci, fg_color="transparent")
        fb.pack(side="left", fill="x", expand=True)
        _label(fb, fname, 12, "bold").pack(anchor="w")
        type_txt = f"{total_files} files" if total_files > 1 else (os.path.splitext(fname)[1].upper().lstrip(".") or "FILE") + " file"
        _label(fb, type_txt, 10, color=C_TEXT_SUB).pack(anchor="w")
        _label(fci, format_bytes(total_size), 12, "bold").pack(side="right")

        # Save location
        _label(content, "Save to", 11, "bold").pack(anchor="w", pady=(0, 4))
        save_drop = ctk.CTkOptionMenu(
            content,
            variable=self.save_opt,
            values=["Downloads / AirDude", "Desktop", "received_files", "Browse custom folder..."],
            command=self._on_save_change,
            height=30, fg_color=C_HOVER, button_color=C_HOVER,
            button_hover_color=C_CARD_HOVER, text_color=C_TEXT,
            dropdown_fg_color=C_CARD, dropdown_text_color=C_TEXT,
            dropdown_hover_color=C_HOVER, font=_font(11), dropdown_font=_font(11),
        )
        save_drop.pack(fill="x")

    def _tick(self):
        try:
            if not self.winfo_exists():
                return
            if self._remaining <= 0:
                self._on_decline()
                return
            self.countdown_lbl.configure(text=f"Auto-declining in {self._remaining}s")
            self._remaining -= 1
            self._auto_decline_job = self.after(1000, self._tick)
        except Exception:
            pass

    def _on_save_change(self, choice: str):
        if choice == "Browse custom folder...":
            chosen = filedialog.askdirectory(title="Select Destination")
            if chosen:
                self.custom_folder = chosen
                self.save_opt.set(f"Custom: {os.path.basename(chosen) or chosen}")
            else:
                self.save_opt.set("Downloads / AirDude")

    def _cancel_timer(self):
        if self._auto_decline_job:
            try:
                self.after_cancel(self._auto_decline_job)
            except Exception:
                pass
            self._auto_decline_job = None

    def _on_decline(self):
        self._cancel_timer()
        self.request["decision"]["accepted"] = False
        self.request["approval_event"].set()
        self.destroy()

    def _on_accept(self):
        self._cancel_timer()
        opt = self.save_opt.get()
        if opt == "Downloads / AirDude":
            dest = os.path.join(os.path.expanduser("~"), "Downloads", "AirDude")
        elif opt == "Desktop":
            dest = os.path.join(os.path.expanduser("~"), "Desktop")
        elif self.custom_folder:
            dest = self.custom_folder
        else:
            dest = os.path.abspath(self.default_save_dir)

        try:
            os.makedirs(dest, exist_ok=True)
        except Exception:
            dest = os.path.abspath(self.default_save_dir)
            try:
                os.makedirs(dest, exist_ok=True)
            except Exception:
                dest = os.path.abspath(".")

        self.request["decision"]["accepted"] = True
        self.request["decision"]["save_dir"] = dest
        self.request["approval_event"].set()
        try:
            if hasattr(self.master, "navigate_to"):
                self.master.navigate_to("transfers")
        except Exception:
            pass
        self.destroy()


# ==========================================
# Lightweight Toast Notification (Sender/Transfer Complete)
# ==========================================

class TransferToast(ctk.CTkFrame):
    """Non-blocking VS Code-style in-app floating toast that auto-closes after 3 seconds."""

    def __init__(
        self,
        master,
        title: str,
        subtitle: str = "",
        duration_ms: int = 3000,
        accent_color: str = C_SUCCESS,
        icon_text: str = "✓",
    ):
        super().__init__(
            master,
            fg_color=C_CARD,
            corner_radius=6,
            border_width=1,
            border_color=accent_color,
            width=360,
            height=68,
        )
        self._dismiss_job = None
        self._closed = False
        self.pack_propagate(False)

        self._build(title, subtitle, accent_color, icon_text)

        try:
            # Place cleanly at bottom-right above the status bar, always on top
            self.place(relx=1.0, rely=1.0, x=-18, y=-42, anchor="se")
            self.lift()
        except Exception:
            pass

        try:
            self._dismiss_job = self.after(duration_ms, self.dismiss)
        except Exception:
            pass

    def _build(self, title: str, subtitle: str, accent_color: str, icon_text: str):
        # Left accent bar
        ctk.CTkFrame(self, width=4, fg_color=accent_color, corner_radius=0).pack(
            side="left", fill="y", padx=(1, 0), pady=1
        )

        # Icon badge
        badge = ctk.CTkFrame(self, width=30, height=30, fg_color=accent_color, corner_radius=4)
        badge.pack(side="left", padx=(10, 10), pady=14)
        badge.pack_propagate(False)
        _label(badge, icon_text, 13, "bold", "#FFFFFF").pack(expand=True)

        # Text column
        txt = ctk.CTkFrame(self, fg_color="transparent")
        txt.pack(side="left", fill="both", expand=True, pady=10)
        _label(txt, title, 12, "bold", C_TEXT).pack(anchor="w")
        if subtitle:
            _label(txt, subtitle, 10, color=C_TEXT_SUB).pack(anchor="w", pady=(2, 0))

        # Dismiss button
        _btn(
            self,
            "✕",
            command=self.dismiss,
            width=24,
            height=24,
            fg="transparent",
            hover=C_HOVER,
            bw=0,
            font_size=11,
            text_color=C_TEXT_SUB,
        ).pack(side="right", padx=(4, 8), anchor="n", pady=8)

    def dismiss(self):
        if self._closed:
            return
        self._closed = True
        if self._dismiss_job:
            try:
                self.after_cancel(self._dismiss_job)
            except Exception:
                pass
            self._dismiss_job = None
        try:
            self.place_forget()
            self.destroy()
        except Exception:
            pass


# ==========================================
# Overview / Welcome Page
# ==========================================

class OverviewPage(ctk.CTkFrame):
    """Landing workspace — quick actions + live device preview + recent transfers."""

    def __init__(self, master, on_send=None, on_devices=None, on_transfers=None,
                 get_devices=None, get_recent=None, on_send_to=None, **kw):
        super().__init__(master, fg_color=C_BG, corner_radius=0, **kw)
        self.on_send = on_send
        self.on_devices = on_devices
        self.on_transfers = on_transfers
        self.get_devices = get_devices or (lambda: {})
        self.get_recent = get_recent or (lambda: [])
        self.on_send_to = on_send_to
        self.pack_propagate(False)
        self._build()

    def _build(self):
        self.scroll = ctk.CTkScrollableFrame(self, fg_color=C_BG, corner_radius=0)
        self.scroll.pack(fill="both", expand=True)

        pad = {"padx": 24}

        # Welcome banner — official AirDude branding
        banner = ctk.CTkFrame(self.scroll, fg_color=C_PANEL, corner_radius=4,
                               border_width=1, border_color=C_BORDER)
        banner.pack(fill="x", **pad, pady=(20, 0))
        bi = ctk.CTkFrame(banner, fg_color="transparent")
        bi.pack(fill="x", padx=20, pady=16)

        # Logo + wordmark row
        brand_row = ctk.CTkFrame(bi, fg_color="transparent")
        brand_row.pack(anchor="w", pady=(0, 6))
        logo_img = get_brand_image("logo_shield.png", (48, 48))
        if logo_img:
            self._overview_logo_ref = logo_img
            _brand_label(brand_row, logo_img, 48, 48, side="left", padx=(0, 14))
        txt_col = ctk.CTkFrame(brand_row, fg_color="transparent")
        txt_col.pack(side="left")
        _label(txt_col, "AirDude", 22, "bold").pack(anchor="w")
        _label(txt_col, "v1.0.0", 10, color=C_TEXT_SUB).pack(anchor="w")

        _label(bi, "Local P2P file sharing: no internet, no cloud, no accounts.", 12,
               color=C_TEXT_SUB).pack(anchor="w")

        # Quick action cards
        cards_row = ctk.CTkFrame(self.scroll, fg_color="transparent")
        cards_row.pack(fill="x", **pad, pady=(14, 0))
        cards_row.grid_columnconfigure(0, weight=1)
        cards_row.grid_columnconfigure(1, weight=1)

        for col, (icon, title, desc, action) in enumerate([
            ("↗", "Send Files", "Transfer files to a nearby device over LAN.", self.on_send),
            ("↙", "Receive Files", "AirDude auto-accepts after you approve the popup.", lambda: None),
        ]):
            c = ctk.CTkFrame(cards_row, fg_color=C_CARD, corner_radius=4,
                             border_width=1, border_color=C_BORDER)
            c.grid(row=0, column=col, sticky="nsew", padx=(0, 8) if col == 0 else (8, 0))
            ci = ctk.CTkFrame(c, fg_color="transparent")
            ci.pack(fill="both", expand=True, padx=16, pady=14)
            _label(ci, icon, 20, color=C_ACCENT).pack(anchor="w")
            _label(ci, title, 13, "bold").pack(anchor="w", pady=(6, 2))
            _label(ci, desc, 10, color=C_TEXT_SUB).pack(anchor="w", pady=(0, 10))
            if col == 0:
                _btn(ci, "+ Choose Files", command=action,
                     height=28, fg=C_ACCENT, hover=C_ACCENT_DIM,
                     text_color="#FFFFFF", bw=0, font_size=11, font_w="bold").pack(anchor="w")

        # Nearby devices section
        nearby_hdr = ctk.CTkFrame(self.scroll, fg_color="transparent")
        nearby_hdr.pack(fill="x", **pad, pady=(18, 4))
        _label(nearby_hdr, "Nearby Devices", 12, "bold").pack(side="left")
        _btn(nearby_hdr, "View All →", command=self.on_devices or (lambda: None),
             height=22, fg="transparent", hover=C_HOVER, text_color=C_ACCENT, bw=0, font_size=10).pack(side="right")

        self.nearby_frame = ctk.CTkFrame(self.scroll, fg_color="transparent")
        self.nearby_frame.pack(fill="x", **pad)
        self._refresh_nearby()

        # Recent transfers
        rt_hdr = ctk.CTkFrame(self.scroll, fg_color="transparent")
        rt_hdr.pack(fill="x", **pad, pady=(18, 4))
        _label(rt_hdr, "Recent Transfers", 12, "bold").pack(side="left")
        _btn(rt_hdr, "View All →", command=self.on_transfers or (lambda: None),
             height=22, fg="transparent", hover=C_HOVER, text_color=C_ACCENT, bw=0, font_size=10).pack(side="right")

        self.recent_frame = ctk.CTkFrame(self.scroll, fg_color="transparent")
        self.recent_frame.pack(fill="x", **pad, pady=(0, 20))
        self._refresh_recent()

    def _refresh_nearby(self):
        for w in self.nearby_frame.winfo_children():
            w.destroy()

        devices = self.get_devices()
        if not devices:
            _label(self.nearby_frame,
                   "No devices found. Keep AirDude open on other PCs.",
                   11, color=C_TEXT_SUB).pack(anchor="w", pady=4)
            return

        for key, dev in list(devices.items())[:4]:
            row = ctk.CTkFrame(self.nearby_frame, fg_color=C_CARD, corner_radius=4,
                               border_width=1, border_color=C_BORDER)
            row.pack(fill="x", pady=(0, 2))
            inner = ctk.CTkFrame(row, fg_color="transparent")
            inner.pack(fill="x", padx=12, pady=6)

            _label(inner, "⬡", 13, color=C_ACCENT).pack(side="left", padx=(0, 8))

            info = ctk.CTkFrame(inner, fg_color="transparent")
            info.pack(side="left", fill="x", expand=True)
            _label(info, dev.get("hostname", key), 11, "bold").pack(anchor="w")
            _label(info, f"{dev.get('os', '')}  ·  {dev.get('ip', '')}", 10, color=C_TEXT_SUB).pack(anchor="w")

            _btn(inner, "↗ Send", command=lambda d=dev: self.on_send_to(d) if self.on_send_to else None,
                 width=56, height=22, fg=C_ACCENT, hover=C_ACCENT_DIM,
                 text_color="#FFFFFF", bw=0, font_size=10).pack(side="right")

    def _refresh_recent(self):
        for w in self.recent_frame.winfo_children():
            w.destroy()

        recent = self.get_recent()
        if not recent:
            _label(self.recent_frame,
                   "No transfers yet. Send or receive files to see history here.",
                   11, color=C_TEXT_SUB).pack(anchor="w", pady=4)
            return

        for rec in recent[:5]:
            is_ok = rec.get("status") == "completed"
            fg_col = C_SUCCESS if is_ok else C_ERROR

            row = ctk.CTkFrame(self.recent_frame, fg_color=C_CARD, corner_radius=4,
                               border_width=1, border_color=C_BORDER)
            row.pack(fill="x", pady=(0, 2))
            inner = ctk.CTkFrame(row, fg_color="transparent")
            inner.pack(fill="x", padx=12, pady=6)

            dir_icon = "↗" if rec.get("direction") == "SEND" else "↙"
            _label(inner, dir_icon, 12, color=C_TEXT_SUB).pack(side="left", padx=(0, 10))

            info = ctk.CTkFrame(inner, fg_color="transparent")
            info.pack(side="left", fill="x", expand=True)
            _label(info, rec.get("filename", "file"), 11, "bold").pack(anchor="w")
            _label(info, rec.get("peer_name", ""), 10, color=C_TEXT_SUB).pack(anchor="w")

            meta = ctk.CTkFrame(inner, fg_color="transparent")
            meta.pack(side="right")
            _label(meta, format_bytes(rec.get("total_size", 0)), 10, color=C_TEXT_SUB).pack(anchor="e")
            _label(meta, "● " + ("Done" if is_ok else "Failed"), 10, "bold", fg_col).pack(anchor="e")

    def refresh(self):
        self._refresh_nearby()
        self._refresh_recent()


# ==========================================
# Main Application Window
# ==========================================

class AirDudeApp(ctk.CTk):
    """Main AirDude application — VS Code 3-column layout."""

    def __init__(
        self,
        name: str = "MyPC",
        udp_port: int = 45454,
        tcp_port: int = 45455,
        broadcast_port: Optional[int] = None,
        save_dir: Optional[str] = None,
    ):
        super().__init__()

        # Clear cached CTkImage objects from any previous Tk root (important for
        # test suites that create multiple AirDudeApp instances in the same process).
        clear_brand_image_cache()

        # Settings
        self.settings = load_settings()
        if self.settings.get("device_name") and name == "MyPC":
            self.device_name = self.settings["device_name"]
        else:
            self.device_name = name

        self.udp_port = udp_port
        self.tcp_port = tcp_port
        self.broadcast_port = broadcast_port if broadcast_port is not None else udp_port

        self.save_dir = save_dir or self.settings.get("save_dir") or f"received_files_{self.device_name}"

        # Apply theme — force Dark for VS Code look
        appearance = self.settings.get("appearance_mode", "Dark")
        try:
            ctk.set_appearance_mode(appearance.lower())
        except Exception:
            ctk.set_appearance_mode("dark")

        self.title(f"AirDude - {self.device_name}")
        self.geometry("1200x740")
        self.minsize(1000, 640)
        self.configure(fg_color=C_BG)

        # Set official brand icon for window titlebar + Windows taskbar
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AirDude.Desktop.1.0")
            except Exception:
                pass
        ico_path = get_asset_path(os.path.join("branding", "app_icon.ico"))
        if os.path.isfile(ico_path):
            try:
                self.iconbitmap(ico_path)
            except Exception:
                pass
            # Also set via wm_iconphoto for Linux/Mac compatibility
            try:
                from PIL import Image, ImageTk
                _pil = Image.open(get_asset_path(os.path.join("branding", "logo_64.png")))
                self._wm_icon = ImageTk.PhotoImage(_pil)
                self.wm_iconphoto(True, self._wm_icon)
            except Exception:
                pass

        # State
        self.active_devices: Dict[str, dict] = {}
        self.history_records: List[dict] = load_history()
        self.recent_transfers: List[dict] = list(self.history_records[:20])
        self._pending_events: List[dict] = []
        self._session_events: List[dict] = []
        self._active_toast: Optional[TransferToast] = None
        self.current_route: str = ""

        # Queues
        self.discovery_queue: queue.Queue = queue.Queue()
        self.transfer_request_queue: queue.Queue = queue.Queue()
        self.transfer_update_queue: queue.Queue = queue.Queue()

        # Network
        self.network = AirDudeNetwork(
            hostname=self.device_name,
            save_directory=self.save_dir,
            tcp_port=self.tcp_port,
            udp_port=self.udp_port,
            broadcast_port=self.broadcast_port,
            discovery_queue=self.discovery_queue,
            transfer_request_queue=self.transfer_request_queue,
            transfer_update_queue=self.transfer_update_queue,
        )
        self.network.start_discovery()
        self.network.start_transfer_server()
        self.network.broadcast_now()

        # Build UI
        self._setup_layout()

        # Event handlers
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind_all("<MouseWheel>", self._scroll, add="+")
        self.bind_all("<Button-4>", self._scroll, add="+")
        self.bind_all("<Button-5>", self._scroll, add="+")

        self.after(100, self.poll_queue)

        # Show branded splash screen, then navigate once it closes
        try:
            self.withdraw()   # hide main window until splash is done
            splash = SplashScreen(self, duration_ms=1400, on_ready=self._post_splash)
        except Exception:
            self._post_splash()

    def _post_splash(self):
        """Called after splash screen closes — reveal the main window."""
        try:
            self.deiconify()
        except Exception:
            pass
        self.navigate_to("devices")

    # ── Layout ──────────────────────────────────────────────

    def _setup_layout(self):
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=0)   # Activity bar
        self.grid_columnconfigure(1, weight=0)   # Sidebar
        self.grid_columnconfigure(2, weight=1)   # Main workspace

        # Activity bar
        self.activity_bar = ActivityBar(self, on_select=self.navigate_to)
        self.activity_bar.grid(row=0, column=0, rowspan=2, sticky="nsew")

        # Sidebar
        self.sidebar = SidebarPanel(self)
        self.sidebar.grid(row=0, column=1, rowspan=2, sticky="nsew")

        # Top bar (spanning main area)
        self.topbar = TopBar(self)
        self.topbar.grid(row=0, column=2, sticky="ew")

        # Main workspace
        self.workspace = ctk.CTkFrame(self, fg_color=C_BG, corner_radius=0)
        self.workspace.grid(row=1, column=2, sticky="nsew")
        self.workspace.grid_rowconfigure(0, weight=1)
        self.workspace.grid_columnconfigure(0, weight=1)

        self.current_page = None

    # ── Navigation ──────────────────────────────────────────

    def navigate_to(self, route: str, target_device: Optional[dict] = None):
        self.current_route = route
        self.activity_bar.set_selected(route if route in ("devices", "transfers", "history", "settings") else self.activity_bar._active or "devices")

        # Update topbar
        route_names = {
            "devices": "Nearby Devices",
            "transfers": "Transfers",
            "history": "Transfer History",
            "settings": "Settings",
            "send": "Send Files",
            "overview": "Overview",
        }
        self.topbar.set_path(route_names.get(route, route.capitalize()))

        # Destroy previous page
        if self.current_page:
            try:
                self.current_page.destroy()
            except Exception:
                pass

        # Update sidebar
        sidebar_titles = {
            "devices": "Devices",
            "transfers": "Transfers",
            "history": "History",
            "settings": "Settings",
            "send": "Send Files",
            "overview": "Overview",
        }
        self.sidebar.set_section(route, sidebar_titles.get(route, route.capitalize()))

        # Build page
        if route == "devices":
            local_info = {
                "name": self.device_name,
                "ip": self.network.local_ip,
                "udp_port": self.udp_port,
                "tcp_port": self.tcp_port,
            }
            self.current_page = DevicesPage(
                self.workspace,
                local_info=local_info,
                on_refresh=self._on_refresh,
                on_send_file=lambda dev: self.navigate_to("send", target_device=dev),
            )
            self.current_page.sync_devices(self.active_devices)

        elif route == "send":
            self.current_page = SendFilesPage(
                self.workspace,
                network=self.network,
                active_devices=self.active_devices,
                target_device=target_device,
            )

        elif route == "transfers":
            self.current_page = TransfersPage(
                self.workspace,
                network=self.network,
                on_send_files=lambda: self.navigate_to("send"),
                app_ref=self,
            )
            for ev in self._session_events:
                self.current_page.handle_transfer_event(ev, replay=True)
            self._pending_events.clear()

        elif route == "history":
            self.current_page = HistoryPage(self.workspace, app_ref=self)

        elif route == "settings":
            self.current_page = SettingsPage(self.workspace, app_ref=self)

        elif route == "overview":
            self.current_page = OverviewPage(
                self.workspace,
                on_send=lambda: self.navigate_to("send"),
                on_devices=lambda: self.navigate_to("devices"),
                on_transfers=lambda: self.navigate_to("transfers"),
                get_devices=lambda: self.active_devices,
                get_recent=lambda: self.recent_transfers,
                on_send_to=lambda dev: self.navigate_to("send", target_device=dev),
            )

        else:
            self.current_page = ctk.CTkFrame(self.workspace, fg_color=C_BG)
            _label(self.current_page, f"Section: {route}", 14, color=C_TEXT_SUB).pack(expand=True)

        self.current_page.grid(row=0, column=0, sticky="nsew")

    # ── Device name ─────────────────────────────────────────

    def update_device_name(self, new_name: str):
        if not new_name or not new_name.strip():
            return
        self.device_name = new_name.strip()
        self.title(f"AirDude - {self.device_name}")
        if hasattr(self.sidebar, "version_lbl"):
            self.sidebar.version_lbl.configure(text=self.device_name)
        self.network.set_hostname(self.device_name)
        self.settings["device_name"] = self.device_name
        save_settings(self.settings)

    # ── History management ───────────────────────────────────

    def record_transfer_result(self, record: dict):
        self.history_records = add_history_entry(record)
        self.recent_transfers = list(self.history_records[:20])
        if isinstance(self.current_page, TransfersPage):
            if getattr(self.current_page, "_active_tab", "") == "history":
                self.current_page._rebuild_history()
        elif isinstance(self.current_page, HistoryPage):
            self.current_page.refresh()
        elif isinstance(self.current_page, OverviewPage):
            self.current_page.refresh()

    def clear_all_history(self):
        clear_history_file()
        self.history_records.clear()
        self.recent_transfers.clear()
        if isinstance(self.current_page, (TransfersPage, HistoryPage)):
            try:
                if isinstance(self.current_page, TransfersPage):
                    self.current_page._rebuild_history()
                else:
                    self.current_page.refresh()
            except Exception:
                pass
        elif isinstance(self.current_page, OverviewPage):
            self.current_page.refresh()

    # ── Scrolling ────────────────────────────────────────────

    def _find_scroll(self, widget):
        w = widget
        while w is not None:
            if isinstance(w, ctk.CTkScrollableFrame):
                return w
            w = getattr(w, "master", None)
        p = self.current_page
        if p:
            for attr in ("scroll", "scroll_container"):
                sf = getattr(p, attr, None)
                if isinstance(sf, ctk.CTkScrollableFrame):
                    return sf
            for child in p.winfo_children():
                if isinstance(child, ctk.CTkScrollableFrame):
                    return child
        return None

    def _scroll(self, event):
        try:
            widget = self.winfo_containing(event.x_root, event.y_root)
        except Exception:
            widget = None
        sf = self._find_scroll(widget)
        if not sf:
            return
        canvas = getattr(sf, "_parent_canvas", None)
        if not canvas:
            return
        if event.num == 4:
            delta = -2
        elif event.num == 5:
            delta = 2
        elif sys.platform == "darwin":
            delta = -int(event.delta) if event.delta else 0
        else:
            delta = -int(event.delta / 3) if event.delta else 0
            if delta == 0 and event.delta:
                delta = -1 if event.delta > 0 else 1
        try:
            canvas.yview("scroll", delta, "units")
        except Exception:
            pass

    # ── Toast Notification ──────────────────────────────────

    def _show_transfer_toast(self, title: str, subtitle: str = "", duration_ms: int = 3000):
        try:
            if self._active_toast is not None:
                self._active_toast.dismiss()
                self._active_toast = None
            self._active_toast = TransferToast(
                self,
                title=title,
                subtitle=subtitle,
                duration_ms=duration_ms,
            )
        except Exception:
            pass

    # ── Poll queue ───────────────────────────────────────────

    def poll_queue(self):
        try:
            updated = False

            # Discovery
            while not self.discovery_queue.empty():
                try:
                    info = self.discovery_queue.get_nowait()
                    if info.get("is_self") or info.get("ip") == "127.0.0.1":
                        continue
                    if info.get("device_id") == self.network.device_id:
                        continue
                    key = info.get("device_key") or info.get("device_id") or f"{info.get('ip')}:{info.get('tcp_port')}"
                    self.active_devices[key] = info
                    updated = True
                except queue.Empty:
                    break

            # Incoming transfers
            while not self.transfer_request_queue.empty():
                try:
                    req = self.transfer_request_queue.get_nowait()
                    IncomingTransferModal(self, req, default_save_dir=self.save_dir)
                except queue.Empty:
                    break

            # Transfer events
            while not self.transfer_update_queue.empty():
                try:
                    event = self.transfer_update_queue.get_nowait()
                    etype = event.get("type", "")

                    # Record non-progress lifecycle events for session replay so
                    # switching tabs never loses active/completed cards.
                    if etype in (
                        "TRANSFER_START",
                        "TRANSFER_PAUSED",
                        "TRANSFER_RESUMED",
                        "TRANSFER_COMPLETE",
                        "TRANSFER_FAILED",
                    ):
                        self._session_events.append(event)
                        if len(self._session_events) > 200:
                            self._session_events = self._session_events[-200:]

                    if etype in ("TRANSFER_COMPLETE", "TRANSFER_FAILED"):
                        rec = {
                            "transfer_id": event.get("transfer_id", ""),
                            "filename": event.get("filename", "?"),
                            "peer_name": event.get("peer_name", "Peer"),
                            "total_size": event.get("total_size", 0),
                            "direction": event.get("direction", "SEND"),
                            "status": "completed" if etype == "TRANSFER_COMPLETE" else (event.get("status") or "failed"),
                            "hash_ok": True if event.get("hash_ok", True) is None else bool(event.get("hash_ok", True)),
                            "saved_path": event.get("saved_path", ""),
                            "message": event.get("message", ""),
                            "timestamp": time.strftime("%H:%M:%S"),
                        }
                        self.record_transfer_result(rec)
                        if etype == "TRANSFER_COMPLETE":
                            try:
                                notify_transfer_complete(self.winfo_id(), filename=rec.get("filename", ""))
                            except Exception:
                                pass
                            if rec.get("direction") == "SEND":
                                self._show_transfer_toast(
                                    title=f"✓ File Sent Successfully to {rec['peer_name']}",
                                    subtitle=f"{rec['filename']}  ·  SHA-256 Verified",
                                )
                        updated = True

                    # Auto-navigate sender to Transfers page as soon as receiver confirms
                    if (
                        isinstance(self.current_page, SendFilesPage)
                        and event.get("direction") == "SEND"
                        and (
                            (etype == "TRANSFER_START" and not event.get("waiting", False))
                            or etype in ("TRANSFER_PROGRESS", "TRANSFER_COMPLETE")
                        )
                    ):
                        self.navigate_to("transfers")

                    if isinstance(self.current_page, TransfersPage):
                        self.current_page.handle_transfer_event(event)
                    else:
                        self._pending_events.append(event)

                except queue.Empty:
                    break

            # Prune offline devices
            now = time.time()
            offline = [k for k, d in self.active_devices.items() if now - d.get("last_seen", 0) > 10.0]
            for k in offline:
                self.active_devices.pop(k, None)
                updated = True

            # Sync active page
            if isinstance(self.current_page, DevicesPage):
                self.current_page.sync_devices(self.active_devices)
            elif isinstance(self.current_page, SendFilesPage) and updated:
                self.current_page.sync_devices(self.active_devices)
            elif isinstance(self.current_page, OverviewPage) and updated:
                self.current_page.refresh()

            if updated and hasattr(self, "sidebar") and hasattr(self.sidebar, "refresh"):
                self.sidebar.refresh()

            # Port conflict alerts
            if getattr(self.network, "tcp_error", None) and not getattr(self, "_tcp_alerted", False):
                self._tcp_alerted = True
                messagebox.showwarning("TCP Port Conflict", self.network.tcp_error, parent=self)
            if getattr(self.network, "udp_error", None) and not getattr(self, "_udp_alerted", False):
                self._udp_alerted = True
                messagebox.showwarning("UDP Port Conflict", self.network.udp_error, parent=self)

        except Exception as e:
            traceback.print_exc()
        finally:
            self.after(100, self.poll_queue)

    def _on_refresh(self):
        self.network.broadcast_now()

    def _on_close(self):
        self.network.stop()
        self.destroy()

    # Alias for backward compat with navigate_to("Transfers" / "Settings" etc.)
    def _compat_nav(self, old_route: str, **kw):
        mapping = {
            "Overview": "overview",
            "Nearby Devices": "devices",
            "Send Files": "send",
            "Transfers": "transfers",
            "History": "history",
            "Settings": "settings",
            "About": "settings",
        }
        self.navigate_to(mapping.get(old_route, old_route.lower()), **kw)


# ==========================================
# Entry Point
# ==========================================

def run_app():
    parser = argparse.ArgumentParser(description="AirDude Desktop Application")
    parser.add_argument("--name", type=str, default="MyPC")
    parser.add_argument("--udp_port", type=int, default=45454)
    parser.add_argument("--tcp_port", type=int, default=45455)
    parser.add_argument("--broadcast_port", type=int, default=None)
    parser.add_argument("--save_dir", type=str, default=None)
    args = parser.parse_args()

    app = AirDudeApp(
        name=args.name,
        udp_port=args.udp_port,
        tcp_port=args.tcp_port,
        broadcast_port=args.broadcast_port,
        save_dir=args.save_dir,
    )
    app.mainloop()


if __name__ == "__main__":
    run_app()
