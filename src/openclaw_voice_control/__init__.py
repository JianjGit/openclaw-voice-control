"""Public API for OpenClaw Voice Core."""

from .events import VoiceEvent, VoiceEventKind
from .presenter import ConsolePresenter, NullPresenter, Presenter
from .service import VoiceControlService

__all__ = [
    "ConsolePresenter",
    "NullPresenter",
    "Presenter",
    "VoiceControlService",
    "VoiceEvent",
    "VoiceEventKind",
    "__version__",
]

__version__ = "0.1.0"
