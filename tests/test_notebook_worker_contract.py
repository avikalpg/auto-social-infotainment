import json
import subprocess
import tempfile
import unittest
from pathlib import Path

WORKER = (
    Path(__file__).resolve().parents[1] / "workers" / "notebooklm" / "hp-local-download-worker.mjs"
)


class NotebookWorkerContractTests(unittest.TestCase):
    def test_node_syntax(self):
        subprocess.run(["node", "--check", str(WORKER)], check=True)

    def test_worker_refuses_output_outside_allow_root_before_cdp(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            req = {
                "request_id": "r1",
                "story_id": "s1",
                "notebook_url": "https://notebook.google.com/notebook/example",
                "artifact_title": "Generic Artifact",
                "output_path": str(root.parent / "escape.mp4"),
                "allow_root": str(root / "allowed"),
                "receipt_path": str(root / "allowed" / "receipt.json"),
            }
            p = root / "request.json"
            p.write_text(json.dumps(req))
            proc = subprocess.run(
                ["node", str(WORKER), str(p)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("outside configured allow_root", proc.stderr + proc.stdout)

    def test_worker_rejects_private_extra_keys(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            req = {
                "request_id": "r1",
                "story_id": "s1",
                "notebook_url": "https://notebook.google.com/notebook/example",
                "artifact_title": "Generic Artifact",
                "output_path": str(root / "allowed" / "out.mp4"),
                "allow_root": str(root / "allowed"),
                "personal_path": "/home/example/private",
            }
            p = root / "request.json"
            p.write_text(json.dumps(req))
            proc = subprocess.run(
                ["node", str(WORKER), str(p)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("unsupported request keys", proc.stderr + proc.stdout)


if __name__ == "__main__":
    unittest.main()
