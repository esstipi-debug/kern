"""Job-fulfillment layer — turn a client brief + data into a deliverable.

This sits on top of the engine (src/) and packages it for real supply-chain
work: intake (adapt arbitrary client data to the canonical schema), a playbook
that runs the analysis, deliverable generation (Excel + report), and automated
QA. Designed so a human stays in the loop for client comms and final review.
"""
