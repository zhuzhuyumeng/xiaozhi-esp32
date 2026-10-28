# Codex Companion 一次部署

最简单的方法是双击：

```text
setup_codex_companion.cmd
```

输入开发板 IP 后即可完成安装。也可以在 PowerShell 中执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\install_codex_companion.ps1 -DeviceHost 192.168.100.56
```

macOS 使用：

```bash
chmod +x ./install_codex_companion.sh
./install_codex_companion.sh 192.168.100.56
```

脚本默认调用 `python3`，也可以通过 `PYTHON=/path/to/python3` 指定解释器。

安装脚本会自动：

- 注册 `SessionStart`、`UserPromptSubmit`、`Stop`、`Interrupt`、`SessionEnd` Hook；
- 保留 `hooks.json` 中其他已有 Hook；
- 重复运行时不重复注册；
- 将当前桥接脚本的绝对路径写入 Hook；迁移包可以放在任意目录，移动后重新运行安装脚本即可更新路径；
- 安装后向设备发送一次 `IDLE`，清除遗留播放状态。

只考虑 GPT 使用状态时，不需要运行语音监听器，也不需要配置 UDP `42155`。

前提：电脑与开发板在同一局域网，且 `-DeviceHost` 是开发板的 IPv4 地址。开发板需要预先烧录支持 Codex Companion 的固件和资源分区。

建议在路由器中为开发板绑定固定 IP，这样安装后无需再次配置。
