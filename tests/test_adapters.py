import sys
import unittest

from workflow_automation.adapters import AdapterNotConfigured, CommandAdapter


class CommandAdapterTests(unittest.TestCase):
    def test_unconfigured_adapter_returns_plan_during_dry_run(self):
        result = CommandAdapter("publisher", None).run(["publish", "--story-id", "STR-001"], True)

        self.assertEqual(result, {"dry_run": True, "command": None})

    def test_configured_adapter_returns_full_command_during_dry_run(self):
        result = CommandAdapter("publisher", ("publisher",)).run(
            ["publish", "--story-id", "STR-001"], True
        )

        self.assertEqual(
            result,
            {
                "dry_run": True,
                "command": ["publisher", "publish", "--story-id", "STR-001"],
            },
        )

    def test_unconfigured_adapter_still_fails_for_production_run(self):
        with self.assertRaises(AdapterNotConfigured):
            CommandAdapter("publisher", None).run([], False)

    def test_dry_run_never_executes_configured_command(self):
        result = CommandAdapter(
            "publisher", (sys.executable, "-c", "raise SystemExit(99)")
        ).run([], True)

        self.assertTrue(result["dry_run"])


if __name__ == "__main__":
    unittest.main()
