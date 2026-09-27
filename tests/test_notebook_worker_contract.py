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

    def test_python_contract_requires_non_empty_string_types(self):
        from workflow_automation.contracts import validate_notebook_request

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            base_request = {
                "request_id": "r1",
                "story_id": "s1",
                "notebook_url": "https://notebook.google.com/notebook/example",
                "artifact_title": "Short overview",
                "allow_root": str(root),
                "output_path": str(root / "out.mp4"),
            }
            # Test non-string or empty-string values for each required field
            bad_cases = [
                ("request_id", 123),
                ("request_id", ""),
                ("request_id", "   "),
                ("story_id", {}),
                ("story_id", ""),
                ("notebook_url", True),
                ("notebook_url", ""),
                ("artifact_title", ["unexpected"]),
                ("artifact_title", ""),
                ("output_path", 456),
                ("output_path", ""),
                ("allow_root", False),
                ("allow_root", ""),
            ]
            for key, bad_val in bad_cases:
                with self.subTest(key=key, bad_val=bad_val):
                    invalid = dict(base_request, **{key: bad_val})
                    with self.assertRaisesRegex(ValueError, f"{key} must be a non-empty string"):
                        validate_notebook_request(invalid)
            for duration in (True, False, 0, -1):
                with self.subTest(expected_duration_seconds=duration):
                    invalid = dict(base_request, expected_duration_seconds=duration)
                    with self.assertRaisesRegex(ValueError, "expected_duration_seconds"):
                        validate_notebook_request(invalid)

    def test_receipt_containment_validation(self):
        from workflow_automation.contracts import (
            validate_notebook_receipt,
            validate_notebook_receipt_containment,
        )

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            allowed = root / "allowed"
            allowed.mkdir()
            outside = root / "outside"
            outside.mkdir()

            receipt = {
                "request_id": "r1",
                "story_id": "s1",
                "status": "done",
                "output_path": str(outside / "out.mp4"),
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
            # Without allow_root specified, basic validation passes for absolute paths
            validate_notebook_receipt(receipt)

            # When allow_root is explicitly passed, receipt fails if output_path is outside
            with self.assertRaisesRegex(ValueError, "within allow_root"):
                validate_notebook_receipt(receipt, allow_root=allowed)

            with self.assertRaisesRegex(ValueError, "within allow_root"):
                validate_notebook_receipt_containment(receipt, allow_root=allowed)

            # If receipt itself carries allow_root, validate_notebook_receipt enforces it
    def test_worker_fails_closed_on_invalid_or_tampered_existing_artifact(self):
        """Worker fails closed without deleting file or attempting browser download on tampered/invalid file."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            allowed = root / "allowed"
            allowed.mkdir()
            output = allowed / "tampered.mp4"
            # Write invalid/too-small non-media content
            output.write_text("invalid media content")

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
                    "cdp_url": "http://127.0.0.1:1",  # CDP endpoint intentionally invalid/unreachable
                },
            )
            # Must fail with an error from probe/ffprobe
            self.assertNotEqual(proc.returncode, 0)
            # The tampered file must NOT have been deleted
            self.assertTrue(output.exists(), "Existing invalid/tampered file must not be deleted")
            # Must NOT have attempted CDP download (which would fail with CDP/Chrome error)
            self.assertNotIn("authenticated Chrome context not found", proc.stderr + proc.stdout)
            self.assertFalse((allowed / "receipt.json").exists())

    def test_worker_rejects_non_string_types(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            allowed = root / "allowed"
            allowed.mkdir()
            for key, val in [("request_id", 123), ("story_id", {}), ("artifact_title", ["test"])]:
                proc = self.run_worker(
                    root,
                    {
                        "request_id": "r1",
                        "story_id": "s1",
                        "notebook_url": "https://notebook.google.com/notebook/example",
                        "artifact_title": "Short overview",
                        "output_path": str(allowed / "out.mp4"),
                        "allow_root": str(allowed),
                        key: val,
                    },
                )
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn("must be a non-empty string", proc.stderr + proc.stdout)

    def test_ingest_worker_receipt_and_ingest_download_receipt_require_allow_root(self):
        from workflow_automation.contracts import (
            validate_notebook_receipt,
            validate_notebook_receipt_containment,
        )
        from workflow_automation.notebook import (
            ingest_download_receipt,
            ingest_worker_receipt,
        )

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            allowed = root / "allowed"
            allowed.mkdir()
            outside = root / "outside"
            outside.mkdir()

            receipt_data = {
                "request_id": "r1",
                "story_id": "s1",
                "status": "done",
                "output_path": str(outside / "out.mp4"),
                "artifact": {
                    "size_bytes": 100,
                    "container": "mp4",
                    "duration_seconds": 10,
                    "dimensions": {"width": 1080, "height": 1920},
                    "codecs": {"video": "h264", "audio": "aac"},
                    "sha256": "0" * 64,
                },
                "evidence": {"local_worker": True},
            }
            receipt_path = root / "receipt.json"
            receipt_path.write_text(json.dumps(receipt_data))

            # Without allow_root argument, calls fail (TypeError: missing required argument)
            with self.assertRaises(TypeError):
                ingest_download_receipt(receipt_path)  # type: ignore[call-arg]
            with self.assertRaises(TypeError):
                ingest_worker_receipt(receipt_path)  # type: ignore[call-arg]

            # When allow_root does not contain output_path, validation fails
            with self.assertRaisesRegex(ValueError, "within allow_root"):
                ingest_download_receipt(receipt_path, allow_root=allowed)
            with self.assertRaisesRegex(ValueError, "within allow_root"):
                ingest_worker_receipt(receipt_path, allow_root=allowed)

            # Contained output_path succeeds
            receipt_data["output_path"] = str(allowed / "out.mp4")
            receipt_path.write_text(json.dumps(receipt_data))
            art = ingest_download_receipt(receipt_path, allow_root=allowed)
            self.assertEqual(art["request_id"], "r1")
            art2 = ingest_worker_receipt(receipt_path, allow_root=allowed)
            self.assertEqual(art2["request_id"], "r1")

            # Valid contained path passes
            contained_receipt = dict(receipt_data, output_path=str(allowed / "out.mp4"), allow_root=str(allowed))
            validate_notebook_receipt(contained_receipt, allow_root=allowed)
            resolved = validate_notebook_receipt_containment(contained_receipt, allowed)
            self.assertEqual(resolved, (allowed / "out.mp4").resolve())

    def test_python_contract_rejects_prefix_confusion_notebook_urls(self):
        from workflow_automation.contracts import validate_notebook_request

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            request = {
                "request_id": "r1",
                "story_id": "s1",
                "notebook_url": "https://notebook.google.com/notebook/example",
                "artifact_title": "Short overview",
                "allow_root": str(root),
                "output_path": str(root / "out.mp4"),
            }
            for malicious_url in (
                "https://notebook.google.com.evil.example/notebook/example",
                "https://notebook.google.com@evil.example/notebook/example",
                "https://notebook.google.com/notebookish/example",
                "http://notebook.google.com/notebook/example",
                "https://notebook.google.com/notebook/example?redirect=evil",
                "https://notebook.google.com:443/notebook/example",
            ):
                with self.subTest(url=malicious_url):
                    invalid = dict(request, notebook_url=malicious_url)
                    with self.assertRaisesRegex(ValueError, "NotebookLM URL"):
                        validate_notebook_request(invalid)


if __name__ == "__main__":
    unittest.main()
