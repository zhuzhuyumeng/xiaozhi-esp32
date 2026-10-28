#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BRIDGE_SCRIPT="$SCRIPT_DIR/codex_status_bridge.py"

if [ "$#" -ge 1 ]; then
    DEVICE_HOST=$1
else
    printf '请输入开发板 IP 地址（例如 192.168.1.50）: '
    IFS= read -r DEVICE_HOST
fi

case "$DEVICE_HOST" in
    ''|*[!0-9.]*|*.*.*.*.*) echo "无效的 IPv4 地址: $DEVICE_HOST" >&2; exit 1 ;;
esac

PYTHON=${PYTHON:-python3}
if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "未找到 Python 3，请先安装 Python 3。" >&2
    exit 1
fi

exec "$PYTHON" - "$BRIDGE_SCRIPT" "$DEVICE_HOST" <<'PY'
import json
from pathlib import Path
import ipaddress
import shlex
import shutil
import subprocess
import sys
import tempfile

bridge = Path(sys.argv[1]).resolve()
device_host = sys.argv[2]
if not bridge.is_file():
    raise SystemExit(f"未找到桥接脚本: {bridge}")
try:
    ipaddress.IPv4Address(device_host)
except ipaddress.AddressValueError:
    raise SystemExit(f"无效的 IPv4 地址: {device_host}")
hooks_path = Path.home() / ".codex" / "hooks.json"
hooks_path.parent.mkdir(parents=True, exist_ok=True)

if hooks_path.exists():
    try:
        config = json.loads(hooks_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"无法解析 {hooks_path}: {exc}")
else:
    config = {}
if not isinstance(config, dict):
    raise SystemExit(f"{hooks_path} 必须是 JSON 对象")

hooks = config.setdefault("hooks", {})
if not isinstance(hooks, dict):
    raise SystemExit(f"{hooks_path} 的 hooks 必须是 JSON 对象")

command = " ".join([
    shlex.quote(sys.executable),
    shlex.quote(str(bridge)),
    "--host",
    shlex.quote(device_host),
    "hook",
])
events = ("SessionStart", "UserPromptSubmit", "Stop", "Interrupt", "SessionEnd")
updated = 0

for event in events:
    entries = hooks.setdefault(event, [])
    if not isinstance(entries, list):
        entries = [entries]

    cleaned = []
    for entry in entries:
        nested = entry.get("hooks", []) if isinstance(entry, dict) else []
        commands = [item.get("command", "") for item in nested if isinstance(item, dict)]
        if any("codex_status_bridge.py" in value for value in commands):
            continue
        cleaned.append(entry)

    cleaned.append({
        "hooks": [{
            "type": "command",
            "command": command,
            "timeout": 3,
        }]
    })
    updated += 1
    hooks[event] = cleaned

payload = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=hooks_path.parent, delete=False) as handle:
    handle.write(payload)
    temporary = Path(handle.name)
shutil.copymode(hooks_path, temporary) if hooks_path.exists() else None
temporary.replace(hooks_path)

subprocess.run([sys.executable, str(bridge), "--host", device_host, "send", "idle"], check=True)
print(f"Codex Companion 已安装。注册/更新 {updated} 个状态 Hook。")
print(f"设备: {device_host}")
print(f"Hooks: {hooks_path}")
print(f"Bridge: {bridge}")
PY
