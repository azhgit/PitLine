import re

TERMINAL_OUTCOMES = {"transfer_and_end_call": "transferred", "end_call": "resolved_no_action"}


def classify(tool_log: list[dict]) -> tuple[str, str | None]:
    """(outcome, transfer_reason) from what the agent's tools actually did -
    code decides, not the model. A successful booking or cancel wins over
    how the call ended; no terminal tool means the caller hung up first.
    """
    succeeded = {t["tool"] for t in tool_log if not (t.get("result") or {}).get("error")}
    for t in tool_log:
        if t["tool"] == "transfer_and_end_call":
            return "transferred", t["args"].get("reason")
    if "book_appointment" in succeeded and any("appointment" in (t.get("result") or {}) for t in tool_log):
        return "booked", None
    if any(t["tool"] == "cancel_appointment" and (t.get("result") or {}).get("cancelled") for t in tool_log):
        return "cancelled", None
    if any(t["tool"] == "end_call" for t in tool_log):
        return "resolved_no_action", None
    return "abandoned", None


def redaction_terms(tool_log: list[dict], calling_number: str | None) -> list[str]:
    """Strings this call is known to have spoken that identify the caller:
    the booked name (and its parts) and phone numbers. Anything the caller
    said that never reached a tool - a name given before a transfer, say -
    isn't known here and stays in the transcript (README: known limitations).
    """
    terms = []
    for t in tool_log:
        name = (t.get("args") or {}).get("customer_name")
        if name:
            terms.append(name)
            terms += [part for part in name.split() if len(part) >= 3]
        phone = (t.get("args") or {}).get("customer_phone")
        if phone:
            terms.append(phone)
        result = t.get("result") or {}
        if result.get("calling_number"):
            terms.append(result["calling_number"])
    if calling_number:
        terms.append(calling_number)
    return sorted(set(terms), key=len, reverse=True)  # longest first: "Andrew Kong" before "Andrew"


def redact(text: str, terms: list[str]) -> str:
    for term in terms:
        label = "[phone]" if re.search(r"\d", term) else "[name]"
        text = re.sub(rf"\b{re.escape(term)}\b", label, text, flags=re.IGNORECASE)
    return text


def transcript(messages: list[dict], kickoff: str, terms: list[str]) -> list[dict]:
    """What was said aloud, in order, redacted - tool calls/results and the
    kickoff instruction dropped (those are in tool_log)."""
    lines = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            texts = [content]
        else:
            texts = [b.get("text", "") for b in content or [] if b.get("type") == "text"]
        for text in texts:
            if text.strip() and text != kickoff:
                role = "agent" if m.get("role") == "assistant" else "caller"
                lines.append({"role": role, "text": redact(text, terms)})
    return lines


if __name__ == "__main__":
    book = {"tool": "book_appointment", "args": {"customer_name": "Andrew Kong"}, "result": {"appointment": {"id": 1}}}
    avail = {"tool": "check_availability", "args": {}, "result": {"slots": [], "calling_number": "672-399-7007"}}
    end = {"tool": "end_call", "args": {}, "result": {"closing": True}}

    assert classify([avail, book, end]) == ("booked", None)
    assert classify([avail, {"tool": "book_appointment", "args": {}, "result": {"error": "booking_failed"}}, end]) == ("resolved_no_action", None)
    assert classify([avail, {"tool": "transfer_and_end_call", "args": {"reason": "urgent_no_availability"}, "result": {}}]) == ("transferred", "urgent_no_availability")
    assert classify([{"tool": "find_appointment", "args": {}, "result": {"verified": True}}, {"tool": "cancel_appointment", "args": {}, "result": {"cancelled": True}}, end]) == ("cancelled", None)
    assert classify([avail]) == ("abandoned", None)

    terms = redaction_terms([avail, book], None)
    msgs = [
        {"role": "user", "content": "The caller just connected."},
        {"role": "assistant", "content": "What's going on?"},
        {"role": "user", "content": "I'm andrew kong, it's a Corolla"},
        {"role": "assistant", "content": [{"type": "text", "text": "Thanks, Andrew. We'll reach you at 672-399-7007."}, {"type": "tool_use"}]},
        {"role": "user", "content": [{"type": "tool_result", "content": "{}"}]},
    ]
    assert transcript(msgs, "The caller just connected.", terms) == [
        {"role": "agent", "text": "What's going on?"},
        {"role": "caller", "text": "I'm [name], it's a Corolla"},
        {"role": "agent", "text": "Thanks, [name]. We'll reach you at [phone]."},
    ]
    print("ok")
