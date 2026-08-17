from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def build_command(tool_name: str, arguments: dict[str, Any]) -> list[str]:
    script = Path(__file__).with_name("operations.py")
    command = [sys.executable, str(script), tool_name]
    if "input_paths" in arguments:
        command += ["--input-paths-json", json.dumps(arguments["input_paths"], separators=(",", ":"))]
    if "input_path" in arguments:
        command += ["--input-path", str(arguments["input_path"])]
    if "operation" in arguments:
        command += ["--operation", str(arguments["operation"])]
    return command
