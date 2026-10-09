import tempfile
import unittest
from pathlib import Path

from app.services.computer_provider import ComputerProviderError
from app.services.docker_computer_provider import DockerComputerProvider


class FakeDockerCommand:
    def __init__(self):
        self.calls = []

    def __call__(self, args, timeout):
        self.calls.append((tuple(args), timeout))
        if args[0] == "run":
            return "container-test"
        if args[0] == "port":
            return "127.0.0.1:45678"
        if args[0] == "rm":
            # No stale container exists in these tests, like a real daemon says.
            raise ComputerProviderError("Error response from daemon: No such container")
        return ""

    def run_args(self):
        return next(call[0] for call in self.calls if call[0][0] == "run")


class DockerComputerProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.docker = FakeDockerCommand()
        self.provider = DockerComputerProvider(
            image="open-grok-bot-computer:test",
            workspace_root=self.root / "computers",
            seccomp_profile=self.root / "missing-seccomp.json",
            start_timeout=0.5,
            docker_command=self.docker,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    async def test_start_builds_a_restricted_per_bot_container(self):
        status = self.provider.get_or_create("bot-test")

        async def ready(_record):
            return {"status": "healthy", "width": 1280, "height": 720}

        self.provider._wait_until_ready = ready
        started = await self.provider.start(status.computer_id)

        self.assertEqual(started.state, "running")
        self.assertEqual(started.provider, "docker-playwright")
        self.assertTrue((self.root / "computers" / status.computer_id).is_dir())
        run_args = self.docker.run_args()
        self.assertEqual(run_args[0], "run")
        self.assertIn("--read-only", run_args)
        self.assertIn("--cap-drop", run_args)
        self.assertIn("ALL", run_args)
        self.assertIn("--security-opt", run_args)
        self.assertIn("no-new-privileges:true", run_args)
        self.assertIn("--pids-limit", run_args)
        self.assertIn("/home/pwuser:rw,nosuid,size=1g,uid=1001,gid=1001", run_args)
        self.assertIn("--publish", run_args)
        self.assertIn("127.0.0.1::3000", run_args)
        self.assertNotIn("COMPUTER_TOKEN", started.to_dict())
        self.assertNotIn("token", started.to_dict())

    async def test_browser_terminal_files_input_and_screenshot_use_scoped_runtime(self):
        status = self.provider.get_or_create("bot-test")

        async def ready(_record):
            return {"status": "healthy"}

        async def request(_record, method, route, payload=None):
            if route == "/health":
                return {"status": "healthy"}
            if route == "/navigate":
                return {"url": payload["url"], "title": "Example"}
            if route == "/terminal":
                return {"exit_code": 0, "stdout": "ok", "stderr": ""}
            if route == "/files":
                return {"path": payload["path"], "entries": []}
            if route == "/input":
                return {"accepted": True, "type": payload["event"]["type"]}
            if route == "/screenshot":
                return {
                    "frame_id": "frame-real-1",
                    "format": "jpeg",
                    "width": 1280,
                    "height": 720,
                    "data": "base64-jpeg",
                }
            raise AssertionError(route)

        self.provider._wait_until_ready = ready
        self.provider._request = request
        await self.provider.start(status.computer_id)

        navigated = await self.provider.browser_navigate(status.computer_id, "https://example.test")
        self.assertEqual(navigated["title"], "Example")
        terminal = await self.provider.terminal_execute(status.computer_id, "printf safe")
        self.assertEqual(terminal["stdout"], "ok")
        files = await self.provider.files_list(status.computer_id, "/workspace")
        self.assertEqual(files["entries"], [])
        input_result = await self.provider.send_input(
            status.computer_id,
            {"type": "click", "x": 4, "y": 5},
        )
        self.assertTrue(input_result["accepted"])
        screen = await self.provider.screenshot(status.computer_id)
        self.assertTrue(screen["available"])
        self.assertEqual(screen["frame_id"], "frame-real-1")
        self.assertEqual(self.provider.describe("bot-test").frame_id, "frame-real-1")

    async def test_a_stale_container_with_the_same_name_is_removed_before_launch(self):
        status = self.provider.get_or_create("bot-test")

        async def ready(_record):
            return {"status": "healthy"}

        self.provider._wait_until_ready = ready
        await self.provider.start(status.computer_id)

        first = self.docker.calls[0][0]
        self.assertEqual(first[:2], ("rm", "-f"))
        self.assertEqual(first[2], f"open-grok-computer-{status.computer_id[-70:]}")
        self.assertEqual(self.docker.calls[1][0][0], "run")

    async def test_network_mode_joins_a_docker_network_instead_of_publishing(self):
        provider = DockerComputerProvider(
            image="open-grok-bot-computer:test",
            workspace_root=self.root / "computers",
            seccomp_profile=self.root / "missing-seccomp.json",
            start_timeout=0.5,
            network="open-grok-bot-computers",
            docker_command=self.docker,
        )
        status = provider.get_or_create("bot-net")

        async def ready(_record):
            return {"status": "healthy"}

        provider._wait_until_ready = ready
        await provider.start(status.computer_id)

        run_args = self.docker.run_args()
        self.assertIn("--network", run_args)
        self.assertIn("open-grok-bot-computers", run_args)
        self.assertNotIn("--publish", run_args)
        self.assertNotIn("port", [call[0][0] for call in self.docker.calls])

        record = provider._runtimes[status.computer_id]
        self.assertEqual(record.host, f"open-grok-computer-{status.computer_id[-70:]}")
        self.assertEqual(record.port, 3000)
        self.assertEqual(
            provider._runtime_url(record, "/health"),
            f"http://open-grok-computer-{status.computer_id[-70:]}:3000/health",
        )

        await provider.stop(status.computer_id)
        self.assertIsNone(record.host)
        self.assertIsNone(record.port)

    async def test_publish_mode_connects_to_loopback_port(self):
        status = self.provider.get_or_create("bot-test")

        async def ready(_record):
            return {"status": "healthy"}

        self.provider._wait_until_ready = ready
        await self.provider.start(status.computer_id)
        record = self.provider._runtimes[status.computer_id]
        self.assertEqual(record.host, "127.0.0.1")
        self.assertEqual(record.port, 45678)
        self.assertEqual(
            self.provider._runtime_url(record, "/screenshot"), "http://127.0.0.1:45678/screenshot"
        )

    async def test_volume_workspace_mode_mounts_a_named_volume(self):
        provider = DockerComputerProvider(
            image="open-grok-bot-computer:test",
            workspace_root=self.root / "computers",
            seccomp_profile=self.root / "missing-seccomp.json",
            start_timeout=0.5,
            workspace_mode="volume",
            docker_command=self.docker,
        )
        status = provider.get_or_create("bot-vol")

        async def ready(_record):
            return {"status": "healthy"}

        provider._wait_until_ready = ready
        await provider.start(status.computer_id)

        run_args = self.docker.run_args()
        mount = run_args[run_args.index("--mount") + 1]
        self.assertEqual(
            mount, f"type=volume,src=open-grok-computer-ws-{status.computer_id[-60:]},dst=/workspace"
        )

    async def test_bind_mode_translates_to_the_host_path_when_configured(self):
        provider = DockerComputerProvider(
            image="open-grok-bot-computer:test",
            workspace_root=self.root / "computers",
            seccomp_profile=self.root / "missing-seccomp.json",
            start_timeout=0.5,
            host_workspace_root="/srv/open-grok-bot/computers",
            docker_command=self.docker,
        )
        status = provider.get_or_create("bot-bind")

        async def ready(_record):
            return {"status": "healthy"}

        provider._wait_until_ready = ready
        await provider.start(status.computer_id)

        run_args = self.docker.run_args()
        mount = run_args[run_args.index("--mount") + 1]
        self.assertTrue(mount.startswith("type=bind,src="))
        self.assertIn(status.computer_id, mount)
        self.assertIn("open-grok-bot", mount.replace("\\", "/"))
        self.assertNotIn(str(self.root), mount)
        self.assertTrue(mount.endswith(",dst=/workspace"))

    async def test_user_control_blocks_bot_input_and_navigation_until_handed_back(self):
        status = self.provider.get_or_create("bot-test")

        async def ready(_record):
            return {"status": "healthy"}

        async def request(_record, method, route, payload=None):
            return {"status": "healthy", "accepted": True, "url": "https://example.test", "title": "x"}

        self.provider._wait_until_ready = ready
        self.provider._request = request
        await self.provider.start(status.computer_id)

        self.assertEqual(self.provider.vnc_target(status.computer_id)["port"], 45678)
        self.assertIn("vnc", status.capabilities)

        takeover = await self.provider.request_takeover(status.computer_id, "Please log in to the site.")
        self.assertTrue(takeover["requested"])
        self.assertEqual(status.takeover_request["reason"], "Please log in to the site.")

        await self.provider.set_control(status.computer_id, "user")
        self.assertEqual(status.controlled_by, "user")
        with self.assertRaisesRegex(ComputerProviderError, "user currently has control"):
            await self.provider.send_input(status.computer_id, {"type": "click", "x": 1, "y": 1})
        with self.assertRaisesRegex(ComputerProviderError, "user currently has control"):
            await self.provider.browser_navigate(status.computer_id, "https://example.test")
        # Looking is still allowed while the user drives.
        self.assertTrue((await self.provider.screenshot(status.computer_id))["available"])

        await self.provider.set_control(status.computer_id, "bot")
        self.assertEqual(status.controlled_by, "bot")
        self.assertIsNone(status.takeover_request)
        self.assertTrue((await self.provider.send_input(status.computer_id, {"type": "click", "x": 1, "y": 1}))["accepted"])

        with self.assertRaisesRegex(ComputerProviderError, "'bot' or 'user'"):
            await self.provider.set_control(status.computer_id, "me")
        await self.provider.stop(status.computer_id)
        self.assertIsNone(self.provider.vnc_target(status.computer_id))

    async def test_configured_wallpaper_is_mounted_read_only_over_the_default(self):
        provider = DockerComputerProvider(
            image="open-grok-bot-computer:test",
            workspace_root=self.root / "computers",
            seccomp_profile=self.root / "missing-seccomp.json",
            start_timeout=0.5,
            wallpaper="/srv/wallpaper.jpg",
            docker_command=self.docker,
        )
        status = provider.get_or_create("bot-wall")

        async def ready(_record):
            return {"status": "healthy"}

        provider._wait_until_ready = ready
        await provider.start(status.computer_id)
        run_args = self.docker.run_args()
        self.assertIn("type=bind,src=/srv/wallpaper.jpg,dst=/opt/open-grok-computer/wallpaper,readonly", run_args)

        # Without a wallpaper nothing extra is mounted.
        plain = [a for a in self.docker.run_args() if "wallpaper" in a]
        self.assertEqual(len(plain), 1)

    def test_unknown_workspace_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            DockerComputerProvider(workspace_root=self.root / "computers", workspace_mode="nfs")

    async def test_docker_failure_is_reported_and_marks_runtime_unhealthy(self):
        def failing_docker(_args, _timeout):
            raise ComputerProviderError("Docker daemon is unavailable.")

        provider = DockerComputerProvider(
            workspace_root=self.root / "computers",
            docker_command=failing_docker,
        )
        status = provider.get_or_create("bot-test")
        with self.assertRaisesRegex(ComputerProviderError, "daemon is unavailable"):
            await provider.start(status.computer_id)
        self.assertEqual(provider.describe("bot-test").state, "error")
        self.assertEqual(provider.describe("bot-test").health, "unhealthy")


if __name__ == "__main__":
    unittest.main()
