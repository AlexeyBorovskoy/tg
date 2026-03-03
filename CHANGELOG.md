# Changelog

All notable changes to this project are documented in this file.

## v2026.03.03-1 - 2026-03-03

### Added
- Added standalone `transcription_system` subsystem:
  - queue-based transcription service (FastAPI + SQLite),
  - ASR providers: `assemblyai`, `local_whisper`, `compare`, `mock`,
  - glossary replacement pipeline,
  - prompt library with export/import,
  - role assignment and meeting protocol generation,
  - local auth (`admin/user`) and resource selection page (`/resources`).
- Added documentation: `docs/transcription_system_overview.md`.

### Changed
- Updated Telethon setup UX in web:
  - explicit delivery hint that login code usually arrives in Telegram app chat `Telegram/777000`, not SMS.
- Extended `/api/user/telethon/send-code` response with delivery metadata:
  - `code_delivery_type`, `code_delivery_channel`, `delivery_hint`.
- Updated top-level and web documentation to include transcription subsystem and Telethon code-delivery clarification.

### Notes
- Production web service was redeployed after these changes.
