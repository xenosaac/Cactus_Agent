# Cactus for macOS

Native macOS voice assistant POC with a Siri-style overlay, right-edge task tab, wake word handling for `cactus`, live transcription, task parsing, and spoken confirmation.

## What ships in this repo

- Native macOS app scaffold generated with XcodeGen
- SwiftUI overlay bubble and transparent task tab
- Voice assistant state machine for wake, capture, parse, confirm, and collapse
- Live transcription pipeline powered by Cactus streaming transcription
- Menu bar control surface for restart and quit
- Unit tests for task parsing behavior

## Cactus setup

This app now uses the installed Cactus runtime directly for live transcription. It expects:

```bash
brew install cactus-compute/cactus/cactus
cactus download nvidia/parakeet-tdt-0.6b-v3
```

## Generate the Xcode project

```bash
xcodegen generate
open Cactus.xcodeproj
```

## Run

1. Build and run the `Cactus` target in Xcode.
2. Grant microphone and speech recognition permissions.
3. Say `cactus`.
4. Speak a task.
5. Watch the bubble collapse into the right-side task tab while the app reads the task back.
