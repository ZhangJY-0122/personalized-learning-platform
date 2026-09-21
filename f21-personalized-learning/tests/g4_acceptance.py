"""Authoritative real G4C-06 acceptance entrypoint.

G4C-00..05 are intentionally consumed from their preserved artifact; only the
remaining isolated scenarios are executed here.
"""
from g4_remaining_acceptance import main as run_remaining
from g4c06_gate import main as close_gate

if __name__ == "__main__":
    run_remaining()
    close_gate()
