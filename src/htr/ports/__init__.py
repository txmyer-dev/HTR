"""The four narrow interfaces (SPEC R5). One implementation each to start; vendors live behind
these, never in front of them."""
from .calendar import CalendarPort, Slot
from .contacts import ContactsPort
from .messaging import MessagingPort
from .voice import VoicePort, ToolCall, CallEnded

__all__ = ["CalendarPort", "Slot", "ContactsPort", "MessagingPort", "VoicePort",
           "ToolCall", "CallEnded"]
