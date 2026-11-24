"""
AirDude - P2P LAN Networking Module
Implements zero-configuration UDP discovery and robust 64KB chunked TCP file streaming.
Runs all network listeners in background daemon threads.
"""

import json
import os
import platform
import queue
import socket
import struct
import threading
import time
import uuid
from typing import Callable, Dict, Optional

from utils import calculate_file_hash, format_bytes, get_local_ip

# Protocol Constants
UDP_DISCOVERY_PORT: int = 45454
TCP_TRANSFER_PORT: int = 45455
CHUNK_SIZE: int = 64 * 1024  # 64 KB chunk size per project specification
BROADCAST_INTERVAL: float = 3.0  # seconds
HEADER_PREFIX_FORMAT: str = "!I"  # 4-byte unsigned integer (Network big-endian)


class AirDudeNetwork:
    """
    Core networking controller for AirDude P2P local file transfer.
    Manages UDP device discovery (broadcast/listen) and TCP file transfer (server/client).
    """

    def __init__(
        self,
        hostname: Optional[str] = None,
        save_directory: str = "received_files",
        tcp_port: int = TCP_TRANSFER_PORT,
        udp_port: int = UDP_DISCOVERY_PORT,
        broadcast_port: Optional[int] = None,
        discovery_queue: Optional[queue.Queue] = None,
        transfer_request_queue: Optional[queue.Queue] = None,
        transfer_update_queue: Optional[queue.Queue] = None,
    ):
        self.hostname: str = hostname or socket.gethostname()
        self.device_id: str = f"dev_{uuid.uuid4().hex[:10]}"
        self.os_name: str = platform.system()
        self.local_ip: str = get_local_ip()
        self.tcp_port: int = tcp_port
        self.udp_port: int = udp_port
        self.broadcast_port: int = broadcast_port if broadcast_port is not None else udp_port
        self.save_directory: str = save_directory
        self.discovery_queue: Optional[queue.Queue] = discovery_queue
        self.transfer_request_queue: Optional[queue.Queue] = transfer_request_queue
        self.transfer_update_queue: Optional[queue.Queue] = transfer_update_queue

        # Ensure received files directory exists
        os.makedirs(self.save_directory, exist_ok=True)

        # Thread-safe dictionary of discovered devices:
        # Key: device_key ("ip:tcp_port"), Value: dict of device metadata (hostname, os, ip, last_seen, etc.)
        self.nearby_devices: Dict[str, dict] = {}
        self._devices_lock: threading.Lock = threading.Lock()

        # Threading controls & state
        self._stop_event: threading.Event = threading.Event()
        self._udp_broadcaster_thread: Optional[threading.Thread] = None
        self._udp_listener_thread: Optional[threading.Thread] = None
        self._tcp_server_thread: Optional[threading.Thread] = None
        self._tcp_server_socket: Optional[socket.socket] = None

        # Active transfer session control: transfer_id -> {pause_event, cancel_event}
        self._transfer_sessions: Dict[str, dict] = {}
        self._sessions_lock: threading.RLock = threading.RLock()

        # Port binding error status (if port is already in use)
        self.tcp_error: Optional[str] = None
        self.udp_error: Optional[str] = None

    # ==========================================
    # Transfer Event Queue Helper
    # ==========================================

    def _push_transfer_event(self, event: dict) -> None:
        """Thread-safe push of a transfer lifecycle event to the UI update queue."""
        if self.transfer_update_queue is not None:
            try:
                self.transfer_update_queue.put_nowait(event)
            except Exception:
                pass

    # ==========================================
    # Pause / Cancel / Resume Controls
    # ==========================================

    def pause_transfer(self, transfer_id: str) -> None:
        """Signal a running send_files session to pause after current chunk."""
        base_id = transfer_id.rsplit("_", 1)[0] if "_" in transfer_id else transfer_id
        with self._sessions_lock:
            session = self._transfer_sessions.get(transfer_id) or self._transfer_sessions.get(base_id)
        if session:
            session["pause_event"].set()
            print(f"[AirDude] Pause requested for transfer {transfer_id}")

    def resume_transfer(self, transfer_id: str) -> None:
        """Resume a previously paused send_files session."""
        base_id = transfer_id.rsplit("_", 1)[0] if "_" in transfer_id else transfer_id
        with self._sessions_lock:
            session = self._transfer_sessions.get(transfer_id) or self._transfer_sessions.get(base_id)
        if session:
            session["pause_event"].clear()
            print(f"[AirDude] Resume requested for transfer {transfer_id}")

    def cancel_transfer(self, transfer_id: str) -> None:
        """Signal a running send_files session to abort immediately."""
        base_id = transfer_id.rsplit("_", 1)[0] if "_" in transfer_id else transfer_id
        with self._sessions_lock:
            session = self._transfer_sessions.get(transfer_id) or self._transfer_sessions.get(base_id)
        if session:
            session["cancel_event"].set()
            session["pause_event"].clear()  # Unblock if currently paused
            print(f"[AirDude] Cancel requested for transfer {transfer_id}")

    # ==========================================
    # UDP Device Discovery (Broadcast & Listen)
    # ==========================================

    def set_hostname(self, new_name: str) -> None:
        """Dynamically updates the device's broadcast name and broadcasts it immediately."""
        if new_name and new_name.strip():
            self.hostname = new_name.strip()
            self.broadcast_now()

    def _udp_broadcast_loop(self) -> None:
        """
        Background worker that continuously broadcasts device presence
        every BROADCAST_INTERVAL seconds over UDP to the specified broadcast_port.
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        # Low timeout so thread can respond to stop_event promptly
        sock.settimeout(1.0)

        payload = {
            "service": "AirDude",
            "device_id": self.device_id,
            "hostname": self.hostname,
            "os": self.os_name,
            "ip": self.local_ip,
            "tcp_port": self.tcp_port,
        }
        encoded_data = json.dumps(payload).encode("utf-8")

        # LAN and subnet broadcast only — do not broadcast to localhost or direct self IP
        targets = ["<broadcast>", "255.255.255.255"]
        if self.local_ip and self.local_ip != "127.0.0.1":
            octets = self.local_ip.split(".")
            if len(octets) == 4:
                targets.append(f"{octets[0]}.{octets[1]}.{octets[2]}.255")

        while not self._stop_event.is_set():
            for target in targets:
                try:
                    sock.sendto(encoded_data, (target, self.broadcast_port))
                except Exception:
                    pass

            # Wait for next broadcast or stop signal
            self._stop_event.wait(BROADCAST_INTERVAL)

        sock.close()

    def _udp_listen_loop(self) -> None:
        """
        Background worker that listens for UDP broadcast packets from other peers
        and updates the nearby_devices catalog.
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_BROADCAST"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        # On Windows, disable WSAECONNRESET to prevent UDP crash on ICMP port unreachable
        if hasattr(socket, "SIO_UDP_CONNRESET"):
            try:
                sock.ioctl(socket.SIO_UDP_CONNRESET, False)
            except Exception:
                pass

        try:
            sock.bind(("", self.udp_port))
            self.udp_error = None
        except OSError as e:
            err_msg = f"UDP discovery port {self.udp_port} is already in use by another program: {e}"
            print(f"[AirDude UDP] ERROR: {err_msg}")
            self.udp_error = err_msg
            sock.close()
            return

        sock.settimeout(1.0)

        while not self._stop_event.is_set():
            try:
                data, addr = sock.recvfrom(4096)
                sender_ip = addr[0]

                # 1. Completely ignore localhost (127.0.0.1)
                if sender_ip == "127.0.0.1":
                    continue

                try:
                    info = json.loads(data.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue

                # Verify payload format belongs to AirDude service
                if isinstance(info, dict) and info.get("service") == "AirDude":
                    remote_device_id = info.get("device_id")
                    remote_tcp_port = info.get("tcp_port", TCP_TRANSFER_PORT)

                    # 2. Ignore if packet originated from this exact instance (device_id match)
                    if remote_device_id and remote_device_id == self.device_id:
                        continue

                    # 3. Ignore if matching own local IP and port, or same hostname and port
                    if (sender_ip == self.local_ip or info.get("ip") == self.local_ip) and remote_tcp_port == self.tcp_port:
                        continue
                    if info.get("hostname") == self.hostname and remote_tcp_port == self.tcp_port:
                        continue

                    # Unique device key prevents duplicate cards for the same device
                    device_key = remote_device_id or f"{sender_ip}:{remote_tcp_port}"

                    info["last_seen"] = time.time()
                    info["ip"] = sender_ip
                    info["device_id"] = device_key
                    info["device_key"] = device_key
                    info["is_self"] = False

                    peer_name = info.get("hostname", "Unknown")
                    print(f"[{self.hostname} UDP] Received discovery packet from '{peer_name}' ({sender_ip}:{remote_tcp_port}, id={device_key})")
                    print(f"[{self.hostname} UDP] -> New peer discovered: '{peer_name}'! Pushing to UI queue.")

                    with self._devices_lock:
                        self.nearby_devices[device_key] = info

                    # Push to thread-safe discovery queue for UI consumption
                    if self.discovery_queue is not None:
                        try:
                            self.discovery_queue.put_nowait(info.copy())
                        except Exception as q_err:
                            print(f"[{self.hostname}] Error putting to discovery queue: {q_err}")
            except socket.timeout:
                continue
            except (ConnectionResetError, OSError):
                # Silently catch Windows ICMP reset or socket interrupts during shutdown
                continue
            except Exception:
                if self._stop_event.is_set():
                    break

        sock.close()

    def broadcast_now(self) -> None:
        """
        Sends an immediate one-off UDP discovery broadcast.
        Useful when manually refreshing or triggering fast peer discovery.
        """
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            payload = {
                "service": "AirDude",
                "device_id": self.device_id,
                "hostname": self.hostname,
                "os": self.os_name,
                "ip": self.local_ip,
                "tcp_port": self.tcp_port,
            }
            encoded_data = json.dumps(payload).encode("utf-8")
            targets = ["<broadcast>", "255.255.255.255"]
            if self.local_ip and self.local_ip != "127.0.0.1":
                octets = self.local_ip.split(".")
                if len(octets) == 4:
                    targets.append(f"{octets[0]}.{octets[1]}.{octets[2]}.255")
            for target in targets:
                try:
                    sock.sendto(encoded_data, (target, self.broadcast_port))
                except Exception:
                    pass
            sock.close()
        except Exception:
            pass

    def start_discovery(self) -> None:
        """
        Spawns UDP broadcaster and listener in background daemon threads.
        """
        self._stop_event.clear()

        self._udp_broadcaster_thread = threading.Thread(
            target=self._udp_broadcast_loop,
            name=f"AirDude-UDP-Broadcaster-{self.hostname}",
            daemon=True,
        )
        self._udp_listener_thread = threading.Thread(
            target=self._udp_listen_loop,
            name=f"AirDude-UDP-Listener-{self.hostname}",
            daemon=True,
        )

        self._udp_broadcaster_thread.start()
        self._udp_listener_thread.start()
        print(f"[{self.hostname} Discovery] Listening on UDP port {self.udp_port}, broadcasting to port {self.broadcast_port}")

    def get_nearby_devices(self, active_within_seconds: float = 10.0, exclude_self: bool = False) -> Dict[str, dict]:
        """
        Returns a snapshot of recently discovered peers on the LAN.
        
        :param active_within_seconds: Devices not seen within this timeframe are filtered out.
        :param exclude_self: If True, filters out broadcasts originating from this local instance.
        :return: Dict mapping device_key ('ip:tcp_port') -> peer device info.
        """
        current_time = time.time()
        active_devices = {}

        with self._devices_lock:
            for key, device_info in list(self.nearby_devices.items()):
                # Check device recency
                if current_time - device_info.get("last_seen", 0) <= active_within_seconds:
                    if exclude_self and device_info.get("is_self", False):
                        continue
                    active_devices[key] = device_info.copy()

        return active_devices

    # ==========================================
    # TCP Server (Receive Files in 64KB Chunks)
    # ==========================================

    def _recv_exact(self, sock: socket.socket, num_bytes: int) -> Optional[bytes]:
        """
        Helper to read an exact number of bytes from a TCP stream.
        Handles TCP stream fragmentation safely.
        """
        buffer = bytearray()
        while len(buffer) < num_bytes:
            packet = sock.recv(min(num_bytes - len(buffer), CHUNK_SIZE))
            if not packet:
                return None
            buffer.extend(packet)
        return bytes(buffer)

    def _handle_incoming_connection(self, conn: socket.socket, addr: tuple) -> None:
        """
        Handles a single incoming TCP transfer session:
        1. Reads 4-byte header length.
        2. Reads & parses JSON metadata header.
        3. Streams file payload to disk in 64KB chunks.
        4. Validates SHA-256 integrity against header hash.
        5. Sends transfer status response back to client.
        """
        sender_ip, sender_port = addr
        print(f"\n[AirDude TCP Server] Incoming connection established from {sender_ip}:{sender_port}")

        try:
            # 1. Read 4-byte big-endian header length prefix
            prefix_data = self._recv_exact(conn, struct.calcsize(HEADER_PREFIX_FORMAT))
            if not prefix_data:
                print(f"[AirDude TCP Server] Connection dropped before receiving header prefix from {sender_ip}")
                return

            header_length = struct.unpack(HEADER_PREFIX_FORMAT, prefix_data)[0]

            # 2. Read JSON header
            header_bytes = self._recv_exact(conn, header_length)
            if not header_bytes:
                print(f"[AirDude TCP Server] Failed to read complete header from {sender_ip}")
                return

            header = json.loads(header_bytes.decode("utf-8"))
            sender_name = header.get("sender_name", "Unknown")

            # Handle batch or single-file headers
            if "files" in header and isinstance(header["files"], list):
                files_list = header["files"]
                total_files = header.get("total_files", len(files_list))
                total_size = header.get("total_size", sum(f.get("filesize", 0) for f in files_list))
            else:
                files_list = [{
                    "filename": header.get("filename", "received_file"),
                    "filesize": int(header.get("filesize", 0)),
                    "file_hash": header.get("file_hash", ""),
                }]
                total_files = 1
                total_size = int(header.get("filesize", 0))

            display_name = files_list[0]["filename"] if total_files == 1 else f"{files_list[0]['filename']} (+{total_files - 1} more)"
            print(f"[AirDude TCP Server] Incoming transfer request from '{sender_name}' ({sender_ip}):")
            print(f"  - Total files: {total_files} ({format_bytes(total_size)})")
            for idx, f in enumerate(files_list, start=1):
                print(f"    [{idx}] {f.get('filename')} ({format_bytes(f.get('filesize', 0))})")

            # 3. Handshake: Trigger ONE popup modal in UI for the entire batch
            decision = {"accepted": False, "save_dir": self.save_directory}
            approval_event = threading.Event()

            if self.transfer_request_queue is not None:
                request_payload = {
                    "sender_name": sender_name,
                    "sender_ip": sender_ip,
                    "files": files_list,
                    "total_files": total_files,
                    "total_size": total_size,
                    "filename": display_name,
                    "filesize": total_size,
                    "approval_event": approval_event,
                    "decision": decision,
                }
                self.transfer_request_queue.put(request_payload)
                print(f"[AirDude TCP Server] Paused. Awaiting user response in UI modal for {total_files} file(s) (30s timeout)...")
                # Wait up to 30 seconds for recipient approval in UI
                approval_event.wait(timeout=30.0)
            else:
                decision["accepted"] = True

            if not decision.get("accepted", False):
                print(f"[AirDude TCP Server] Transfer of {total_files} file(s) was DECLINED or timed out.")
                decline_resp = json.dumps({
                    "status": "DECLINED",
                    "message": "Transfer declined by recipient (or timed out).",
                }).encode("utf-8")
                try:
                    conn.sendall(struct.pack(HEADER_PREFIX_FORMAT, len(decline_resp)) + decline_resp)
                except Exception:
                    pass
                return

            # If accepted, send ACCEPTED handshake response to sender
            print(f"[AirDude TCP Server] Transfer ACCEPTED. Destination: {decision.get('save_dir')}")
            accept_resp = json.dumps({
                "status": "ACCEPTED",
                "message": "Transfer accepted. Ready for batch stream.",
            }).encode("utf-8")
            conn.sendall(struct.pack(HEADER_PREFIX_FORMAT, len(accept_resp)) + accept_resp)

            # Ensure destination directory exists
            target_directory = decision.get("save_dir", self.save_directory)
            os.makedirs(target_directory, exist_ok=True)

            # 4. Stream each file payload in 64KB chunks directly to disk
            all_valid = True
            transfer_id = str(uuid.uuid4())[:12]
            batch_start_time = time.time()

            for f_idx, file_meta in enumerate(files_list, start=1):
                f_name = os.path.basename(file_meta.get("filename", f"received_{f_idx}"))
                f_size = int(file_meta.get("filesize", 0))
                expected_hash = file_meta.get("file_hash", "")
                destination_path = os.path.join(target_directory, f_name)

                print(f"\n[TCP RECEIVER] Starting receive for file [{f_idx}/{total_files}]: '{f_name}' ({format_bytes(f_size)})")
                print(f"[TCP RECEIVER] Writing to: {destination_path}")

                # Push START event to UI
                self._push_transfer_event({
                    "type": "TRANSFER_START",
                    "transfer_id": f"{transfer_id}_{f_idx}",
                    "filename": f_name,
                    "total_size": f_size,
                    "bytes_done": 0,
                    "direction": "RECEIVE",
                    "peer_name": sender_name,
                    "peer_ip": sender_ip,
                    "status": "in_progress",
                    "file_index": f_idx,
                    "total_files": total_files,
                    "start_time": time.time(),
                    "saved_path": destination_path,
                })

                bytes_received = 0
                file_ok = True
                chunk_count = 0

                try:
                    with open(destination_path, "wb") as disk_file:
                        last_progress_time = time.time()
                        while bytes_received < f_size:
                            chunk_to_read = min(CHUNK_SIZE, f_size - bytes_received)
                            chunk = conn.recv(chunk_to_read)
                            if not chunk:
                                raise ConnectionError(f"Connection lost while receiving stream for '{f_name}'.")
                            disk_file.write(chunk)
                            bytes_received += len(chunk)
                            chunk_count += 1

                            now = time.time()
                            # Push PROGRESS event every ~256KB, or every 0.05s, or at 100%
                            if bytes_received == f_size or (now - last_progress_time >= 0.05) or (chunk_count % 4 == 0):
                                last_progress_time = now
                                self._push_transfer_event({
                                    "type": "TRANSFER_PROGRESS",
                                    "transfer_id": f"{transfer_id}_{f_idx}",
                                    "filename": f_name,
                                    "bytes_done": bytes_received,
                                    "total_size": f_size,
                                    "direction": "RECEIVE",
                                    "peer_name": sender_name,
                                    "status": "in_progress",
                                })

                            # Debug print every 16 chunks (~1 MB)
                            if chunk_count % 16 == 0 or bytes_received == f_size:
                                print(f"[TCP RECEIVER] Writing chunk to '{f_name}': {format_bytes(bytes_received)} / {format_bytes(f_size)}")

                except Exception as recv_err:
                    print(f"[TCP RECEIVER] ERROR writing file '{f_name}': {recv_err}")
                    file_ok = False
                    all_valid = False
                    self._push_transfer_event({
                        "type": "TRANSFER_FAILED",
                        "transfer_id": f"{transfer_id}_{f_idx}",
                        "filename": f_name,
                        "direction": "RECEIVE",
                        "peer_name": sender_name,
                        "status": "failed",
                        "message": str(recv_err),
                    })
                    raise

                # === STRICT VERIFICATION: size + SHA-256 ===
                actual_size = os.path.getsize(destination_path)
                if actual_size != f_size:
                    err_msg = f"Size mismatch for '{f_name}': expected {f_size} bytes, got {actual_size} bytes."
                    print(f"[TCP RECEIVER] CRITICAL: {err_msg}")
                    all_valid = False
                    file_ok = False
                    self._push_transfer_event({
                        "type": "TRANSFER_FAILED",
                        "transfer_id": f"{transfer_id}_{f_idx}",
                        "filename": f_name,
                        "direction": "RECEIVE",
                        "peer_name": sender_name,
                        "status": "failed",
                        "message": err_msg,
                        "saved_path": destination_path,
                    })
                else:
                    calculated_hash = calculate_file_hash(destination_path, chunk_size=CHUNK_SIZE)
                    if calculated_hash.lower() == expected_hash.lower():
                        elapsed = time.time() - batch_start_time
                        print(f"[RECEIVED] Saved '{f_name}' (Size: {format_bytes(actual_size)}, Hash: {calculated_hash})")
                        print(f"[TCP RECEIVER] SHA-256 integrity check PASSED for '{f_name}'")
                        self._push_transfer_event({
                            "type": "TRANSFER_COMPLETE",
                            "transfer_id": f"{transfer_id}_{f_idx}",
                            "filename": f_name,
                            "total_size": f_size,
                            "bytes_done": f_size,
                            "direction": "RECEIVE",
                            "peer_name": sender_name,
                            "status": "completed",
                            "hash_ok": True,
                            "saved_path": destination_path,
                            "elapsed": elapsed,
                            "message": "SHA-256 Verified",
                        })
                    else:
                        err_msg = f"SHA-256 MISMATCH for '{f_name}'! Expected: {expected_hash[:16]}... Got: {calculated_hash[:16]}..."
                        print(f"[TCP RECEIVER] CRITICAL: {err_msg}")
                        all_valid = False
                        file_ok = False
                        self._push_transfer_event({
                            "type": "TRANSFER_FAILED",
                            "transfer_id": f"{transfer_id}_{f_idx}",
                            "filename": f_name,
                            "direction": "RECEIVE",
                            "peer_name": sender_name,
                            "status": "failed",
                            "message": "SHA-256 hash mismatch — file corrupted.",
                            "saved_path": destination_path,
                        })

            # 5. Send batch status response back to client
            if all_valid:
                response = {
                    "status": "SUCCESS",
                    "message": f"All {total_files} file(s) received and verified.",
                    "total_files": total_files,
                }
            else:
                response = {
                    "status": "ERROR",
                    "message": "One or more files failed SHA-256 verification.",
                    "total_files": total_files,
                }

            response_bytes = json.dumps(response).encode("utf-8")
            response_prefix = struct.pack(HEADER_PREFIX_FORMAT, len(response_bytes))
            conn.sendall(response_prefix + response_bytes)

        except Exception as e:
            print(f"[AirDude TCP Server] Transfer error from {sender_ip}: {e}")
            try:
                error_response = json.dumps({"status": "ERROR", "message": str(e)}).encode("utf-8")
                conn.sendall(struct.pack(HEADER_PREFIX_FORMAT, len(error_response)) + error_response)
            except Exception:
                pass
        finally:
            conn.close()

    def _tcp_server_loop(self) -> None:
        """
        Background listener loop accepting incoming TCP connections on port 45455.
        """
        self._tcp_server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._tcp_server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._tcp_server_socket.bind(("", self.tcp_port))
            self._tcp_server_socket.listen(5)
            self._tcp_server_socket.settimeout(1.0)
            self.tcp_error = None
        except OSError as e:
            err_msg = f"TCP transfer port {self.tcp_port} is already in use by another program: {e}"
            print(f"[AirDude TCP Server] ERROR: {err_msg}")
            self.tcp_error = err_msg
            return

        print(f"[AirDude TCP Server] Listening for incoming transfers on port {self.tcp_port}")

        while not self._stop_event.is_set():
            try:
                conn, addr = self._tcp_server_socket.accept()
                # Spawn a worker thread per transfer so server remains responsive
                client_thread = threading.Thread(
                    target=self._handle_incoming_connection,
                    args=(conn, addr),
                    name=f"AirDude-Transfer-{addr[0]}",
                    daemon=True,
                )
                client_thread.start()
            except socket.timeout:
                continue
            except Exception:
                if self._stop_event.is_set():
                    break

        if self._tcp_server_socket:
            self._tcp_server_socket.close()

    def start_transfer_server(self) -> None:
        """
        Starts the TCP transfer server in a background daemon thread.
        """
        self._stop_event.clear()
        self._tcp_server_thread = threading.Thread(
            target=self._tcp_server_loop,
            name="AirDude-TCP-Server",
            daemon=True,
        )
        self._tcp_server_thread.start()

    # ==========================================
    # TCP Client (Stream File in 64KB Chunks)
    # ==========================================

    def send_files(
        self,
        target_ip: str,
        filepaths: list,
        sender_name: Optional[str] = None,
        port: int = TCP_TRANSFER_PORT,
        progress_callback: Optional[Callable[[int, int, str, int, int], None]] = None,
        peer_name: Optional[str] = None,
    ) -> dict:
        """
        Connects to peer at target_ip:port, transmits a single batch JSON metadata header
        covering all files, and streams each file sequentially in 64KB chunks upon acceptance.
        
        :param target_ip: Destination IPv4 address.
        :param filepaths: List of file paths to send.
        :param sender_name: Friendly name of sender.
        :param port: Destination TCP port.
        :param progress_callback: Optional callback receiving (bytes_sent_file, file_size, current_filename, file_idx, total_files).
        :param peer_name: Friendly name of recipient peer for UI display.
        :return: Server acknowledgment dictionary.
        """
        valid_files = [fp for fp in filepaths if os.path.isfile(fp)]
        if not valid_files:
            raise FileNotFoundError("No valid files to send.")

        sender = sender_name or self.hostname
        recipient_label = peer_name or target_ip
        transfer_id = str(uuid.uuid4())[:12]
        print(f"\n[TCP SENDER] Preparing batch transfer of {len(valid_files)} file(s) to {target_ip}:{port} ({recipient_label})...")

        # Register this transfer session so pause/cancel controls work
        pause_event = threading.Event()
        cancel_event = threading.Event()
        with self._sessions_lock:
            self._transfer_sessions[transfer_id] = {
                "pause_event": pause_event,
                "cancel_event": cancel_event,
            }

        files_meta = []
        total_batch_size = 0
        for fp in valid_files:
            fn = os.path.basename(fp)
            fs = os.path.getsize(fp)
            print(f"[TCP SENDER] Hashing '{fn}' ({format_bytes(fs)}) for integrity check...")
            fh = calculate_file_hash(fp, chunk_size=CHUNK_SIZE)
            print(f"[TCP SENDER] Hash computed: {fh[:32]}...")
            files_meta.append({
                "filename": fn,
                "filesize": fs,
                "file_hash": fh,
                "filepath": fp,
            })
            total_batch_size += fs

        # Construct single batch JSON header
        header = {
            "type": "BATCH_TRANSFER",
            "sender_name": sender,
            "total_files": len(files_meta),
            "total_size": total_batch_size,
            "files": [
                {"filename": m["filename"], "filesize": m["filesize"], "file_hash": m["file_hash"]}
                for m in files_meta
            ],
        }
        header_bytes = json.dumps(header).encode("utf-8")
        header_prefix = struct.pack(HEADER_PREFIX_FORMAT, len(header_bytes))

        # Immediately emit initial TRANSFER_START events for each file so cards appear on UI
        for idx, m in enumerate(files_meta, start=1):
            self._push_transfer_event({
                "type": "TRANSFER_START",
                "transfer_id": f"{transfer_id}_{idx}",
                "filename": m["filename"],
                "file_path": m.get("filepath", ""),
                "target_ip": target_ip,
                "target_port": port,
                "total_size": m.get("filesize", 0),
                "bytes_done": 0,
                "direction": "SEND",
                "peer_name": recipient_label,
                "status": "waiting",
                "waiting": True,
                "file_index": idx,
                "total_files": len(files_meta),
                "start_time": time.time(),
            })

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(30.0)

        try:
            sock.connect((target_ip, port))
            print(f"[TCP SENDER] Connected to {target_ip}:{port}. Sending batch metadata header...")

            # Send length-prefixed header
            sock.sendall(header_prefix + header_bytes)

            # Wait for recipient handshake approval (ACCEPTED or DECLINED) with 30s timeout
            print(f"[TCP SENDER] Batch header sent. Waiting for recipient response in modal (30s timeout)...")
            handshake_prefix = self._recv_exact(sock, struct.calcsize(HEADER_PREFIX_FORMAT))
            if not handshake_prefix:
                raise ConnectionError("Recipient disconnected before responding to transfer request.")

            handshake_len = struct.unpack(HEADER_PREFIX_FORMAT, handshake_prefix)[0]
            handshake_bytes = self._recv_exact(sock, handshake_len)
            if not handshake_bytes:
                raise ConnectionError("Failed to receive handshake response.")

            handshake = json.loads(handshake_bytes.decode("utf-8"))
            if handshake.get("status") != "ACCEPTED":
                print(f"[TCP SENDER] Recipient DECLINED transfer: {handshake.get('message')}")
                # Push declined events for all files
                for idx, m in enumerate(files_meta, start=1):
                    self._push_transfer_event({
                        "type": "TRANSFER_FAILED",
                        "transfer_id": f"{transfer_id}_{idx}",
                        "filename": m["filename"],
                        "file_path": m.get("filepath", ""),
                        "target_ip": target_ip,
                        "target_port": port,
                        "total_size": m.get("filesize", 0),
                        "direction": "SEND",
                        "peer_name": recipient_label,
                        "status": "declined",
                        "message": "Transfer declined by recipient.",
                    })
                return handshake

            print(f"[TCP SENDER] Recipient ACCEPTED! Streaming {len(files_meta)} file(s) ({format_bytes(total_batch_size)}) in 64KB chunks...")

            # Stream each file sequentially over the same socket
            for idx, m in enumerate(files_meta, start=1):
                fpath = m["filepath"]
                fsize = m["filesize"]
                fname = m["filename"]
                bytes_sent = 0
                file_start_time = time.time()

                print(f"\n[TCP SENDER] Sending file: '{fname}' [{idx}/{len(files_meta)}] ({format_bytes(fsize)})")

                # Push START event (transition from waiting to active in_progress)
                self._push_transfer_event({
                    "type": "TRANSFER_START",
                    "transfer_id": f"{transfer_id}_{idx}",
                    "filename": fname,
                    "file_path": fpath,
                    "target_ip": target_ip,
                    "target_port": port,
                    "total_size": fsize,
                    "bytes_done": 0,
                    "direction": "SEND",
                    "peer_name": recipient_label,
                    "status": "in_progress",
                    "waiting": False,
                    "file_index": idx,
                    "total_files": len(files_meta),
                    "start_time": file_start_time,
                })

                chunk_count = 0
                with open(fpath, "rb") as f:
                    last_progress_time = time.time()
                    while bytes_sent < fsize:
                        chunk = f.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        sock.sendall(chunk)
                        bytes_sent += len(chunk)
                        chunk_count += 1

                        now = time.time()
                        # Push PROGRESS to UI every ~256KB, or every 0.05s, or at 100%
                        if bytes_sent == fsize or (now - last_progress_time >= 0.05) or (chunk_count % 4 == 0):
                            last_progress_time = now
                            self._push_transfer_event({
                                "type": "TRANSFER_PROGRESS",
                                "transfer_id": f"{transfer_id}_{idx}",
                                "filename": fname,
                                "bytes_done": bytes_sent,
                                "total_size": fsize,
                                "direction": "SEND",
                                "peer_name": recipient_label,
                                "status": "in_progress",
                            })

                            if progress_callback:
                                progress_callback(bytes_sent, fsize, fname, idx, len(files_meta))

                        # Debug print every 16 chunks (~1MB)
                        if chunk_count % 16 == 0 or bytes_sent == fsize:
                            print(f"[TCP SENDER] Sending file: '{fname}' — {format_bytes(bytes_sent)} / {format_bytes(fsize)}")

                        # ── Pause / Cancel checks ──
                        if cancel_event.is_set():
                            raise ConnectionAbortedError(f"Transfer '{fname}' cancelled by user.")
                        if pause_event.is_set():
                            self._push_transfer_event({
                                "type": "TRANSFER_PAUSED",
                                "transfer_id": f"{transfer_id}_{idx}",
                                "filename": fname,
                                "bytes_done": bytes_sent,
                                "total_size": fsize,
                                "direction": "SEND",
                                "peer_name": recipient_label,
                                "status": "paused",
                            })
                            print(f"[TCP SENDER] Transfer '{fname}' PAUSED at {format_bytes(bytes_sent)}")
                            while pause_event.is_set():
                                if cancel_event.is_set():
                                    raise ConnectionAbortedError(f"Transfer '{fname}' cancelled while paused.")
                                time.sleep(0.1)
                            self._push_transfer_event({
                                "type": "TRANSFER_RESUMED",
                                "transfer_id": f"{transfer_id}_{idx}",
                                "filename": fname,
                                "bytes_done": bytes_sent,
                                "total_size": fsize,
                                "direction": "SEND",
                                "peer_name": recipient_label,
                                "status": "in_progress",
                            })
                            print(f"[TCP SENDER] Transfer '{fname}' RESUMED at {format_bytes(bytes_sent)}")

                print(f"[TCP SENDER] File '{fname}' stream complete — {format_bytes(bytes_sent)} sent.")

            # Await server verification response for the batch
            print(f"\n[TCP SENDER] All {len(files_meta)} file(s) streamed. Waiting for server SHA-256 verification...")
            resp_prefix = self._recv_exact(sock, struct.calcsize(HEADER_PREFIX_FORMAT))
            if resp_prefix:
                resp_length = struct.unpack(HEADER_PREFIX_FORMAT, resp_prefix)[0]
                resp_bytes = self._recv_exact(sock, resp_length)
                if resp_bytes:
                    response = json.loads(resp_bytes.decode("utf-8"))
                    final_status = response.get("status", "UNKNOWN")
                    print(f"[TCP SENDER] Server verification response: {final_status} — {response.get('message')}")

                    # Push COMPLETE or FAILED events for each file based on server response
                    event_type = "TRANSFER_COMPLETE" if final_status == "SUCCESS" else "TRANSFER_FAILED"
                    for idx, m in enumerate(files_meta, start=1):
                        self._push_transfer_event({
                            "type": event_type,
                            "transfer_id": f"{transfer_id}_{idx}",
                            "filename": m["filename"],
                            "total_size": m["filesize"],
                            "bytes_done": m["filesize"],
                            "direction": "SEND",
                            "peer_name": recipient_label,
                            "status": "completed" if final_status == "SUCCESS" else "failed",
                            "hash_ok": final_status == "SUCCESS",
                            "message": response.get("message", ""),
                        })
                    return response

            # Fallback: assume success if server gave no response
            fallback = {"status": "SUCCESS", "message": "Batch transfer completed (no ACK received)."}
            for idx, m in enumerate(files_meta, start=1):
                self._push_transfer_event({
                    "type": "TRANSFER_COMPLETE",
                    "transfer_id": f"{transfer_id}_{idx}",
                    "filename": m["filename"],
                    "total_size": m["filesize"],
                    "bytes_done": m["filesize"],
                    "direction": "SEND",
                    "peer_name": recipient_label,
                    "status": "completed",
                    "hash_ok": None,
                    "message": "Transfer complete (ACK not received).",
                })
            return fallback

        except (socket.timeout, TimeoutError):
            err_msg = "Error: Receiver did not respond (Timed out)"
            print(f"[TCP SENDER] TIMEOUT: {err_msg} from {target_ip}:{port}")
            for idx, m in enumerate(files_meta, start=1):
                self._push_transfer_event({
                    "type": "TRANSFER_FAILED",
                    "transfer_id": f"{transfer_id}_{idx}",
                    "filename": m["filename"],
                    "file_path": m.get("filepath", ""),
                    "target_ip": target_ip,
                    "target_port": port,
                    "total_size": m.get("filesize", 0),
                    "direction": "SEND",
                    "peer_name": recipient_label,
                    "status": "failed",
                    "message": "Error: Receiver did not respond (Timed out)",
                })
            raise TimeoutError("Error: Receiver did not respond (Timed out)")

        except Exception as e:
            err_str = str(e)
            if "timed out" in err_str.lower():
                err_msg = "Error: Receiver did not respond (Timed out)"
            elif not err_str.lower().startswith("error"):
                err_msg = f"Error: {err_str}"
            else:
                err_msg = err_str

            print(f"[TCP SENDER] ERROR during transfer to {target_ip}: {err_msg}")
            # Push failed events for all files
            for idx, m in enumerate(files_meta, start=1):
                self._push_transfer_event({
                    "type": "TRANSFER_FAILED",
                    "transfer_id": f"{transfer_id}_{idx}",
                    "filename": m["filename"],
                    "file_path": m.get("filepath", ""),
                    "target_ip": target_ip,
                    "target_port": port,
                    "total_size": m.get("filesize", 0),
                    "direction": "SEND",
                    "peer_name": recipient_label,
                    "status": "failed",
                    "message": err_msg,
                })
            raise

        finally:
            try:
                sock.close()
            except Exception:
                pass
            # Cleanup session registration
            with self._sessions_lock:
                self._transfer_sessions.pop(transfer_id, None)

    def send_file(
        self,
        target_ip: str,
        filepath: str,
        sender_name: Optional[str] = None,
        port: int = TCP_TRANSFER_PORT,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        peer_name: Optional[str] = None,
    ) -> dict:
        """Single file convenience wrapper calling send_files."""
        def cb_wrapper(bs, ts, fn, idx, tot):
            if progress_callback:
                progress_callback(bs, ts)
        return self.send_files(
            target_ip=target_ip,
            filepaths=[filepath],
            sender_name=sender_name,
            port=port,
            progress_callback=cb_wrapper,
            peer_name=peer_name,
        )

    # ==========================================
    # Lifecycle Management
    # ==========================================

    def stop(self) -> None:
        """
        Signals all daemon threads to stop and closes active server sockets.
        """
        print("[AirDude] Shutting down networking services...")
        self._stop_event.set()
        if self._tcp_server_socket:
            try:
                self._tcp_server_socket.close()
            except Exception:
                pass


# Module-level convenience functions matching user requirements
_default_instance: Optional[AirDudeNetwork] = None


def get_default_network() -> AirDudeNetwork:
    """Returns singleton default network instance."""
    global _default_instance
    if _default_instance is None:
        _default_instance = AirDudeNetwork()
    return _default_instance


def start_discovery() -> None:
    get_default_network().start_discovery()


def start_transfer_server(save_directory: str = "received_files") -> None:
    network = get_default_network()
    network.save_directory = save_directory
    os.makedirs(save_directory, exist_ok=True)
    network.start_transfer_server()


def send_file(target_ip: str, filepath: str, sender_name: Optional[str] = None, port: int = TCP_TRANSFER_PORT) -> dict:
    return get_default_network().send_file(target_ip, filepath, sender_name=sender_name, port=port)


def get_nearby_devices(active_within_seconds: float = 10.0, exclude_self: bool = False) -> Dict[str, dict]:
    return get_default_network().get_nearby_devices(active_within_seconds=active_within_seconds, exclude_self=exclude_self)
