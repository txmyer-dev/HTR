"""Adapter registry (STEALS: switchboard). A tenant's adapters: block in install.yaml names keys
here. Adding an implementation is adding an entry, never touching call sites."""
from __future__ import annotations

from ..config import Tenant


def calendar_for(tenant: Tenant, engine):
    kind = tenant.adapters.get("calendar", "local")
    if kind == "local":
        from .calendar_local import LocalCalendar
        return LocalCalendar(engine, tenant)
    raise KeyError(f"unknown calendar adapter: {kind}")


def contacts_for(tenant: Tenant, engine):
    kind = tenant.adapters.get("contacts", "local")
    if kind == "local":
        from .contacts_local import LocalContacts
        return LocalContacts(engine)
    raise KeyError(f"unknown contacts adapter: {kind}")


def messaging_for(tenant: Tenant):
    kind = tenant.adapters.get("messaging", "console")
    if kind == "console":
        from .messaging_console import ConsoleMessaging
        return ConsoleMessaging()
    if kind == "ntfy":
        from .messaging_ntfy import NtfyMessaging
        return NtfyMessaging()
    if kind == "twilio":
        from .messaging_twilio import TwilioMessaging
        return TwilioMessaging(tenant.secrets)
    raise KeyError(f"unknown messaging adapter: {kind}")


def voice_for(tenant: Tenant):
    kind = tenant.adapters.get("voice", "retell")
    if kind == "retell":
        from .voice_retell import RetellVoice
        return RetellVoice()
    raise KeyError(f"unknown voice adapter: {kind}")


def llm_for(tenant: Tenant):
    kind = tenant.adapters.get("llm", "template")
    if kind == "template":
        from .llm_template import TemplateLLM
        return TemplateLLM()
    if kind in ("openai", "omniroute", "openai_compat"):
        from .llm_openai_compat import OpenAICompatLLM
        return OpenAICompatLLM()
    raise KeyError(f"unknown llm adapter: {kind}")
