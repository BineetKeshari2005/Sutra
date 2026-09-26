"""Per-phase token ceilings enforced on the message history sent to the model.

`enforce_ceiling` is the actual fix for a real, observed failure mode: a
single open_file on a real, sizeable file (e.g. a ~450-line TSX component)
can cost 5k+ tokens for that one observation, and it then sits verbatim in
every later prompt for the rest of the phase, compounding on every
subsequent turn. Once a phase's cumulative spend crosses ~60-65% of its
ceiling -- not 100%, since summarizing needs its own token budget to run --
this collapses everything but the most recent few tool-call/observation
pairs into one short summary message.
"""
from __future__ import annotations

from harness.context_manager.summarizer import summarize_messages
from harness.model_adapter.base import Message, ModelAdapter

SUMMARIZE_THRESHOLD_FRACTION = 0.60
# ~3 tool-call/observation pairs (assistant-with-tool-call + tool-result each)
# kept verbatim; only messages older than this get collapsed.
KEEP_RECENT_MESSAGES = 6


def enforce_ceiling(
    messages: list[Message],
    phase_start_idx: int,
    spent_tokens: int,
    max_tokens: int,
    adapter: ModelAdapter,
) -> tuple[list[Message], dict | None]:
    """Returns (messages to actually send next, summary_info | None).

    `messages[phase_start_idx:]` is the current phase's slice of the running
    conversation (starting with that phase's instruction message). When spend
    is past the threshold and there's enough history to be worth collapsing,
    everything between the instruction message and the most recent
    KEEP_RECENT_MESSAGES gets replaced by one summary message, and
    `summary_info` describes what was collapsed (for the caller to log and
    charge the summarization call's own tokens to the budget). Returns the
    input unchanged (and None) when there's nothing worth summarizing yet.
    """
    if max_tokens <= 0 or spent_tokens < SUMMARIZE_THRESHOLD_FRACTION * max_tokens:
        return messages, None

    phase_messages = messages[phase_start_idx:]
    if len(phase_messages) <= 1 + KEEP_RECENT_MESSAGES:
        return messages, None  # not enough history yet to be worth collapsing

    instruction_msg = phase_messages[0]
    older = phase_messages[1:-KEEP_RECENT_MESSAGES]
    recent_tail = phase_messages[-KEEP_RECENT_MESSAGES:]
    if not older:
        return messages, None

    summary_message, usage = summarize_messages(older, adapter)
    collapsed_chars = sum(len(m.content) for m in older)

    new_messages = messages[:phase_start_idx] + [instruction_msg, summary_message, *recent_tail]
    summary_info = {
        "collapsed_messages": len(older),
        "collapsed_chars": collapsed_chars,
        "summary": summary_message.content,
        "tokens_used": usage.total_tokens,
    }
    return new_messages, summary_info
