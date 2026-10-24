import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from clm.validation_analysis import PACKET_FIELDS, pcap_metrics


class FakeDecoder:
    def __init__(self, text):
        self.stdout = io.StringIO(text)
    def wait(self):
        return 0
    def kill(self):
        pass


def packet(ts, src, sport, dst, dport, seq, ack_seq=0, length=0, **flags):
    row = {key: "" for key in PACKET_FIELDS}
    row.update({"frame.time_epoch": str(ts / 1000), "frame.len": "100", "frame.cap_len": "100", "ip.len": "86",
                "ip.src": src, "ip.dst": dst, "tcp.srcport": str(sport), "tcp.dstport": str(dport),
                "tcp.stream": "1", "tcp.seq_raw": str(seq), "tcp.ack_raw": str(ack_seq), "tcp.len": str(length),
                "tcp.flags.syn": "1" if flags.get("syn") else "0", "tcp.flags.ack": "1" if flags.get("ack") else "0",
                "tcp.flags.fin": "0", "tcp.flags.reset": "1" if flags.get("rst") else "0",
                "http.request.uri": flags.get("uri", "")})
    return row


class PcapAnalysisTests(unittest.TestCase):
    def decode(self, reset=False, drops=0, recording=None):
        client, vip = "192.168.13.20", "192.168.13.50"
        rows = [packet(80, client, 40000, vip, 8080, 100, syn=True),
                packet(81, vip, 8080, client, 40000, 500, 101, syn=True, ack=True),
                packet(82, client, 40000, vip, 8080, 101, 501, ack=True),
                packet(85, client, 40000, vip, 8080, 101, 501, length=30, ack=True, uri="/stream?format=raw"),
                packet(90, vip, 8080, client, 40000, 501, 131, length=100, ack=True),
                packet(170, vip, 8080, client, 40000, 601, 131, length=100, ack=True)]
        if reset:
            rows.append(packet(180, vip, 8080, client, 40000, 701, 131, rst=True, ack=True))
        text = io.StringIO()
        writer = csv.DictWriter(text, fieldnames=PACKET_FIELDS, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "run/validation/monitor"
            folder.mkdir(parents=True)
            (folder / "traffic.pcap").write_bytes(b"synthetic")
            (folder / "stopped.json").write_text(json.dumps(
                recording if recording is not None else {"complete": True, "start_ms": 0, "end_ms": 250}))
            (folder / "tcpdump.log").write_text(f"6 packets captured\n{drops} packets dropped by kernel\n")
            with patch("clm.validation_analysis.shutil.which", return_value="tshark"), \
                 patch("clm.validation_analysis.subprocess.Popen", return_value=FakeDecoder(text.getvalue())), \
                 patch("clm.validation_analysis.subprocess.run", return_value=SimpleNamespace(stdout="synthetic decoder")):
                return pcap_metrics(folder / "traffic.pcap", 100, 150, 200, vip)

    def test_same_established_stream_delivering_after_migration_survives(self):
        result = self.decode()
        self.assertEqual(result["survival_fraction"], 1)
        self.assertEqual(result["stream_counts"]["survived"], 1)
        self.assertEqual(result["streams"][0]["max_payload_arrival_gap_ms"], 80)
        self.assertIsNone(result["ack_rtt_ms"]["median"])
        self.assertEqual(result["ack_rtt_ms"]["n"], 0)

    def test_reset_within_observation_is_not_hidden_by_prior_recovery(self):
        result = self.decode(reset=True)
        self.assertEqual(result["survival_fraction"], 0)
        self.assertEqual(result["stream_counts"]["lost"], 1)

    def test_capture_drops_invalidate_survival_fraction(self):
        result = self.decode(drops=2)
        self.assertFalse(result["stream_classification_reliable"])
        self.assertIsNone(result["survival_fraction"])

    def test_resource_end_does_not_extend_capture_coverage(self):
        result = self.decode(recording={"complete": False, "start_ms": 0, "end_ms": 250,
                                        "capture_start_ms": 10, "capture_end_ms": 180})
        self.assertFalse(result["covers_window"])
        self.assertFalse(result["stream_classification_reliable"])
        self.assertIsNone(result["survival_fraction"])

    def test_capture_readiness_after_window_start_is_not_full_coverage(self):
        result = self.decode(recording={"complete": True, "start_ms": 0, "end_ms": 250,
                                        "capture_start_ms": 110, "capture_end_ms": 240})
        self.assertFalse(result["covers_window"])

    def test_absent_capture_timestamps_do_not_fall_back_to_resource_times(self):
        result = self.decode(recording={"complete": False, "start_ms": 0, "end_ms": 250,
                                        "capture_start_ms": None, "capture_end_ms": None})
        self.assertFalse(result["covers_window"])


if __name__ == "__main__":
    unittest.main()
