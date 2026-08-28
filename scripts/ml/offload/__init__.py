"""R3 offload transport — carry a runner-trained model to the trainer's registry.

``emit_bundle`` runs on the RUNNER and writes a committable drop; ``drain_inbox``
runs on the TRAINER and re-registers it there (so the registered path resolves
by construction). The drain ships UNARMED — see its module docstring.
"""
