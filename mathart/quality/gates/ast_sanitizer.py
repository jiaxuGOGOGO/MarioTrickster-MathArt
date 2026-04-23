"""SESSION-155: AST Sanitizer for Auto-Generated Enforcers.

This module provides strict AST-based validation for Python code generated
by the Autonomous Knowledge Compiler. It ensures that the generated code
is safe to execute and conforms to the Enforcer plugin contract.

Rules enforced:
1. Must be valid Python syntax (parsable by ast.parse).
2. Must contain exactly one ClassDef inheriting from EnforcerBase.
3. Must not contain any imports inside the class body.
4. Must not call blacklisted functions (exec, eval, open, etc.).
5. Must implement required methods: name, source_docs, validate.
"""

import ast
from typing import List, Set, Tuple


class AstValidationError(Exception):
    """Raised when generated code fails AST validation."""
    pass


class EnforcerNodeVisitor(ast.NodeVisitor):
    """Walks the AST to validate Enforcer plugin constraints."""

    BLACKLISTED_FUNCTIONS = {
        "exec", "eval", "open", "__import__", "compile",
        "globals", "locals", "getattr", "setattr", "delattr",
        "hasattr", "memoryview", "bytearray", "bytes",
    }

    def __init__(self) -> None:
        self.class_count = 0
        self.enforcer_class_node: ast.ClassDef | None = None
        self.methods_found: Set[str] = set()
        self.errors: List[str] = []
        self.in_class_body = False

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.class_count += 1
        
        # Check base classes
        has_enforcer_base = False
        for base in node.bases:
            if isinstance(base, ast.Name) and base.id == "EnforcerBase":
                has_enforcer_base = True
                break
            elif isinstance(base, ast.Attribute) and base.attr == "EnforcerBase":
                has_enforcer_base = True
                break
                
        if not has_enforcer_base:
            self.errors.append(f"Class '{node.name}' does not inherit from EnforcerBase.")
        else:
            self.enforcer_class_node = node
            
        # Visit body
        self.in_class_body = True
        self.generic_visit(node)
        self.in_class_body = False

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if self.in_class_body:
            self.methods_found.add(node.name)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        if self.in_class_body:
            self.errors.append(f"Line {node.lineno}: Import statements are not allowed inside the class body.")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if self.in_class_body:
            self.errors.append(f"Line {node.lineno}: ImportFrom statements are not allowed inside the class body.")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func_name = None
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr

        if func_name in self.BLACKLISTED_FUNCTIONS:
            self.errors.append(f"Line {node.lineno}: Call to blacklisted function '{func_name}' is forbidden.")
            
        self.generic_visit(node)


def validate_enforcer_code(source_code: str) -> Tuple[bool, List[str]]:
    """Validate that the source code is a safe Enforcer plugin.
    
    Args:
        source_code: The Python source code to validate.
        
    Returns:
        Tuple of (is_valid, list_of_error_messages).
    """
    try:
        tree = ast.parse(source_code, mode='exec')
    except SyntaxError as e:
        return False, [f"SyntaxError at line {e.lineno}: {e.msg}"]
    except Exception as e:
        return False, [f"Failed to parse AST: {str(e)}"]

    visitor = EnforcerNodeVisitor()
    visitor.visit(tree)

    errors = list(visitor.errors)

    if visitor.class_count == 0:
        errors.append("No class definition found in the generated code.")
    elif visitor.class_count > 1:
        errors.append(f"Expected exactly 1 class definition, found {visitor.class_count}.")

    if visitor.enforcer_class_node:
        required_methods = {"name", "source_docs", "validate"}
        missing = required_methods - visitor.methods_found
        if missing:
            errors.append(f"Class '{visitor.enforcer_class_node.name}' is missing required methods: {', '.join(missing)}")

    return len(errors) == 0, errors
