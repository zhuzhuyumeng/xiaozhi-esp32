Option Explicit

Dim shell, pythonExe, bridgeScript, command
Set shell = CreateObject("WScript.Shell")

pythonExe = "C:\Users\zym\AppData\Local\Programs\Python\Python312\pythonw.exe"
bridgeScript = "F:\codex\esp32\tools\codex_status_bridge.py"
command = Chr(34) & pythonExe & Chr(34) & " " & Chr(34) & bridgeScript & Chr(34) & " voice-listen --device-host 192.168.100.56 --model-provider custom --model gpt-5.6-sol"

shell.Run command, 0, False
