<img src="assets/banner.png" alt="Logo" style="border-radius: 30px; width: 60%;">

# Cactus Voice Agent

Python voice computer-use agent for macOS.

The current runtime is push-to-talk only: hold Space, speak a command, release Space. Cactus handles mic/VAD/Whisper, Gemini handles planning and vision, Browser Use handles web tasks, MCP handles Gmail/Calendar, and pyautogui handles native desktop actions.

## Setup

```bash
# Clone Cactus beside the Python app.
git clone https://github.com/cactus-compute/cactus
cd cactus && source ./setup && cd ..

# Build Cactus Python bindings and download local runtime models.
cactus build --python
cactus download google/gemma-4-E4B-it --reconvert
cactus download openai/whisper-small --reconvert
cactus download snakers4/silero-vad --reconvert

# Install Python deps.
pip install -e '.[dev]'
playwright install chromium

# Configure Hybrid mode.
cp .env.example .env
# edit .env and set GEMINI_API_KEY
```

Grant **Microphone**, **Accessibility**, and **Screen Recording** permissions when macOS prompts. Accessibility is needed for click/type actions; Screen Recording is needed for screenshot-based vision.

## Run

```bash
cactus/venv/bin/python -m voice_agent.main
```

Hold Space to talk. Release Space to send the command.

Local-only mode is not the supported path right now; Hybrid mode is the working path.

## Debug

```bash
# STT/VAD diagnostics only. Does not initialize planner/tools.
VA_VOICE_DEBUG=1 cactus/venv/bin/python -m voice_agent.main --voice-debug-only --ui none

# Full test suite, excluding live evals.
cactus/venv/bin/python -m pytest tests/ -q --ignore=tests/evals
```

Voice debug audio is opt-in and writes captured speech segments under `logs/voice_debug`.

## Module Map

```
voice_agent/
├── config.py
├── events.py
├── main.py
├── agent/
│   ├── browser_use_adapter.py
│   ├── cactus_chat_model.py
│   ├── desktop_app_launcher.py
│   ├── gemini_client.py
│   ├── mcp_client.py
│   ├── orchestrator.py
│   ├── screen_reader_tool.py
│   ├── system_prompts.py
│   ├── tool_router.py
│   └── vision_desktop_tool.py
├── ui/native/
└── voice/
```
