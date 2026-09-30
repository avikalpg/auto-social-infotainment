import hashlib
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
FFPROBE = shutil.which("ffprobe")


def probe_artifact(path: Path) -> dict[str, object]:
    raw = subprocess.run(
        [
            FFPROBE,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    data = json.loads(raw.stdout)
    video = next(stream for stream in data["streams"] if stream["codec_type"] == "video")
    audio = next((stream for stream in data["streams"] if stream["codec_type"] == "audio"), None)
    return {
        "size_bytes": path.stat().st_size,
        "container": data["format"]["format_name"],
        "duration_seconds": float(data["format"].get("duration") or video["duration"]),
        "dimensions": {"width": video["width"], "height": video["height"]},
        "codecs": {"video": video["codec_name"], "audio": audio["codec_name"] if audio else None},
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


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
                    "receipt_path": str(root / "allowed" / "receipt.json"),
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
                    "receipt_path": str(allowed / "receipt.json"),
                    "allow_root": str(allowed),
                },
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("parent must not contain symlinks", proc.stderr + proc.stdout)

    def test_worker_rejects_lexical_escape_before_creating_parent_directories(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            allowed = root / "allowed"
            outside_parent = root / "outside" / "created-too-early"
            proc = self.run_worker(
                root,
                {
                    "request_id": "r1",
                    "story_id": "s1",
                    "notebook_url": "https://notebook.google.com/notebook/example",
                    "artifact_title": "Generic Artifact",
                    "output_path": str(outside_parent / "out.mp4"),
                    "receipt_path": str(allowed / "receipt.json"),
                    "allow_root": str(allowed),
                },
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertFalse(outside_parent.exists())

    @unittest.skipUnless(
        FFMPEG and FFPROBE, "ffmpeg/ffprobe required for download-worker integration test"
    )
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
            request = {
                "request_id": "r1",
                "story_id": "s1",
                "request_token": "generation-r1",
                "notebook_url": "https://notebook.google.com/notebook/example",
                "artifact_title": "Short overview",
                "output_path": str(output),
                "receipt_path": str(allowed / "receipt.json"),
                "allow_root": str(allowed),
                "expected_format": "Short",
                "expected_container": "mp4",
            }
            prior_receipt = {
                "schema_version": 1,
                "request_id": request["request_id"],
                "story_id": request["story_id"],
                "request_token": request["request_token"],
                "status": "done",
                "notebook_url": request["notebook_url"],
                "video_format": "Short",
                "output_path": request["output_path"],
                "allow_root": request["allow_root"],
                "timestamp": "2026-09-27T00:00:00Z",
                "artifact": probe_artifact(output),
                "evidence": {"artifact_title": request["artifact_title"], "local_worker": True},
            }
            (allowed / "receipt.json").write_text(json.dumps(prior_receipt))
            proc = self.run_worker(root, request)
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
                "receipt_path": str(root / "receipt.json"),
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
                "timestamp": "2026-09-27T00:00:00Z",
                "output_path": "relative.mp4",
                "artifact": {
                    "size_bytes": 1,
                    "container": "mp4",
                    "duration_seconds": 1,
                    "dimensions": {"width": 1, "height": 1},
                    "codecs": {"video": "h264", "audio": None},
                    "sha256": "a" * 64,
                },
                "evidence": {"local_worker": True},
            }
            with self.assertRaisesRegex(ValueError, "output_path"):
                validate_notebook_receipt(receipt, allow_root=root)

    def test_receipt_contract_validates_identity_timestamp_and_notebook_url(self):
        from workflow_automation.contracts import validate_notebook_receipt

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            receipt = {
                "request_id": "r1",
                "story_id": "s1",
                "status": "done",
                "timestamp": "2026-09-27T00:00:00Z",
                "notebook_url": "https://notebook.google.com/notebook/example",
                "output_path": str(root / "video.mp4"),
                "artifact": {
                    "size_bytes": 1,
                    "container": "mp4",
                    "duration_seconds": 1,
                    "dimensions": {"width": 1, "height": 1},
                    "codecs": {"video": "h264", "audio": None},
                    "sha256": "a" * 64,
                },
                "evidence": {"local_worker": True},
            }
            validate_notebook_receipt(receipt, allow_root=root)
            invalid_evidence = dict(receipt["evidence"], local_worker="yes")
            with self.assertRaisesRegex(ValueError, "local_worker must be boolean"):
                validate_notebook_receipt(
                    dict(receipt, evidence=invalid_evidence), allow_root=root
                )
            for key, value in (
                ("request_id", 1),
                ("story_id", " "),
                ("timestamp", {}),
                ("output_path", []),
            ):
                with self.subTest(key=key), self.assertRaisesRegex(ValueError, key):
                    validate_notebook_receipt(dict(receipt, **{key: value}), allow_root=root)
            with self.assertRaisesRegex(ValueError, "NotebookLM URL"):
                validate_notebook_receipt(
                    dict(receipt, notebook_url="https://notebook.google.com.evil/notebook/example"),
                    allow_root=root,
                )
            for key, value in (("size_bytes", True), ("duration_seconds", False)):
                with self.subTest(key=key):
                    artifact = dict(receipt["artifact"], **{key: value})
                    with self.assertRaisesRegex(ValueError, key):
                        validate_notebook_receipt(dict(receipt, artifact=artifact), allow_root=root)
            for key in ("width", "height"):
                with self.subTest(dimension=key):
                    dimensions = dict(receipt["artifact"]["dimensions"], **{key: True})
                    artifact = dict(receipt["artifact"], dimensions=dimensions)
                    with self.assertRaisesRegex(ValueError, "dimensions"):
                        validate_notebook_receipt(dict(receipt, artifact=artifact), allow_root=root)

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
                "receipt_path": str(root / "receipt.json"),
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
                ("receipt_path", None),
                ("receipt_path", ""),
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
            for invalid_format in ("Long", "Explainer", ""):
                with self.subTest(expected_format=invalid_format):
                    invalid = dict(base_request, expected_format=invalid_format)
                    with self.assertRaisesRegex(ValueError, "expected_format must be Short"):
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
                "timestamp": "2026-09-27T00:00:00Z",
                "output_path": str(outside / "out.mp4"),
                "artifact": {
                    "size_bytes": 1,
                    "container": "mp4",
                    "duration_seconds": 1,
                    "dimensions": {"width": 1, "height": 1},
                    "codecs": {"video": "h264", "audio": None},
                    "sha256": "a" * 64,
                },
                "evidence": {"local_worker": True},
            }
            # The canonical validator cannot be called without a trusted containment root.
            with self.assertRaises(TypeError):
                validate_notebook_receipt(receipt)  # type: ignore[call-arg]

            # A trusted allow_root rejects receipt paths outside that boundary.
            with self.assertRaisesRegex(ValueError, "within allow_root"):
                validate_notebook_receipt(receipt, allow_root=allowed)

            with self.assertRaisesRegex(ValueError, "within allow_root"):
                validate_notebook_receipt_containment(receipt, allow_root=allowed)

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
            parse_download_receipt,
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
                "timestamp": "2026-09-27T00:00:00Z",
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

            # Structural parsing succeeds for a contained path, but trusted ingestion
            # refuses to accept receipt claims when the artifact does not exist.
            receipt_data["output_path"] = str(allowed / "out.mp4")
            receipt_path.write_text(json.dumps(receipt_data))
            art = parse_download_receipt(receipt_path, allow_root=allowed)
            self.assertEqual(art["request_id"], "r1")
            with self.assertRaisesRegex(ValueError, "does not exist"):
                ingest_download_receipt(receipt_path, allow_root=allowed)
            with self.assertRaisesRegex(ValueError, "does not exist"):
                ingest_worker_receipt(receipt_path, allow_root=allowed)

            # Valid contained path passes
            contained_receipt = dict(
                receipt_data, output_path=str(allowed / "out.mp4"), allow_root=str(allowed)
            )
            validate_notebook_receipt(contained_receipt, allow_root=allowed)
            resolved = validate_notebook_receipt_containment(contained_receipt, allowed)
            self.assertEqual(resolved, (allowed / "out.mp4").resolve())

            contradictory_receipt = dict(contained_receipt, allow_root=str(outside))
            with self.assertRaisesRegex(ValueError, "does not match trusted root"):
                validate_notebook_receipt(contradictory_receipt, allow_root=allowed)

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
                "receipt_path": str(root / "receipt.json"),
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

    def test_schema_version_validation(self):
        from workflow_automation.contracts import (
            validate_notebook_generation_receipt,
            validate_notebook_generation_request,
            validate_notebook_receipt,
            validate_notebook_request,
        )

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            req = {
                "schema_version": 2,
                "request_id": "r1",
                "story_id": "s1",
                "notebook_url": "https://notebook.google.com/notebook/example",
                "artifact_title": "Short overview",
                "allow_root": str(root),
                "output_path": str(root / "out.mp4"),
                "receipt_path": str(root / "receipt.json"),
            }
            for schema_version in (2, True, "1", 1.0):
                with (
                    self.subTest(download_schema_version=schema_version),
                    self.assertRaisesRegex(ValueError, "schema_version must be 1"),
                ):
                    validate_notebook_request(dict(req, schema_version=schema_version))

            gen_req = {
                "schema_version": 99,
                "request_id": "r1",
                "story_id": "s1",
                "request_token": "tok",
                "notebook_url": "https://notebook.google.com/notebook/example",
                "artifact_title": "Short overview",
                "focus_prompt": "Focus",
                "allow_root": str(root),
                "receipt_path": str(root / "receipt.json"),
            }
            for schema_version in (99, True, "1", 1.0):
                with (
                    self.subTest(generation_schema_version=schema_version),
                    self.assertRaisesRegex(ValueError, "schema_version must be 1"),
                ):
                    validate_notebook_generation_request(
                        dict(gen_req, schema_version=schema_version)
                    )

            receipt = {
                "schema_version": 0,
                "request_id": "r1",
                "story_id": "s1",
                "status": "done",
                "timestamp": "2026-09-27T00:00:00Z",
                "output_path": str(root / "out.mp4"),
                "artifact": {
                    "size_bytes": 1,
                    "container": "mp4",
                    "duration_seconds": 1,
                    "dimensions": {"width": 1, "height": 1},
                    "codecs": {"video": "h264", "audio": None},
                    "sha256": "a" * 64,
                },
                "evidence": {"local_worker": True},
            }
            for schema_version in (0, True, "1", 1.0):
                with (
                    self.subTest(download_receipt_schema_version=schema_version),
                    self.assertRaisesRegex(ValueError, "schema_version must be 1"),
                ):
                    validate_notebook_receipt(
                        dict(receipt, schema_version=schema_version), allow_root=root
                    )

            gen_receipt = {
                "schema_version": 5,
                "request_id": "r1",
                "story_id": "s1",
                "request_token": "tok",
                "status": "queued",
                "artifact_title": "Short overview",
                "notebook_url": "https://notebook.google.com/notebook/example",
                "video_format": "Short",
                "timestamp": "2026-09-27T00:00:00Z",
                "evidence": {
                    "generation_only": True,
                    "download_attempted": False,
                    "generation_state": "queued",
                },
            }
            for schema_version in (5, True, "1", 1.0):
                with (
                    self.subTest(generation_receipt_schema_version=schema_version),
                    self.assertRaisesRegex(ValueError, "schema_version must be 1"),
                ):
                    validate_notebook_generation_receipt(
                        dict(gen_receipt, schema_version=schema_version)
                    )

    def test_generation_receipt_rejects_malformed_status_and_evidence_types(self):
        from workflow_automation.contracts import validate_notebook_generation_receipt

        receipt = {
            "schema_version": 1,
            "request_id": "r1",
            "story_id": "s1",
            "request_token": "tok",
            "status": "queued",
            "artifact_title": "Short overview",
            "notebook_url": "https://notebook.google.com/notebook/example",
            "video_format": "Short",
            "timestamp": "2026-09-30T00:00:00Z",
            "evidence": {
                "generation_only": True,
                "download_attempted": False,
                "generation_state": "queued",
            },
        }
        for value in ({}, [], True):
            with self.subTest(status=value), self.assertRaisesRegex(ValueError, "status"):
                validate_notebook_generation_receipt(dict(receipt, status=value))

        malformed_evidence = (
            ("generation_only", []),
            ("download_attempted", {}),
            ("generation_state", []),
            ("page_reused", "yes"),
            ("already_queued", 1),
            ("request_path", {}),
            ("request_sha256", "not-a-sha256"),
            ("allow_root", []),
            ("cdp_url", True),
            ("confirmation", False),
        )
        for key, value in malformed_evidence:
            mutation = dict(receipt)
            mutation["evidence"] = {**receipt["evidence"], key: value}
            with self.subTest(evidence=key), self.assertRaisesRegex(ValueError, key):
                validate_notebook_generation_receipt(mutation)

        for cdp_url in (
            "not-a-url",
            "ws://127.0.0.1:9222",
            "http://user:password@127.0.0.1:9222",
        ):
            mutation = dict(receipt)
            mutation["evidence"] = {**receipt["evidence"], "cdp_url": cdp_url}
            with self.subTest(cdp_url=cdp_url), self.assertRaisesRegex(ValueError, "cdp_url"):
                validate_notebook_generation_receipt(mutation)

    def test_download_request_rejects_unknown_keys_before_processing_paths(self):
        from workflow_automation.contracts import validate_notebook_request

        request = {
            "schema_version": 1,
            "request_id": "r1",
            "story_id": "s1",
            "notebook_url": "https://notebook.google.com/notebook/example",
            "artifact_title": "Short overview",
            "allow_root": [],
            "output_path": {},
            "receipt_path": True,
            "unexpected": "reject first",
        }
        with self.assertRaisesRegex(ValueError, "unsupported keys: unexpected"):
            validate_notebook_request(request)

    def test_generation_error_receipt_is_valid_but_cannot_advance_workflow(self):
        from workflow_automation.contracts import validate_notebook_generation_receipt
        from workflow_automation.notebook import ingest_generation_receipt

        receipt = {
            "schema_version": 1,
            "request_id": "r1",
            "story_id": "s1",
            "request_token": "tok",
            "status": "error",
            "artifact_title": "Short overview",
            "notebook_url": "https://notebook.google.com/notebook/example",
            "video_format": "Short",
            "timestamp": "2026-09-28T20:00:00Z",
            "evidence": {
                "generation_only": True,
                "download_attempted": False,
            },
            "error": {"message": "queue button unavailable"},
        }
        validate_notebook_generation_receipt(receipt)

        with tempfile.TemporaryDirectory() as temporary:
            receipt_path = Path(temporary) / "generation.receipt.json"
            receipt_path.write_text(json.dumps(receipt))
            with self.assertRaisesRegex(RuntimeError, "queue button unavailable"):
                ingest_generation_receipt(
                    receipt_path,
                    request_id="r1",
                    story_id="s1",
                    request_token="tok",
                )

    def test_generation_receipt_is_bound_to_exact_request_file_and_root(self):
        from workflow_automation.notebook import ingest_generation_receipt

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request_path = root / "generation.request.json"
            request_path.write_text(
                json.dumps(
                    {
                        "request_id": "r1",
                        "story_id": "s1",
                        "request_token": "tok",
                        "focus_prompt": "Original focus",
                    }
                )
            )
            receipt = {
                "schema_version": 1,
                "request_id": "r1",
                "story_id": "s1",
                "request_token": "tok",
                "status": "queued",
                "artifact_title": "Short overview",
                "notebook_url": "https://notebook.google.com/notebook/example",
                "video_format": "Short",
                "timestamp": "2026-09-30T16:00:00Z",
                "evidence": {
                    "request_path": str(request_path.resolve()),
                    "request_sha256": hashlib.sha256(request_path.read_bytes()).hexdigest(),
                    "allow_root": str(root.resolve()),
                    "generation_only": True,
                    "download_attempted": False,
                    "generation_state": "queued",
                },
            }
            receipt_path = root / "generation.receipt.json"
            receipt_path.write_text(json.dumps(receipt))

            accepted = ingest_generation_receipt(
                receipt_path,
                request_id="r1",
                story_id="s1",
                request_token="tok",
                request_path=request_path,
                allow_root=root,
            )
            self.assertEqual(accepted["artifact_title"], "Short overview")

            request_path.write_text(
                json.dumps(
                    {
                        "request_id": "r1",
                        "story_id": "s1",
                        "request_token": "tok",
                        "focus_prompt": "Changed focus",
                    }
                )
            )
            with self.assertRaisesRegex(ValueError, "request_sha256"):
                ingest_generation_receipt(
                    receipt_path,
                    request_id="r1",
                    story_id="s1",
                    request_token="tok",
                    request_path=request_path,
                    allow_root=root,
                )

            receipt["evidence"]["request_sha256"] = hashlib.sha256(
                request_path.read_bytes()
            ).hexdigest()
            receipt["evidence"]["request_path"] = str(root / "other.request.json")
            receipt_path.write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, "request_path"):
                ingest_generation_receipt(
                    receipt_path,
                    request_id="r1",
                    story_id="s1",
                    request_token="tok",
                    request_path=request_path,
                    allow_root=root,
                )

            receipt["evidence"]["request_path"] = str(request_path.resolve())
            receipt["evidence"]["allow_root"] = str(root / "different-root")
            receipt_path.write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, "allow_root"):
                ingest_generation_receipt(
                    receipt_path,
                    request_id="r1",
                    story_id="s1",
                    request_token="tok",
                    request_path=request_path,
                    allow_root=root,
                )

    def test_receipt_contracts_reject_unsupported_fields(self):
        from workflow_automation.contracts import (
            validate_notebook_generation_receipt,
            validate_notebook_receipt,
        )

        generation_receipt = {
            "schema_version": 1,
            "request_id": "r1",
            "story_id": "s1",
            "request_token": "tok",
            "status": "queued",
            "artifact_title": "Short overview",
            "notebook_url": "https://notebook.google.com/notebook/example",
            "video_format": "Short",
            "timestamp": "2026-09-28T20:00:00Z",
            "evidence": {
                "generation_only": True,
                "download_attempted": False,
                "generation_state": "queued",
            },
        }
        for mutation in (
            {**generation_receipt, "trusted": True},
            {
                **generation_receipt,
                "evidence": {**generation_receipt["evidence"], "trusted": True},
            },
        ):
            with (
                self.subTest(receipt="generation"),
                self.assertRaisesRegex(ValueError, "unsupported keys"),
            ):
                validate_notebook_generation_receipt(mutation)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            download_receipt = {
                "schema_version": 1,
                "request_id": "r1",
                "story_id": "s1",
                "status": "done",
                "timestamp": "2026-09-28T20:00:00Z",
                "output_path": str(root / "out.mp4"),
                "artifact": {
                    "size_bytes": 1,
                    "container": "mp4",
                    "duration_seconds": 1,
                    "dimensions": {"width": 1, "height": 1},
                    "codecs": {"video": "h264", "audio": None},
                    "sha256": "a" * 64,
                },
                "evidence": {"local_worker": True},
            }
            mutations = (
                {**download_receipt, "trusted": True},
                {
                    **download_receipt,
                    "artifact": {**download_receipt["artifact"], "trusted": True},
                },
                {
                    **download_receipt,
                    "evidence": {**download_receipt["evidence"], "trusted": True},
                },
            )
            for mutation in mutations:
                with (
                    self.subTest(receipt="download"),
                    self.assertRaisesRegex(ValueError, "unsupported keys"),
                ):
                    validate_notebook_receipt(mutation, allow_root=root)

    def test_generation_request_optional_fields_validation(self):
        from workflow_automation.contracts import validate_notebook_generation_request

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            base_gen_req = {
                "schema_version": 1,
                "request_id": "r1",
                "story_id": "s1",
                "request_token": "tok",
                "notebook_url": "https://notebook.google.com/notebook/example",
                "artifact_title": "Short overview",
                "focus_prompt": "Focus",
                "allow_root": str(root),
                "receipt_path": str(root / "receipt.json"),
            }
            # Invalid cdp_url
            for bad_cdp in ("not-a-url", "ftp://localhost:9222", 123, ""):
                with self.subTest(bad_cdp=bad_cdp):
                    invalid = dict(base_gen_req, cdp_url=bad_cdp)
                    with self.assertRaises(ValueError):
                        validate_notebook_generation_request(invalid)

            # Valid cdp_url
            valid_cdp = dict(base_gen_req, cdp_url="http://127.0.0.1:9222")
            validate_notebook_generation_request(valid_cdp)

            # Invalid timestamp
            for bad_ts in (123, "", "   "):
                with self.subTest(bad_ts=bad_ts):
                    invalid = dict(base_gen_req, timestamp=bad_ts)
                    with self.assertRaisesRegex(ValueError, "timestamp"):
                        validate_notebook_generation_request(invalid)

            # Invalid request_token
            for bad_token in (123, "", "   "):
                with self.subTest(bad_token=bad_token):
                    invalid = dict(base_gen_req, request_token=bad_token)
                    with self.assertRaisesRegex(ValueError, "request_token"):
                        validate_notebook_generation_request(invalid)

    def test_legacy_worker_request_reports_missing_notebook_fields(self):
        from workflow_automation.notebook import write_worker_request

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(
                ValueError, "missing required NotebookLM fields: notebook_url, artifact_title"
            ):
                write_worker_request(root / "request.json", {"id": "STR-001"}, root)

    def test_receipt_identity_verification_in_handoff_and_ingestion(self):
        from workflow_automation.handoff import handoff_notebooklm_video
        from workflow_automation.notebook import ingest_download_receipt

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            allowed = root / "allowed"
            allowed.mkdir()
            source = allowed / "video.mp4"
            source.write_bytes(b"dummy video content for receipt test")
            receipt = {
                "schema_version": 1,
                "request_id": "req-1",
                "story_id": "story-1",
                "status": "done",
                "timestamp": "2026-09-27T00:00:00Z",
                "notebook_url": "https://notebook.google.com/notebook/example",
                "output_path": str(source),
                "allow_root": str(allowed),
                "artifact": {
                    "size_bytes": len(source.read_bytes()),
                    "container": "mp4",
                    "duration_seconds": 1.0,
                    "dimensions": {"width": 10, "height": 10},
                    "codecs": {"video": "h264", "audio": None},
                    "sha256": "0" * 64,
                },
                "evidence": {
                    "artifact_title": "Title 1",
                    "local_worker": True,
                },
            }
            receipt_path = root / "receipt.json"
            receipt_path.write_text(json.dumps(receipt))

            # Missing receipt file
            missing_receipt = root / "missing.json"
            with self.assertRaises(FileNotFoundError):
                ingest_download_receipt(missing_receipt, allow_root=allowed)
            with self.assertRaises(FileNotFoundError):
                handoff_notebooklm_video(
                    missing_receipt,
                    allowed_output_root=allowed,
                    handoff_root=root / "handoff",
                    allowed_handoff_root=root,
                )

            # Mismatched request_id
            with self.assertRaisesRegex(ValueError, "request_id mismatch"):
                ingest_download_receipt(
                    receipt_path,
                    allow_root=allowed,
                    expected_request_id="req-2",
                )
            with self.assertRaisesRegex(ValueError, "request_id mismatch"):
                handoff_notebooklm_video(
                    receipt_path,
                    allowed_output_root=allowed,
                    handoff_root=root / "handoff",
                    allowed_handoff_root=root,
                    expected_request_id="req-2",
                )

            # Mismatched story_id
            with self.assertRaisesRegex(ValueError, "story_id mismatch"):
                ingest_download_receipt(
                    receipt_path,
                    allow_root=allowed,
                    expected_story_id="story-2",
                )
            with self.assertRaisesRegex(ValueError, "story_id mismatch"):
                handoff_notebooklm_video(
                    receipt_path,
                    allowed_output_root=allowed,
                    handoff_root=root / "handoff",
                    allowed_handoff_root=root,
                    expected_story_id="story-2",
                )

            # Mismatched artifact_title
            with self.assertRaisesRegex(ValueError, "artifact_title mismatch"):
                handoff_notebooklm_video(
                    receipt_path,
                    allowed_output_root=allowed,
                    handoff_root=root / "handoff",
                    allowed_handoff_root=root,
                    expected_artifact_title="Title 2",
                )

            # Mismatched notebook_url
            with self.assertRaisesRegex(ValueError, "notebook_url mismatch"):
                handoff_notebooklm_video(
                    receipt_path,
                    allowed_output_root=allowed,
                    handoff_root=root / "handoff",
                    allowed_handoff_root=root,
                    expected_notebook_url="https://notebook.google.com/notebook/other",
                )


if __name__ == "__main__":
    unittest.main()
