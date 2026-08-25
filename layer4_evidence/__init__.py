"""
Agent Transaction Risk Layer — Layer 4: Evidence Generator

Assembles structured dispute-evidence packets from all three
layers' outputs. Submits via Razorpay's contest-dispute endpoint.

Produces liability determinations:
- merchant-defensible
- merchant-should-accept
- escalate-to-provider
- fraud
"""
