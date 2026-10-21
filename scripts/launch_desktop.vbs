Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\artem\Documents\projects\automatization"
WshShell.Run "uv run cliphunter desktop", 0, False
