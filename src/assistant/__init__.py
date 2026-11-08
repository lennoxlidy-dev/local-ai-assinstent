"""Local, voice-controlled AI assistant for Windows."""

__version__ = "0.1.11"

#: Internal application id: used for folder names, registry values, the lock file.
#: This is NOT the assistant's spoken name (that one is user-chosen, see config).
APP_ID = "Assistant"

#: Explicit AppUserModelID so Windows groups our windows/notifications correctly.
APP_USER_MODEL_ID = "LocalAssistant.Assistant"
