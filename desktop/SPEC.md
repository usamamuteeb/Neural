# Neural Desktop Avatar - Implementation Prompt

## Overview
Build an Electron-based desktop application that displays an always-on-top floating avatar companion that can interact with the user via text and voice, with context awareness of the active window and system state.

## Requirements

### Core Features
1. **Floating Avatar Window**
   - Always-on-top overlay window (200x300px default, resizable)
   - Transparent background, draggable by top handle
   - Minimize/close buttons
   - Persistent position and size across sessions
   - Clickable only on avatar, pass-through elsewhere

2. **User Interaction**
   - Text input box below avatar for typing messages
   - Push-to-talk voice input (spacebar to record, release to send)
   - Optional: Always-listening background voice detection
   - Right-click context menu for quick actions

3. **Avatar Behavior**
   - Mood-based expressions: neutral, happy, excited, playful, proud, surprised, curious, worried, sad, embarrassed, affectionate, annoyed
   - Real-time lip-sync animation to audio responses
   - Breathing and blinking animations
   - Eye movement (optional, follows cursor)
   - Smooth mood transitions

4. **Voice & Audio**
   - Web Speech API or similar for voice-to-text input
   - Text-to-speech for responses (OS native or ElevenLabs API)
   - Stream audio for real-time lip-sync
   - Noise detection and automatic pause during system sounds

5. **Context Awareness**
   - Detect active window title and app
   - Read clipboard on user request or auto-capture
   - Screenshot active window region on demand
   - Desktop file search capability
   - Suggest actions based on active context

6. **Backend Integration**
   - Connect to Claude API for intelligence
   - Stream responses for real-time display
   - Maintain conversation history locally
   - Handle offline mode with message queuing
   - Auto-retry on connection loss

7. **Quick Actions**
   - Screenshot active window
   - Copy active window text to clipboard
   - Web search integration
   - Launch applications
   - Custom user-defined commands
   - Weather, time, reminders

### Architecture

**Technology Stack**
- Electron (v28+) for cross-platform desktop app
- React 18+ for UI
- Web Speech API for voice input
- TTS service (native OS or ElevenLabs)
- SQLite for local chat history
- Node.js + Express (optional local server)
- Claude API for language intelligence

**Project Structure**
```
neural-desktop/
├── electron/
│   ├── main.js
│   ├── preload.js
│   └── ipc-handlers.js
├── src/
│   ├── components/
│   │   ├── Avatar.jsx
│   │   ├── InputBox.jsx
│   │   ├── ContextMenu.jsx
│   │   └── ChatBubble.jsx
│   ├── hooks/
│   │   ├── useVoice.js
│   │   ├── useMood.js
│   │   └── useContext.js
│   ├── utils/
│   │   ├── audioSync.js
│   │   ├── windowContext.js
│   │   └── clipboardManager.js
│   ├── api/
│   │   └── claudeClient.js
│   ├── store/
│   │   └── database.js
│   └── App.jsx
├── assets/
│   ├── avatar/ (images, sprites)
│   └── audio/ (sfx, animations)
├── package.json
└── electron-builder.config.js
```

### Implementation Phases

**Phase 1: MVP (2-3 weeks)**
- Basic Electron window setup
- Static avatar render with mood states
- Text input and response display
- Claude API integration
- Window dragging and resizing
- Persistent state (position, size, chat history)

**Phase 2: Voice & Audio (1-2 weeks)**
- Web Speech API integration (voice-to-text)
- TTS engine (text-to-speech)
- Push-to-talk input mechanism
- Basic lip-sync to audio
- Audio streaming from Claude

**Phase 3: Context & Intelligence (1 week)**
- Active window detection (Windows API)
- Clipboard monitoring
- Screenshot capability
- Context-aware suggestions
- Quick actions menu

**Phase 4: Polish & Animation (1+ week)**
- Advanced animations (breathing, blinking, eye tracking)
- Smooth mood transitions
- UI refinements
- Performance optimization
- System tray integration
- Autostart on login

### Key Implementation Details

**Electron Main Process**
- Create BrowserWindow with frame: false for custom UI
- Set alwaysOnTop: true
- Implement dragging via CSS (user-select: none on drag handle)
- Handle app lifecycle (ready, before-quit, etc.)
- Set up IPC channels for renderer ↔ main communication

**Voice Input**
- Use Web Speech API for browser-compatible voice-to-text
- Fallback to external service if needed
- Show visual feedback while listening
- Auto-stop after silence detection

**Lip-Sync**
- Phoneme detection from TTS audio stream
- Map phonemes to avatar mouth positions
- Use CSS animations or canvas for smooth transitions
- Sync with audio playback (lag compensation)

**Context Detection**
- Windows: Use GetForegroundWindow() API via node-ffi
- macOS: Use native Objective-C bridge
- Linux: Use xdotool or similar
- Debounce updates to avoid constant polling

**Claude Integration**
- Use Claude API with streaming for real-time response
- Send mood preference as system instruction
- Keep conversation context in SQLite
- Implement rate limiting and retry logic

### Testing & Deployment
- Unit tests for core logic
- E2E tests for Electron app
- Code signing for Windows/macOS
- Auto-updates via electron-updater
- Installer via electron-builder

### Success Criteria
1. Avatar window stays on-top across all apps
2. Voice input captures and responds in <2 seconds
3. Lip-sync within 100ms of audio
4. Context detection within 500ms
5. <150MB memory footprint
6. Smooth animations at 60fps
7. Works offline with queued messages
8. Respects user privacy (no telemetry without consent)

---

**Note**: This is a complete specification for building Neural's desktop avatar companion. Implement iteratively, starting with Phase 1 MVP, then add features incrementally. Prioritize smooth animations and responsive interaction over feature completeness.
