# Handoff simulation (CONTEXT.md): one scripted response - "connecting you to
# our on-call service advisor now" - reused for every trigger, with a short
# situation-specific lead-in. No real telephony transfer happens for any of
# these.
TRANSFER_MESSAGES = {
    "urgent_no_availability": (
        "This needs a Master Technician right away - connecting you to our "
        "on-call service advisor now."
    ),
    "cannot_understand_caller": (
        "I'm having trouble understanding you - connecting you to our on-call "
        "service advisor now."
    ),
    "cannot_verify_caller": (
        "I'm not able to verify this call to make changes to an existing "
        "appointment - connecting you to our on-call service advisor now."
    ),
    "verification_failed": (
        "That code didn't match, so I can't make changes to this "
        "appointment - connecting you to our on-call service advisor now."
    ),
}
