#!/usr/bin/env python3
"""SESSION-177: Execute legacy state migration to vault."""
import sys
import os
import logging

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(message)s")

from mathart.evolution.state_vault import migrate_legacy_states, load_all_vault_states

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

print("=" * 70)
print("[StateVault] SESSION-177: Legacy State Hot Migration")
print("=" * 70)

manifest = migrate_legacy_states(project_root)
for m in manifest:
    print(f"  {m['status']:30s} {m['original_name']:50s} -> {m['clean_name']}")

print(f"\nTotal: {len(manifest)} files processed")
print()

# Verify vault contents
states = load_all_vault_states(project_root)
print(f"[StateVault] Vault now contains {len(states)} evolution state modules:")
for name, data in sorted(states.items()):
    keys = len(data) if isinstance(data, dict) else 0
    print(f"  - {name}: {keys} keys")
