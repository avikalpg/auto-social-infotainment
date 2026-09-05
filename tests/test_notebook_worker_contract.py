import json
import subprocess
import tempfile
import unittest
from pathlib import Path

WORKER = Path(__file__).resolve().parents[1] / "workers" / "notebooklm" / "hp-local-download-worker.mjs"


class NotebookWorkerContractTests(unittest.TestCase):
    def run_worker(self, root: Path, request: dict[str, object]) -> subprocess.CompletedProcess[str]:
        request_path = root / "request.json"
        request_path.write_text(json.dumps(request))
        return subprocess.run(["node", str(WORKER), str(request_path)], text=True, capture_output=True, check=False)

    def test_node_syntax(self):
        subprocess.run(["node", "--check", str(WORKER)], check=True)

    def test_worker_refuses_output_outside_allow_root_before_cdp(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            proc = self.run_worker(root, {"request_id": "r1", "story_id": "s1", "notebook_url": "https://notebook.google.com/notebook/example", "artifact_title": "Generic Artifact", "output_path": str(root.parent / "escape.mp4"), "allow_root": str(root / "allowed"), "receipt_path": str(root / "allowed" / "receipt.json")})
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("outside configured allow_root", proc.stderr + proc.stdout)

    def test_worker_rejects_private_extra_keys(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            proc = self.run_worker(root, {"request_id": "r1", "story_id": "s1", "notebook_url": "https://notebook.google.com/notebook/example", "artifact_title": "Generic Artifact", "output_path": str(root / "allowed" / "out.mp4"), "allow_root": str(root / "allowed"), "personal_path": "/home/example/private"})
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("unsupported request keys", proc.stderr + proc.stdout)

    def test_worker_rejects_symlinked_output_parent(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            allowed, outside = root / "allowed", root / "outside"
            allowed.mkdir(); outside.mkdir()
            (allowed / "escape").symlink_to(outside, target_is_directory=True)
            proc = self.run_worker(root, {"request_id": "r1", "story_id": "s1", "notebook_url": "https://notebook.google.com/notebook/example", "artifact_title": "Generic Artifact", "output_path": str(allowed / "escape" / "out.mp4"), "allow_root": str(allowed)})
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("resolves outside configured allow_root", proc.stderr + proc.stdout)

    def test_omitted_receipt_stays_under_allow_root(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            # An invalid URL stops before CDP; no receipt is created beside the request.
            proc = self.run_worker(root, {"request_id": "r1", "story_id": "s1", "notebook_url": "https://invalid.example/", "artifact_title": "Generic Artifact", "output_path": str(root / "allowed" / "out.mp4"), "allow_root": str(root / "allowed")})
            self.assertNotEqual(proc.returncode, 0)
            self.assertFalse((root / "request.json.receipt.json").exists())


if __name__ == "__main__":
    unittest.main()
