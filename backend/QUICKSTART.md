# Quick Start

```powershell
.\venv\Scripts\pip.exe install -r requirements.txt
ollama pull LiquidAI/lfm2.5-350m
ollama serve
.\venv\Scripts\python.exe launch.py server
```

Open:

```text
http://127.0.0.1:8000/docs
```

Run smoke tests:

```powershell
.\venv\Scripts\python.exe test_assistant.py
```

Interactive client:

```powershell
.\venv\Scripts\python.exe client.py --interactive
```
