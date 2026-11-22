"""Resolve local executables without tying the repository to one computer."""

import os
import shutil
from pathlib import Path


def resolve_godot() -> Path:
    configured = os.environ.get("GODOT_EXE", "").strip()
    if configured:
        executable = Path(configured).expanduser()
        if executable.is_file():
            return executable
        raise FileNotFoundError(f"GODOT_EXE 指向的文件不存在：{executable}")

    for name in ("godot", "godot4"):
        found = shutil.which(name)
        if found:
            return Path(found)
    raise FileNotFoundError("找不到 Godot。请设置 GODOT_EXE 为可执行文件的完整路径。")
