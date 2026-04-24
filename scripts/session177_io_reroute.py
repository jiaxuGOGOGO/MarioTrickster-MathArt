#!/usr/bin/env python3
"""SESSION-177: Automated I/O Route Refactoring Script.

This script performs the mechanical code transformation to reroute all
evolution bridge state file I/O from root-level hidden paths to the
unified State Vault at workspace/evolution_states/.

Strategy:
---------
There are two patterns to intercept:

Pattern A — Bridge classes with STATE_FILE class attribute:
    STATE_FILE = ".xxx_state.json"
    self.state_path = self.root / self.STATE_FILE
  Transform to:
    STATE_FILE = "xxx_state.json"  # dot stripped
    (add import of resolve_state_path)
    self.state_path = resolve_state_path(self.root, self.STATE_FILE)

Pattern B — Standalone collector functions with inline path:
    state_path = root / ".xxx_state.json"
  Transform to:
    state_path = resolve_state_path(root, ".xxx_state.json")

Pattern C — Inline self.project_root / ".xxx_state.json":
    state_path = self.project_root / ".xxx_state.json"
  Transform to:
    state_path = resolve_state_path(self.project_root, ".xxx_state.json")
"""
import re
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVOLUTION_DIR = os.path.join(PROJECT_ROOT, "mathart", "evolution")

# Import statement to add
VAULT_IMPORT = "from mathart.evolution.state_vault import resolve_state_path"
VAULT_IMPORT_RELATIVE = "from .state_vault import resolve_state_path"

def process_file(filepath):
    """Process a single Python file to reroute state I/O paths."""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    original = content
    changes = []
    
    # Check if file references state files at all
    if '_state.json' not in content and '_state"' not in content:
        return False, []
    
    # Determine if we're in the evolution package (use relative import)
    is_evolution_pkg = 'mathart/evolution/' in filepath.replace('\\', '/')
    import_line = VAULT_IMPORT_RELATIVE if is_evolution_pkg else VAULT_IMPORT
    
    # Skip state_vault.py itself
    if filepath.endswith('state_vault.py'):
        return False, []
    
    # ── Pattern A: STATE_FILE class attribute ──────────────────────────
    # Strip the leading dot from STATE_FILE values
    pattern_state_file = r'(STATE_FILE\s*=\s*)"(\.[^"]+_state\.json)"'
    def replace_state_file(m):
        prefix = m.group(1)
        old_name = m.group(2)
        new_name = old_name.lstrip('.')
        changes.append(f"STATE_FILE: '{old_name}' -> '{new_name}'")
        return f'{prefix}"{new_name}"'
    content = re.sub(pattern_state_file, replace_state_file, content)
    
    # ── Pattern B: self.state_path = self.root / self.STATE_FILE ──────
    # Transform to use resolve_state_path
    pattern_self_root_state = r'(self\.state_path\s*=\s*)self\.(root|project_root)\s*/\s*self\.STATE_FILE'
    def replace_self_root_state(m):
        prefix = m.group(1)
        root_attr = m.group(2)
        changes.append(f"self.state_path: self.{root_attr} / STATE_FILE -> resolve_state_path()")
        return f'{prefix}resolve_state_path(self.{root_attr}, self.STATE_FILE)'
    content = re.sub(pattern_self_root_state, replace_self_root_state, content)
    
    # ── Pattern C: state_path = root / ".xxx_state.json" (collectors) ─
    pattern_root_inline = r'((?:state_path|wfc_state_path|fluid_state_path)\s*=\s*)(\w+(?:\.\w+)*)\s*/\s*"(\.[^"]+_state\.json)"'
    def replace_root_inline(m):
        prefix = m.group(1)
        root_var = m.group(2)
        filename = m.group(3)
        changes.append(f"inline path: {root_var} / '{filename}' -> resolve_state_path()")
        return f'{prefix}resolve_state_path({root_var}, "{filename}")'
    content = re.sub(pattern_root_inline, replace_root_inline, content)
    
    # ── Pattern D: self.state_path = self.project_root / ".xxx_state.json" (layer3) ─
    pattern_self_inline = r'(self\.state_path\s*=\s*)self\.(project_root|root)\s*/\s*"(\.[^"]+_state\.json)"'
    def replace_self_inline(m):
        prefix = m.group(1)
        root_attr = m.group(2)
        filename = m.group(3)
        changes.append(f"self.state_path inline: self.{root_attr} / '{filename}' -> resolve_state_path()")
        return f'{prefix}resolve_state_path(self.{root_attr}, "{filename}")'
    content = re.sub(pattern_self_inline, replace_self_inline, content)
    
    # ── Pattern E: return self.project_root / ".xxx_state.json" (property) ─
    pattern_return_inline = r'(return\s+)self\.(project_root|root)\s*/\s*"(\.[^"]+_state\.json)"'
    def replace_return_inline(m):
        prefix = m.group(1)
        root_attr = m.group(2)
        filename = m.group(3)
        changes.append(f"return inline: self.{root_attr} / '{filename}' -> resolve_state_path()")
        return f'{prefix}resolve_state_path(self.{root_attr}, "{filename}")'
    content = re.sub(pattern_return_inline, replace_return_inline, content)
    
    # If changes were made, add the import
    if content != original:
        # Check if import already exists
        if 'from .state_vault import' not in content and 'from mathart.evolution.state_vault import' not in content:
            # Find the best place to insert the import
            # After the last 'from .' import or after 'from __future__' block
            lines = content.split('\n')
            insert_idx = 0
            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith('from __future__'):
                    insert_idx = i + 1
                elif stripped.startswith('from .') and 'import' in stripped:
                    insert_idx = i + 1
                elif stripped.startswith('import ') and insert_idx == 0:
                    insert_idx = i + 1
            
            lines.insert(insert_idx, import_line)
            content = '\n'.join(lines)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        return True, changes
    
    return False, []


def main():
    print("=" * 70)
    print("[SESSION-177] Inner Loop I/O Route Refactoring")
    print("=" * 70)
    
    total_files = 0
    total_changes = 0
    
    # Process all Python files in mathart/evolution/
    for filename in sorted(os.listdir(EVOLUTION_DIR)):
        if not filename.endswith('.py'):
            continue
        filepath = os.path.join(EVOLUTION_DIR, filename)
        modified, changes = process_file(filepath)
        if modified:
            total_files += 1
            total_changes += len(changes)
            print(f"\n  ✅ {filename}:")
            for c in changes:
                print(f"      - {c}")
    
    print(f"\n{'=' * 70}")
    print(f"Total: {total_files} files modified, {total_changes} I/O routes intercepted")
    print(f"{'=' * 70}")


if __name__ == '__main__':
    main()
