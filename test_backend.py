"""
AirDude - Multi-Instance Backend Test & Verification CLI
Supports running multiple instances on a single PC or across LAN machines.
Continuously monitors discovered peers and provides commands to send test/custom files.
"""

import argparse
import os
import sys
import threading
import time
from typing import Dict, Optional

from network import AirDudeNetwork
from utils import calculate_file_hash, format_bytes, get_local_ip


def create_sample_payload(name: str, size_kb: int = 128) -> str:
    """Generates a random test payload spanning multiple 64KB chunks."""
    os.makedirs("test_data", exist_ok=True)
    filepath = os.path.join("test_data", f"test_payload_{name}_{size_kb}KB.bin")
    data = os.urandom(size_kb * 1024)
    with open(filepath, "wb") as f:
        f.write(data)
    return filepath


class DiscoveredPeerManager:
    """Helper to maintain indexed peer references ([1], [2], ...) for easy CLI selection."""

    def __init__(self):
        self.peer_list = []
        self._lock = threading.Lock()

    def update(self, devices: Dict[str, dict]) -> list:
        with self._lock:
            self.peer_list = list(devices.values())
            return list(self.peer_list)

    def get_by_index(self, index: int) -> Optional[dict]:
        with self._lock:
            if 1 <= index <= len(self.peer_list):
                return self.peer_list[index - 1]
            return None

    def get_all(self) -> list:
        with self._lock:
            return list(self.peer_list)


def continuous_discovery_monitor(
    network: AirDudeNetwork,
    peer_manager: DiscoveredPeerManager,
    stop_event: threading.Event,
    interval: float = 3.5,
) -> None:
    """
    Background worker that continuously prints discovered devices to the console.
    """
    last_count = -1
    last_keys = set()

    while not stop_event.is_set():
        # Get active devices excluding this local instance
        devices = network.get_nearby_devices(active_within_seconds=10.0, exclude_self=True)
        current_keys = set(devices.keys())
        peers = peer_manager.update(devices)
        timestamp = time.strftime("%H:%M:%S")

        # Print update if devices changed or periodic heartbeat
        if len(devices) != last_count or current_keys != last_keys:
            last_count = len(devices)
            last_keys = current_keys

            print("\n" + "-" * 70)
            if not peers:
                print(f"[{timestamp}] [Discovery] Searching for peers... (0 found)")
            else:
                print(f"[{timestamp}] [Discovery] Discovered Peers ({len(peers)}):")
                for idx, peer in enumerate(peers, start=1):
                    sec_ago = max(0, int(time.time() - peer.get("last_seen", time.time())))
                    print(
                        f"  [{idx}] {peer.get('hostname', 'Peer')} ({peer.get('os', 'Unknown')}) "
                        f"-> IP: {peer.get('ip')}:{peer.get('tcp_port', 45455)} "
                        f"(Last seen: {sec_ago}s ago)"
                    )
            print("-" * 70)
            print(f"AirDude ({network.hostname}) > ", end="", flush=True)

        stop_event.wait(interval)


def print_progress_bar(bytes_sent: int, total_bytes: int) -> None:
    percent = (bytes_sent / total_bytes) * 100 if total_bytes > 0 else 100
    bar_length = 25
    filled = int(percent / 100 * bar_length)
    bar = "=" * filled + "-" * (bar_length - filled)
    print(f"\r  Uploading: [{bar}] {percent:.1f}% ({format_bytes(bytes_sent)} / {format_bytes(total_bytes)})", end="", flush=True)


def execute_transfer(
    network: AirDudeNetwork,
    target_ip: str,
    target_port: int,
    file_path: str,
) -> None:
    """Executes chunked file transfer with progress reporting and checksum verification."""
    if not os.path.isfile(file_path):
        print(f"\n[ERROR] File not found: {file_path}")
        return

    print(f"\n[*] Starting transfer to {target_ip}:{target_port}...")
    print(f"[*] File: {os.path.abspath(file_path)} ({format_bytes(os.path.getsize(file_path))})")

    try:
        result = network.send_file(
            target_ip=target_ip,
            filepath=file_path,
            sender_name=network.hostname,
            port=target_port,
            progress_callback=print_progress_bar,
        )
        print(f"\n[+] Transfer completed! Peer status response: {result.get('status')} - {result.get('message')}")
    except Exception as e:
        print(f"\n[-] Transfer failed: {e}")


def interactive_menu(
    network: AirDudeNetwork,
    peer_manager: DiscoveredPeerManager,
    stop_event: threading.Event,
) -> None:
    """Interactive command shell allowing the user to trigger transfers to discovered peers."""
    print("\n" + "=" * 70)
    print("                     AirDude CLI Command Menu                     ")
    print("=" * 70)
    print("  Type:                                                           ")
    print("   [1], [2], ...        : Send quick 128KB test payload to peer # ")
    print("   send <#> [path]      : Send custom file to peer number         ")
    print("   send <ip>:<port> <path> : Send custom file to exact IP:Port    ")
    print("   list (or l)          : Print currently discovered peers list   ")
    print("   help (or h)          : Show this menu                          ")
    print("   quit (or q)          : Exit AirDude                            ")
    print("=" * 70 + "\n")

    while not stop_event.is_set():
        try:
            cmd = input(f"AirDude ({network.hostname}) > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[*] Exiting AirDude...")
            break

        if not cmd:
            continue

        parts = cmd.split()
        action = parts[0].lower()

        if action in ("q", "quit", "exit"):
            break

        elif action in ("h", "help", "menu"):
            print("\nCommands:")
            print("  <1, 2, ...>              -> Send quick 128KB test payload to peer number")
            print("  send <#> [file_path]     -> Send file to peer number")
            print("  send <ip>:<port> <path>  -> Send file to IP:port")
            print("  list (or l)              -> Display all active peers")
            print("  quit (or q)              -> Stop and exit\n")

        elif action in ("l", "list"):
            peers = peer_manager.get_all()
            print(f"\n--- Discovered Peers ({len(peers)}) ---")
            if not peers:
                print("  No peers active currently. Check UDP ports or wait for broadcast.")
            else:
                for idx, peer in enumerate(peers, start=1):
                    sec_ago = max(0, int(time.time() - peer.get("last_seen", time.time())))
                    print(
                        f"  [{idx}] {peer.get('hostname')} ({peer.get('os')}) "
                        f"@ {peer.get('ip')}:{peer.get('tcp_port')} (seen {sec_ago}s ago)"
                    )
            print("------------------------------------\n")

        # Shorthand: user just types peer number (e.g. "1")
        elif action.isdigit():
            peer_idx = int(action)
            peer = peer_manager.get_by_index(peer_idx)
            if not peer:
                print(f"[!] Invalid peer number [{peer_idx}]. Type 'list' to see active peers.")
                continue

            test_file = create_sample_payload(name=network.hostname, size_kb=128)
            execute_transfer(
                network=network,
                target_ip=peer.get("ip"),
                target_port=peer.get("tcp_port", 45455),
                file_path=test_file,
            )

        elif action == "send":
            if len(parts) == 1:
                # Interactive prompt
                peers = peer_manager.get_all()
                if not peers:
                    print("[!] No discovered peers yet. You can manually enter target IP:PORT.")
                    target_str = input("Enter target (IP:PORT): ").strip()
                else:
                    print("Active peers:")
                    for idx, peer in enumerate(peers, start=1):
                        print(f"  [{idx}] {peer.get('hostname')} ({peer.get('ip')}:{peer.get('tcp_port')})")
                    target_str = input("Select Peer # or enter IP:PORT: ").strip()

                if target_str.isdigit():
                    peer = peer_manager.get_by_index(int(target_str))
                    if not peer:
                        print("[!] Invalid peer number.")
                        continue
                    t_ip = peer.get("ip")
                    t_port = peer.get("tcp_port", 45455)
                elif ":" in target_str:
                    t_ip, t_port_str = target_str.split(":", 1)
                    t_port = int(t_port_str)
                else:
                    t_ip = target_str
                    t_port = 45455

                file_to_send = input("File path to send (press Enter for sample 128KB payload): ").strip()
                if not file_to_send:
                    file_to_send = create_sample_payload(name=network.hostname, size_kb=128)

                execute_transfer(network, t_ip, t_port, file_to_send)

            elif len(parts) >= 2:
                target_spec = parts[1]
                custom_file = parts[2] if len(parts) >= 3 else create_sample_payload(name=network.hostname, size_kb=128)

                if target_spec.isdigit():
                    peer = peer_manager.get_by_index(int(target_spec))
                    if not peer:
                        print(f"[!] Invalid peer number [{target_spec}].")
                        continue
                    t_ip = peer.get("ip")
                    t_port = peer.get("tcp_port", 45455)
                elif ":" in target_spec:
                    t_ip, t_port_str = target_spec.split(":", 1)
                    t_port = int(t_port_str)
                else:
                    t_ip = target_spec
                    t_port = 45455

                execute_transfer(network, t_ip, t_port, custom_file)

        else:
            print(f"[!] Unrecognized command: '{cmd}'. Type 'help' or 'list'.")


def main():
    parser = argparse.ArgumentParser(
        description="AirDude Backend Network Diagnostics & Single-PC / Multi-Device Verification"
    )
    # Required arguments matching user specification
    parser.add_argument(
        "--name",
        type=str,
        default="MyPC",
        help="Device display name / identifier (default: 'MyPC')",
    )
    parser.add_argument(
        "--udp_port",
        type=int,
        default=45454,
        help="Local UDP port to listen for incoming discovery broadcasts (default: 45454)",
    )
    parser.add_argument(
        "--tcp_port",
        type=int,
        default=45455,
        help="Local TCP port to listen for incoming chunked file transfers (default: 45455)",
    )
    parser.add_argument(
        "--broadcast_port",
        type=int,
        default=None,
        help="Target UDP port to broadcast presence to (default: same as --udp_port)",
    )
    parser.add_argument(
        "--save_dir",
        type=str,
        default=None,
        help="Directory to save received files (default: 'received_files_<name>')",
    )

    args = parser.parse_args()

    # If broadcast_port not explicitly given, broadcast to the same port as udp_port
    target_broadcast_port = args.broadcast_port if args.broadcast_port is not None else args.udp_port
    save_directory = args.save_dir or f"received_files_{args.name}"

    local_ip = get_local_ip()

    print("=" * 70)
    print(f"                AirDude Instance: {args.name}                     ")
    print("=" * 70)
    print(f"  Device Name:          {args.name}")
    print(f"  Local IPv4:           {local_ip}")
    print(f"  UDP Listen Port:      {args.udp_port}")
    print(f"  UDP Broadcast Target: {target_broadcast_port}")
    print(f"  TCP Transfer Port:    {args.tcp_port} (Streaming chunk: 64 KB)")
    print(f"  Save Directory:       {os.path.abspath(save_directory)}")
    print("=" * 70)

    # Initialize network core
    network = AirDudeNetwork(
        hostname=args.name,
        save_directory=save_directory,
        tcp_port=args.tcp_port,
        udp_port=args.udp_port,
        broadcast_port=target_broadcast_port,
    )

    peer_manager = DiscoveredPeerManager()
    stop_event = threading.Event()

    # Start discovery and transfer servers (background daemon threads)
    network.start_discovery()
    network.start_transfer_server()

    # Start background continuous discovery printer thread
    monitor_thread = threading.Thread(
        target=continuous_discovery_monitor,
        args=(network, peer_manager, stop_event),
        name=f"Monitor-{args.name}",
        daemon=True,
    )
    monitor_thread.start()

    # Run interactive menu in main thread
    try:
        interactive_menu(network, peer_manager, stop_event)
    finally:
        stop_event.set()
        network.stop()
        print(f"[*] AirDude ({args.name}) stopped cleanly.")


if __name__ == "__main__":
    main()
