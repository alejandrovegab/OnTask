"""OnTask - periodic on-task check-ins with focus tracking."""

__version__ = "0.1.0"

# How macOS, the keychain and notifications know the app, and what OnTask's own
# windows count as in rules. Shared with the Flatpak. Changing it resets every
# user's permissions, so it stays fixed.
APP_NAME = "OnTask"
BUNDLE_ID = "io.github.alejandrovegab.OnTask"
