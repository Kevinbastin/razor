# Decision log

## 1. Mandate is authority; it is not intent

Razorpay has no agent object. A mandate is a scoped, revocable user grant, so it is the authority primitive. Treating it as proof of goal alignment would hide the central threat; Layer 3 is intentionally separate.

## 2. Deterministic liability before LLM prose

Liability is a reproducible rule with explicit precedence. The LLM only turns an already-built packet into reviewer-friendly prose and is constrained to packet facts, because free-form model judgement is unsuitable for payment routing.

## 3. A6 is expected to pass behaviour checks

The simulator deliberately makes injected-intent sessions look normal. Optimizing Layer 2 to catch it would erase the distinction between behavioural anomaly and goal hijack; Layer 3 owns that residual risk.

## 4. Strict triple rather than beneficiary novelty alone

A new beneficiary is a legitimate hard negative. I4 fires only when first-time beneficiary, upper-quartile value, and off-pattern timing co-occur, trading recall for a more explainable alert.

## 5. Offline semantic embedding by default

The default Layer 3 embedding is local and deterministic so evaluation and demos do not download models or require external inference. It is a baseline that can be replaced with a reviewed production embedding after catalog-specific validation.

## 6. Fail closed, but recover medium risk

Malformed authority/intent data is denied or flagged. In contrast, a valid, coherent transaction with medium behavioural uncertainty becomes pending re-authorization rather than a hard decline.
