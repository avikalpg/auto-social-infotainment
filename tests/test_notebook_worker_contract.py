import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

WORKER = (
    Path(__file__).resolve().parents[1] / "workers" / "notebooklm" / "hp-local-download-worker.mjs"
)
FFMPEG = shutil.which("ffmpeg")


class NotebookWorkerContractTests(unittest.TestCase):
    def run_worker(
        self, root: Path, request: dict[str, object]
    ) -> subprocess.CompletedProcess[str]:
        request_path = root / "request.json"
        request_path.write_text(json.dumps(request))
        return subprocess.run(
            ["node", str(WORKER), str(request_path)], text=True, capture_output=True, check=False
        )

    def test_node_syntax(self):
        subprocess.run(["node", "--check", str(WORKER)], check=True)

    def test_worker_refuses_output_outside_allow_root_before_cdp(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            proc = self.run_worker(
                root,
                {
                    "request_id": "r1",
                    "story_id": "s1",
                    "notebook_url": "https://notebook.google.com/notebook/example",
                    "artifact_title": "Generic Artifact",
                    "output_path": str(root.parent / "escape.mp4"),
                    "allow_root": str(root / "allowed"),
                    "receipt_path": str(root / "allowed" / "receipt.json"),
                },
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("outside configured allow_root", proc.stderr + proc.stdout)

    def test_worker_rejects_private_extra_keys(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            proc = self.run_worker(
                root,
                {
                    "request_id": "r1",
                    "story_id": "s1",
                    "notebook_url": "https://notebook.google.com/notebook/example",
                    "artifact_title": "Generic Artifact",
                    "output_path": str(root / "allowed" / "out.mp4"),
                    "allow_root": str(root / "allowed"),
                    "personal_path": "/home/example/private",
                },
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("unsupported request keys", proc.stderr + proc.stdout)

    def test_worker_rejects_symlinked_output_parent(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            allowed, outside = root / "allowed", root / "outside"
            allowed.mkdir()
            outside.mkdir()
            (allowed / "escape").symlink_to(outside, target_is_directory=True)
            proc = self.run_worker(
                root,
                {
                    "request_id": "r1",
                    "story_id": "s1",
                    "notebook_url": "https://notebook.google.com/notebook/example",
                    "artifact_title": "Generic Artifact",
                    "output_path": str(allowed / "escape" / "out.mp4"),
                    "allow_root": str(allowed),
                },
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("resolves outside configured allow_root", proc.stderr + proc.stdout)

    @unittest.skipUnless(FFMPEG, "ffmpeg required for download-worker integration test")
    def test_short_overview_format_is_not_compared_to_mp4_container(self):
        """Existing artifact path deterministically exercises the worker's post-download handoff."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            allowed = root / "allowed"
            allowed.mkdir()
            output = allowed / "short-overview.mp4"
            subprocess.run(
                [
                    FFMPEG,
                    "-y",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=s=32x32:d=0.5",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(output),
                ],
                check=True,
            )
            proc = self.run_worker(
                root,
                {
                    "request_id": "r1",
                    "story_id": "s1",
                    "notebook_url": "https://notebook.google.com/notebook/example",
                    "artifact_title": "Short overview",
                    "output_path": str(output),
                    "receipt_path": str(allowed / "receipt.json"),
                    "allow_root": str(allowed),
                    "expected_format": "Short",
                    "expected_container": "mp4",
                },
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            receipt = json.loads((allowed / "receipt.json").read_text())
            self.assertIn("mp4", receipt["artifact"]["container"])

    def test_python_contract_requires_absolute_contained_paths_and_receipt_output_path(self):
        from workflow_automation.contracts import (
            validate_notebook_receipt,
            validate_notebook_request,
        )

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            request = {
                "request_id": "r1",
                "story_id": "s1",
                "notebook_url": "https://notebook.google.com/notebook/example",
                "artifact_title": "Short overview",
                "allow_root": str(root),
                "output_path": "relative.mp4",
            }
            with self.assertRaisesRegex(ValueError, "absolute"):
                validate_notebook_request(request)
            request["output_path"] = str(root.parent / "outside.mp4")
            with self.assertRaisesRegex(ValueError, "within allow_root"):
                validate_notebook_request(request)
            receipt = {
                "request_id": "r1",
                "story_id": "s1",
                "status": "done",
                "output_path": "relative.mp4",
                "artifact": {
                    "size_bytes": 1,
                    "container": "mp4",
                    "duration_seconds": 1,
                    "dimensions": {"width": 1, "height": 1},
                    "codecs": {"video": "h264", "audio": None},
                    "sha256": "a" * 64,
                },
                "evidence": {"checked": True},
            }
            with self.assertRaisesRegex(ValueError, "output_path"):
                validate_notebook_receipt(receipt)


if __name__ == "__main__":
    unittest.main()
