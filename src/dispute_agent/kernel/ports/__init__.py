"""Kernel ports: the abstractions the kernel depends on (ARCHITECTURE.md section 2).

One file per port, all `typing.Protocol`. Adapters live outside the kernel and are wired in
by the composition root, so the kernel never imports an implementation — the import-linter
contract "Kernel is pure" is what keeps that true.

Only the infrastructure ports are `runtime_checkable`. `isinstance` against a Protocol checks
method names and nothing else, so it would pass for an object whose `now()` returns a string;
it earns its place for the small ports a fake is built for in tests, and not elsewhere, where
it would read as a guarantee it cannot give.
"""

from dispute_agent.kernel.ports.approval_service import ApprovalService
from dispute_agent.kernel.ports.case_outcome_observer import CaseOutcomeObserver
from dispute_agent.kernel.ports.case_repository import CaseRepository
from dispute_agent.kernel.ports.clock import Clock
from dispute_agent.kernel.ports.event_log import EventLog
from dispute_agent.kernel.ports.evidence_source import EvidenceSource
from dispute_agent.kernel.ports.id_generator import IdGenerator
from dispute_agent.kernel.ports.input_screen import InputScreen
from dispute_agent.kernel.ports.kill_switch import KillSwitch
from dispute_agent.kernel.ports.ledger import Ledger
from dispute_agent.kernel.ports.message_channel import MessageChannel
from dispute_agent.kernel.ports.message_drafter import MessageDrafter
from dispute_agent.kernel.ports.model_gateway import ModelGateway
from dispute_agent.kernel.ports.module_status import ModuleStatus
from dispute_agent.kernel.ports.pattern_context_provider import PatternContextProvider
from dispute_agent.kernel.ports.prompt_registry import PromptRegistry
from dispute_agent.kernel.ports.reasoner import Reasoner
from dispute_agent.kernel.ports.reply_interpreter import ReplyInterpreter
from dispute_agent.kernel.ports.ticketing_system import TicketingSystem
from dispute_agent.kernel.ports.tracer import Tracer

__all__ = [
    "ApprovalService",
    "CaseOutcomeObserver",
    "CaseRepository",
    "Clock",
    "EventLog",
    "EvidenceSource",
    "IdGenerator",
    "InputScreen",
    "KillSwitch",
    "Ledger",
    "MessageChannel",
    "MessageDrafter",
    "ModelGateway",
    "ModuleStatus",
    "PatternContextProvider",
    "PromptRegistry",
    "Reasoner",
    "ReplyInterpreter",
    "TicketingSystem",
    "Tracer",
]
