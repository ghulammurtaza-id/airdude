"""
AirDude - Utility Functions
Provides helper utilities for file hashing, byte formatting, and local IP resolution.
"""

import hashlib
import json
import os
import socket
import sys

# ==========================================
# Brand Asset Management
# ==========================================

_BRAND_IMAGE_CACHE = {}

def clear_brand_image_cache():
    """
    Clears the cached CTkImage objects.  Call this when a new Tk root is created
    so stale photo-image references from the previous root are not reused
    (which causes TclError: image "pyimageN" does not exist).
    """
    global _BRAND_IMAGE_CACHE
    _BRAND_IMAGE_CACHE = {}

def get_asset_path(relative_path: str) -> str:
    """
    Returns absolute path to an asset, handling both direct script execution
    and PyInstaller frozen bundles (sys._MEIPASS).
    """
    base_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, "assets", relative_path)

def get_brand_image(name: str = "logo_shield.png", size: tuple = (32, 32)):
    """
    Loads and caches a CTkImage for the brand logo to prevent garbage collection issues
    and ensure crisp scaling across high-DPI displays.
    """
    key = (name, size)
    if key in _BRAND_IMAGE_CACHE:
        return _BRAND_IMAGE_CACHE[key]

    path = get_asset_path(os.path.join("branding", name))
    if not os.path.isfile(path):
        return None
    try:
        from PIL import Image
        import customtkinter as ctk
        pil_img = Image.open(path)
        ctk_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=size)
        _BRAND_IMAGE_CACHE[key] = ctk_img
        return ctk_img
    except Exception as e:
        print(f"[AirDude Branding] Error loading brand image '{name}': {e}")
        return None

# Path to the persistent history file in the application directory
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history.json")
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")
MAX_HISTORY_ENTRIES = 100


# ==========================================
# Settings Persistence (settings.json)
# ==========================================

DEFAULT_SETTINGS = {
    "device_name": "",
    "appearance_mode": "System",
    "save_dir": "received_files",
}

def load_settings() -> dict:
    """
    Loads application settings from settings.json.
    Returns default settings if file doesn't exist or is invalid.
    """
    if not os.path.isfile(SETTINGS_FILE):
        return DEFAULT_SETTINGS.copy()
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                merged = DEFAULT_SETTINGS.copy()
                merged.update(data)
                return merged
    except Exception as e:
        print(f"[AirDude Settings] Could not load {SETTINGS_FILE}: {e}")
    return DEFAULT_SETTINGS.copy()


def save_settings(settings: dict) -> bool:
    """
    Persists application settings dictionary to settings.json.
    """
    try:
        tmp_file = SETTINGS_FILE + ".tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
        os.replace(tmp_file, SETTINGS_FILE)
        return True
    except Exception as e:
        print(f"[AirDude Settings] Could not save {SETTINGS_FILE}: {e}")
        return False


# ==========================================
# Sound & OS Flash Notifications
# ==========================================

def notify_transfer_complete(hwnd=None, filename: str = ""):
    """
    Plays a subtle OS system chime and flashes the taskbar icon when a file
    transfer completes.
    """
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_ICONASTERISK)
    except Exception:
        pass

    if hwnd:
        try:
            import ctypes
            ctypes.windll.user32.FlashWindow(hwnd, True)
        except Exception:
            pass


# ==========================================
# Native Windows Drag & Drop (ctypes)
# ==========================================

_wndproc_refs = []

def enable_drag_and_drop(tk_window, callback) -> bool:
    """
    Enables native Windows Explorer file drag-and-drop onto a Tk/CustomTkinter window.
    When files are dropped, calls `callback(list_of_filepaths)`.
    Returns True if successfully enabled, False otherwise (e.g. non-Windows or error).
    """
    import sys
    if sys.platform != "win32":
        return False

    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32

        # Get parent HWND if available, or direct window id
        tk_window.update_idletasks()
        win_id = tk_window.winfo_id()
        hwnd = user32.GetParent(win_id)
        if not hwnd:
            hwnd = win_id

        GWL_WNDPROC = -4
        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

        user32.GetWindowLongPtrW.restype = ctypes.c_longlong
        user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.SetWindowLongPtrW.restype = ctypes.c_longlong
        user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, WNDPROC]
        user32.CallWindowProcW.restype = ctypes.c_longlong
        user32.CallWindowProcW.argtypes = [ctypes.c_longlong, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]

        old_proc = user32.GetWindowLongPtrW(hwnd, GWL_WNDPROC)

        def custom_wndproc(h, msg, wparam, lparam):
            if msg == 0x0233:  # WM_DROPFILES
                hdrop = wparam
                try:
                    count = shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
                    files = []
                    buf = ctypes.create_unicode_buffer(1024)
                    for i in range(count):
                        shell32.DragQueryFileW(hdrop, i, buf, 1024)
                        files.append(buf.value)
                    shell32.DragFinish(hdrop)
                    if callback and files:
                        tk_window.after(0, lambda: callback(files))
                except Exception as drop_err:
                    print(f"[DragDrop] Error processing dropped files: {drop_err}")
                return 0
            return user32.CallWindowProcW(old_proc, h, msg, wparam, lparam)

        c_proc = WNDPROC(custom_wndproc)
        # Store reference on window and global list to prevent garbage collection
        _wndproc_refs.append(c_proc)
        tk_window._drag_drop_wndproc = c_proc

        user32.SetWindowLongPtrW(hwnd, GWL_WNDPROC, c_proc)
        shell32.DragAcceptFiles(hwnd, True)
        return True
    except Exception as e:
        print(f"[DragDrop] Native drag and drop hook not available: {e}")
        return False



def calculate_file_hash(filepath: str, chunk_size: int = 65536) -> str:
    """
    Computes the SHA-256 hash of a file by reading in chunks to maintain low memory overhead.
    
    :param filepath: Path to the target file.
    :param chunk_size: Size of chunks to read into memory (defaults to 64KB).
    :return: Hexadecimal SHA-256 digest string.
    :raises FileNotFoundError: If the file does not exist.
    """
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")

    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            sha256.update(chunk)
    return sha256.hexdigest()


def format_bytes(size_bytes: int) -> str:
    """
    Converts a raw byte count into a human-readable string representation (B, KB, MB, GB, TB).
    
    :param size_bytes: Total number of bytes.
    :return: Formatted string with appropriate unit.
    """
    if size_bytes < 0:
        raise ValueError("Byte size cannot be negative.")

    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    value = float(size_bytes)
    
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.2f} {unit}"
        value /= 1024.0

    return f"{value:.2f} PB"


def get_local_ip() -> str:
    """
    Determines the active local IPv4 address connected to the LAN/Wi-Fi subnet.
    Uses an ephemeral UDP socket connection check to avoid hardcoding or resolving localhost.
    
    :return: Local IPv4 address as a string (e.g. '192.168.1.104').
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Connect to a public DNS IP (no packet is actually sent over wire for UDP)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
    except Exception:
        # Fallback to localhost or hostname lookup if offline
        try:
            local_ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            local_ip = "127.0.0.1"
    finally:
        s.close()
    return local_ip


# ==========================================
# Transfer History Persistence (history.json)
# ==========================================

def load_history() -> list:
    """
    Loads transfer history records from history.json.
    Returns an empty list if the file does not exist, is empty, or contains invalid data.
    Ensures no more than MAX_HISTORY_ENTRIES records are returned.
    """
    if not os.path.isfile(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data[:MAX_HISTORY_ENTRIES]
    except Exception as e:
        print(f"[AirDude History] Could not load {HISTORY_FILE}: {e}")
    return []


def save_history(records: list) -> bool:
    """
    Atomically writes up to MAX_HISTORY_ENTRIES (100) records to history.json.
    Uses a temporary file before renaming to prevent corrupted files on crash.
    """
    try:
        trimmed = records[:MAX_HISTORY_ENTRIES]
        tmp_file = HISTORY_FILE + ".tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(trimmed, f, indent=2, ensure_ascii=False)
        os.replace(tmp_file, HISTORY_FILE)
        return True
    except Exception as e:
        print(f"[AirDude History] Could not save {HISTORY_FILE}: {e}")
        return False


def add_history_entry(record: dict) -> list:
    """
    Prepends a new transfer record to history (newest first).
    Enforces the 100-item retention limit and saves to history.json.
    Returns the updated records list.
    """
    records = load_history()
    # Deduplicate by transfer_id if already present
    tid = record.get("transfer_id")
    if tid:
        records = [r for r in records if r.get("transfer_id") != tid]

    records.insert(0, record)
    records = records[:MAX_HISTORY_ENTRIES]
    save_history(records)
    return records


def clear_history_file() -> bool:
    """
    Wipes all records from history.json, resetting it to an empty JSON list [].
    """
    return save_history([])


