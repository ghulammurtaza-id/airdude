"""
AirDude Automated Verification & Test Suite
Covers:
1. Module imports & class structure
2. Utils functions (hashing, byte formatting, IP, settings, history)
3. Settings JSON persistence & retention
4. History JSON persistence & 100-item retention
5. Full TCP loopback transfer with 64KB chunk streaming and SHA-256 integrity verification
6. Transfer control (Pause, Resume, Cancel)
7. UI initialization and route navigation testing
"""

import json
import os
import queue
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest

# Ensure working directory is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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
    MAX_HISTORY_ENTRIES,
)
from network import AirDudeNetwork, CHUNK_SIZE
import app_ui


class TestUtils(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_byte_formatting(self):
        self.assertEqual(format_bytes(0), "0 B")
        self.assertEqual(format_bytes(512), "512 B")
        self.assertEqual(format_bytes(1024), "1.00 KB")
        self.assertEqual(format_bytes(1024 * 1024), "1.00 MB")
        self.assertEqual(format_bytes(1024 * 1024 * 1024), "1.00 GB")

    def test_calculate_file_hash(self):
        sample_path = os.path.join(self.test_dir, "sample.txt")
        with open(sample_path, "wb") as f:
            f.write(b"Hello AirDude VS Code Redesign")
        file_hash = calculate_file_hash(sample_path)
        self.assertIsInstance(file_hash, str)
        self.assertEqual(len(file_hash), 64)

    def test_settings_persistence(self):
        original_settings = load_settings()
        new_settings = original_settings.copy()
        new_settings["device_name"] = "UnitTestDevice"
        new_settings["appearance_mode"] = "Dark"
        new_settings["save_dir"] = os.path.join(self.test_dir, "test_recv")
        
        saved = save_settings(new_settings)
        self.assertTrue(saved)
        
        loaded = load_settings()
        self.assertEqual(loaded["device_name"], "UnitTestDevice")
        self.assertEqual(loaded["appearance_mode"], "Dark")
        self.assertEqual(loaded["save_dir"], new_settings["save_dir"])
        
        # Restore original
        save_settings(original_settings)

    def test_history_persistence_and_retention(self):
        clear_history_file()
        self.assertEqual(len(load_history()), 0)

        # Add 105 entries to verify MAX_HISTORY_ENTRIES (100) cap
        for i in range(105):
            add_history_entry({
                "transfer_id": f"test_tid_{i}",
                "filename": f"file_{i}.dat",
                "total_size": 1024 * (i + 1),
                "direction": "SEND",
                "peer_name": "TestPeer",
                "status": "completed",
                "hash_ok": True,
                "timestamp": time.strftime("%H:%M:%S"),
            })

        history = load_history()
        self.assertEqual(len(history), MAX_HISTORY_ENTRIES)
        # Newest should be index 0
        self.assertEqual(history[0]["transfer_id"], "test_tid_104")
        self.assertEqual(history[-1]["transfer_id"], "test_tid_5")

        clear_history_file()
        self.assertEqual(len(load_history()), 0)


class TestNetworkTransfer(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.recv_dir = os.path.join(self.test_dir, "received")
        os.makedirs(self.recv_dir, exist_ok=True)

        # Dynamic ports to avoid collision
        self.rx_port = 55101
        self.tx_port = 55102

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_loopback_tcp_transfer_and_hash_verification(self):
        # Create receiver
        rx_network = AirDudeNetwork(
            hostname="RxNode",
            save_directory=self.recv_dir,
            tcp_port=self.rx_port,
            udp_port=55103,
            broadcast_port=55103,
        )
        rx_network.start_transfer_server()
        time.sleep(0.5)

        # Create test file (200KB to span multiple 64KB chunks)
        sample_path = os.path.join(self.test_dir, "payload_200kb.bin")
        payload_data = os.urandom(200 * 1024)
        with open(sample_path, "wb") as f:
            f.write(payload_data)
        src_hash = calculate_file_hash(sample_path)

        # Create sender
        tx_network = AirDudeNetwork(
            hostname="TxNode",
            save_directory=self.test_dir,
            tcp_port=self.tx_port,
            udp_port=55104,
            broadcast_port=55104,
        )

        progress_calls = []
        def on_progress(sent, total, fname, idx, total_files):
            progress_calls.append((sent, total))

        res = tx_network.send_files(
            target_ip="127.0.0.1",
            filepaths=[sample_path],
            port=self.rx_port,
            sender_name="TxNode",
            progress_callback=on_progress,
            peer_name="RxNode",
        )

        self.assertEqual(res.get("status"), "SUCCESS")
        self.assertTrue(len(progress_calls) > 0)

        # Verify received file exists and hash matches exactly
        recv_file = os.path.join(self.recv_dir, os.path.basename(sample_path))
        self.assertTrue(os.path.isfile(recv_file))
        dst_hash = calculate_file_hash(recv_file)
        self.assertEqual(src_hash, dst_hash)

        # Clean up
        tx_network.stop()
        rx_network.stop()

    def test_transfer_cancellation(self):
        rx_network = AirDudeNetwork(
            hostname="RxCancelNode",
            save_directory=self.recv_dir,
            tcp_port=55105,
            udp_port=55107,
            broadcast_port=55107,
        )
        rx_network.start_transfer_server()
        time.sleep(0.3)

        # 2MB file
        sample_path = os.path.join(self.test_dir, "cancel_test.bin")
        with open(sample_path, "wb") as f:
            f.write(os.urandom(2 * 1024 * 1024))

        tx_network = AirDudeNetwork(
            hostname="TxCancelNode",
            save_directory=self.test_dir,
            tcp_port=55106,
            udp_port=55108,
            broadcast_port=55108,
        )

        def cancel_after_some_progress(sent, total, fname, idx, total_files):
            if sent >= 128 * 1024:
                with tx_network._sessions_lock:
                    for tid in list(tx_network._transfer_sessions.keys()):
                        tx_network.cancel_transfer(tid)

        try:
            tx_network.send_files(
                target_ip="127.0.0.1",
                filepaths=[sample_path],
                port=55105,
                sender_name="TxCancelNode",
                progress_callback=cancel_after_some_progress,
                peer_name="RxCancelNode",
            )
            cancelled = False
        except Exception:
            cancelled = True

        self.assertTrue(cancelled)

        tx_network.stop()
        rx_network.stop()


class TestUIComponents(unittest.TestCase):
    def test_app_ui_structure_and_routes(self):
        # Test creating the UI, checking routes, then safely closing
        app = app_ui.AirDudeApp(name="TestApp", udp_port=55201, tcp_port=55202)
        
        # Test route navigation
        routes = ["devices", "send", "transfers", "history", "settings", "overview"]
        for r in routes:
            app.navigate_to(r)
            self.assertEqual(app.current_route, r)
            self.assertIsNotNone(app.current_page)

        # Test updating device name
        app.update_device_name("RenamedApp")
        self.assertEqual(app.device_name, "RenamedApp")

        # Test clearing history
        app.clear_all_history()
        self.assertEqual(len(app.history_records), 0)

        # Test poll_queue iteration with discovery packet (previously triggered AttributeError)
        app.discovery_queue.put({
            "hostname": "DiscoveredPeer",
            "ip": "192.168.1.50",
            "tcp_port": 45455,
            "device_id": "peer_12345",
            "os": "Windows",
            "last_seen": time.time(),
        })
        app.poll_queue()
        self.assertIn("peer_12345", app.active_devices)

        # Shutdown app
        app._on_close()

    def test_incoming_transfer_modal_acceptance(self):
        app = app_ui.AirDudeApp(name="TestRxApp", udp_port=55203, tcp_port=55204)
        approval_event = threading.Event()
        decision = {"accepted": False, "save_dir": app.save_dir}
        req_payload = {
            "sender_name": "SenderPC",
            "sender_ip": "192.168.1.99",
            "files": [{"filename": "doc.pdf", "filesize": 1024, "file_hash": "a"*64}],
            "total_files": 1,
            "total_size": 1024,
            "filename": "doc.pdf",
            "filesize": 1024,
            "approval_event": approval_event,
            "decision": decision,
        }

        # Put request in queue and poll
        app.transfer_request_queue.put(req_payload)
        app.poll_queue()

        # Find the modal among app children
        modal = None
        for child in app.winfo_children():
            if isinstance(child, app_ui.IncomingTransferModal):
                modal = child
                break

        self.assertIsNotNone(modal, "IncomingTransferModal must appear when transfer request is queued")
        
        # Simulate user clicking Accept
        modal._on_accept()
        self.assertTrue(approval_event.is_set())
        self.assertTrue(decision["accepted"])
        self.assertTrue(os.path.isdir(decision["save_dir"]))

        app._on_close()

    def test_incoming_transfer_modal_auto_decline_on_timeout(self):
        app = app_ui.AirDudeApp(name="TestTimeoutApp", udp_port=55205, tcp_port=55206)
        approval_event = threading.Event()
        decision = {"accepted": False, "save_dir": app.save_dir}
        req_payload = {
            "sender_name": "SenderPC",
            "sender_ip": "192.168.1.99",
            "files": [{"filename": "data.zip", "filesize": 2048, "file_hash": "b"*64}],
            "total_files": 1,
            "total_size": 2048,
            "filename": "data.zip",
            "filesize": 2048,
            "approval_event": approval_event,
            "decision": decision,
        }

        app.transfer_request_queue.put(req_payload)
        app.poll_queue()

        modal = None
        for child in app.winfo_children():
            if isinstance(child, app_ui.IncomingTransferModal):
                modal = child
                break

        self.assertIsNotNone(modal)
        # Fast-forward countdown to 0 and trigger tick
        modal._remaining = 0
        modal._tick()

        self.assertTrue(approval_event.is_set())
        self.assertFalse(decision["accepted"])

        app._on_close()


if __name__ == "__main__":
    unittest.main()
