# Card payments leave the media stream: Twilio `<Pay>`, never the agent

**Status: proposed, not built.** This is the design for a "pay my repair
invoice" journey, the phone version of the payment journeys a servicing
agent usually owns.

The rule is that no card digit reaches anything PitLine runs. That means
STT, the LLM, its context, the call record, and the logs.

## Why a prompt rule can't do it

Since step-up, the pipeline turns keypad presses into text for the LLM
(`DTMFAggregator` in `bot.py`). Twilio sends DTMF over the media stream as
events. So if a caller keyed a card number in during the call, those digits
would go into the model's context and the stored tool log. No instruction
can keep the model from seeing what's already in its context. The digits
have to never arrive.

Speaking the number aloud is worse. It goes through Deepgram as audio and
comes back as a transcript.

## Decision

Taking the payment moves the call **out of the stream**:

1. The agent verifies the caller first. A payment needs Tier 1 (Caller ID)
   or Tier 2 (SMS step-up). Then a `start_payment` tool looks up the
   invoice in the DMS. Code reads the amount back from that record, word
   for word, and the caller confirms it. The model never states an amount
   itself, which is a UDAAP point: a wrong amount said aloud is a
   misleading statement.
2. Code redirects the live call (Twilio REST `Calls/{sid}` update) to
   TwiML that ends `<Connect><Stream>` and runs `<Pay>`. From then on,
   Twilio collects the card over the keypad and sends it to the payment
   processor through its connector. PitLine receives only the outcome:
   success or a failure code, plus a processor reference.
3. `<Pay>`'s action URL connects the call back to a new stream. The agent
   picks up from a state record in the DMS, keyed by CallSid, because the
   first pipeline ended. Code, not the model, reads the result to the
   caller.

Twilio's account-level **PCI Mode** stays on, so payment data is redacted
from Twilio's own logs too. Recording is paused around `<Pay>`.

## Unhappy paths

| Case | Ends in |
|---|---|
| Card declined | One retry through `<Pay>`, then a transfer with reason `payment_failed`. The handoff says "declined", never why |
| Processor or `<Pay>` error | No claim that anything was paid. Transfer, and the handoff records the processor reference if there is one |
| Caller starts reading digits aloud | The agent cuts in with a fixed line ("please don't say your card number, you'll enter it on the keypad"). Deepgram's PCI redaction is on as a backstop, and the call record redacts digit runs |
| Caller isn't verified | No amount is read, and the caller is transferred. An unverified caller learns nothing about the invoice |
| Caller hangs up during `<Pay>` | The DMS state record expires. The payment result, if any, comes from the processor's webhook, not from the call |

## Alternatives rejected

- **Capture DTMF in our pipeline and mask it before the LLM.** This puts
  PitLine's own code in PCI scope as a system that handles card data. One
  missed path, such as a log line or the tool log, is a breach.
- **Send a pay-by-link SMS.** This is simpler and keeps us out of PCI
  scope entirely. It's the better choice if the business accepts the
  caller leaving the call to pay, and it needs `sms_consent` like any text.
  `<Pay>` is for "pay now, on this call."

## Tests this would need

- A simulator scenario where the stubbed `start_payment` reports a
  decline. The check: the agent never says "paid".
- A code check that no tool argument or result in any trace contains a
  run of 12 or more digits.
- One real call per release with a processor test card, because the
  redirect and the return to the stream are exactly what the text-only
  simulator can't see.
