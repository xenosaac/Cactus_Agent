"""Open a native macOS app by name. Thin wrapper around `/usr/bin/open -a`.

This is the fast path for "Hey Cactus, open Safari". Before this tool existed,
the planner had to fall through to vision + pyautogui (screenshot the Dock,
click the icon), which is slow and fragile. A Spotlight-equivalent one-liner
is both reliable and legible on stage.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from voice_agent.agent.tool_router import ToolResult

log = logging.getLogger(__name__)


class DesktopAppLauncher:
    name = "open_app"
    description = (
        "Open a native macOS application by name (e.g. 'Safari', 'Keynote', "
        "'Notes', 'Mail'). Returns after launch; does not interact with the "
        "app — use read_visible_screen to inspect visible content or "
        "desktop_native_app to click/type."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "app": {
                "type": "string",
                "description": "Application name as shown in /Applications (e.g. 'Safari').",
            }
        },
        "required": ["app"],
    }

    async def call(self, args: dict[str, Any]) -> ToolResult:
        # Accept common planner variants (`app`, `app_name`, `name`).
        app = (
            args.get("app")
            or args.get("app_name")
            or args.get("name")
            or ""
        )
        app = str(app).strip()
        if not app:
            return ToolResult(ok=False, content="missing 'app' argument", error="bad_args")
        proc = await asyncio.create_subprocess_exec(
            "/usr/bin/open", "-a", app,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        except TimeoutError:
            proc.kill()
            return ToolResult(ok=False, content=f"timed out opening {app}", error="timeout")
        if proc.returncode == 0:
            return ToolResult(ok=True, content=f"opened {app}")
        msg = stderr.decode(errors="replace").strip() or f"open exited {proc.returncode}"
        return ToolResult(ok=False, content=msg, error="open_failed")
