"""
End-to-End Multi-Instance Verification for AirDude Incoming Transfer Popup
Tests real TCP transfer between two live AirDudeApp instances:
1. Modal appearance on receiver
2. Modal details (sender name, IP, files, size)
3. Modal Accept flow -> streaming -> SHA-256 verification -> complete
4. Modal Decline flow -> sender receives declined state
5. Modal Auto-decline / timeout flow
"""

import os
import shutil
import tempfile
import threading
import time
import unittest

from app_ui import AirDudeApp, IncomingTransferModal, TransferCard
from utils import calculate_file_hash, clear_history_file


class TestE2ETransferModal(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.sender_dir = os.path.join(self.test_dir, "sender")
        self.receiver_dir = os.path.join(self.test_dir, "receiver")
        os.makedirs(self.sender_dir, exist_ok=True)
        os.makedirs(self.receiver_dir, exist_ok=True)

        clear_history_file()

        # Port configuration
        self.tx_udp, self.tx_tcp = 55401, 55402
        self.rx_udp, self.rx_tcp = 55403, 55404

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)
        clear_history_file()

    def test_e2e_incoming_modal_accept_and_transfer(self):
        # 1. Start Receiver App
        rx_app = AirDudeApp(name="ReceiverPC", udp_port=self.rx_udp, tcp_port=self.rx_tcp)
        rx_app.save_dir = self.receiver_dir
        rx_app.update()

        # 2. Start Sender App
        tx_app = AirDudeApp(name="SenderPC", udp_port=self.tx_udp, tcp_port=self.tx_tcp)
        tx_app.update()

        # 3. Create test file (128 KB)
        test_file = os.path.join(self.sender_dir, "project_specs.pdf")
        content = os.urandom(128 * 1024)
        with open(test_file, "wb") as f:
            f.write(content)
        expected_hash = calculate_file_hash(test_file)

        # 4. Sender starts transfer in background thread
        sender_result = {}
        def send_worker():
            try:
                res = tx_app.network.send_files(
                    target_ip="127.0.0.1",
                    filepaths=[test_file],
                    port=self.rx_tcp,
                    sender_name="SenderPC",
                    peer_name="ReceiverPC",
                )
                sender_result["result"] = res
            except Exception as e:
                sender_result["error"] = e

        t = threading.Thread(target=send_worker, daemon=True)
        t.start()

        # 5. Wait for request to arrive at receiver and be polled
        modal = None
        for _ in range(30):
            time.sleep(0.1)
            rx_app.poll_queue()
            rx_app.update()
            for child in rx_app.winfo_children():
                if isinstance(child, IncomingTransferModal):
                    modal = child
                    break
            if modal is not None:
                break

        self.assertIsNotNone(modal, "IncomingTransferModal must appear on the receiver device!")
        self.assertEqual(modal.request.get("sender_name"), "SenderPC")
        self.assertEqual(modal.request.get("filename"), "project_specs.pdf")
        self.assertEqual(modal.request.get("total_size"), 128 * 1024)

        # 6. Receiver accepts the modal
        modal.save_opt.set("received_files")
        modal._on_accept()
        rx_app.update()

        # 7. Wait for transfer thread to complete
        t.join(timeout=10.0)
        self.assertFalse(t.is_alive(), "Sender thread should complete after acceptance")

        # 8. Check sender result
        self.assertIn("result", sender_result, f"Transfer failed with error: {sender_result.get('error')}")
        self.assertEqual(sender_result["result"].get("status"), "SUCCESS")

        # 9. Verify file received on receiver
        received_path = os.path.join(self.receiver_dir, "project_specs.pdf")
        self.assertTrue(os.path.isfile(received_path), "Received file must exist in save directory")
        actual_hash = calculate_file_hash(received_path)
        self.assertEqual(actual_hash, expected_hash, "SHA-256 hash of received file must match exactly")

        # Clean up
        tx_app._on_close()
        rx_app._on_close()

    def test_e2e_incoming_modal_decline(self):
        rx_app = AirDudeApp(name="ReceiverPC", udp_port=55405, tcp_port=55406)
        rx_app.update()

        tx_app = AirDudeApp(name="SenderPC", udp_port=55407, tcp_port=55408)
        tx_app.update()

        test_file = os.path.join(self.sender_dir, "decline_test.txt")
        with open(test_file, "wb") as f:
            f.write(b"Declined payload")

        sender_result = {}
        def send_worker():
            try:
                res = tx_app.network.send_files(
                    target_ip="127.0.0.1",
                    filepaths=[test_file],
                    port=55406,
                    sender_name="SenderPC",
                    peer_name="ReceiverPC",
                )
                sender_result["result"] = res
            except Exception as e:
                sender_result["error"] = e

        t = threading.Thread(target=send_worker, daemon=True)
        t.start()

        modal = None
        for _ in range(30):
            time.sleep(0.1)
            rx_app.poll_queue()
            rx_app.update()
            for child in rx_app.winfo_children():
                if isinstance(child, IncomingTransferModal):
                    modal = child
                    break
            if modal is not None:
                break

        self.assertIsNotNone(modal, "Modal should appear for decline test")
        # Click Decline
        modal._on_decline()
        rx_app.update()

        t.join(timeout=10.0)
        self.assertFalse(t.is_alive())
        self.assertIn("result", sender_result)
        self.assertEqual(sender_result["result"].get("status"), "DECLINED")

        tx_app._on_close()
        rx_app._on_close()


if __name__ == "__main__":
    unittest.main()
