"""Fix syntax error in cli_wizard.py — unterminated string literal."""

with open("mathart/cli_wizard.py", "rb") as f:
    content = f.read().decode("utf-8")

# Fix the broken f-string with embedded newline
old_broken = '''            output_fn(
                f"\033[1;33m[⚠️ 视觉临摹] 处理失败: {_distill_err}\n"
                "将使用默认参数继续。\033[0m"
            )'''

# Check if this exact pattern exists
if old_broken in content:
    print("[FIX] Found exact broken pattern, fixing...")
else:
    # The issue is the raw newline inside the f-string
    # Let's find and fix it more carefully
    # Look for the actual bytes
    idx = content.find('处理失败: {_distill_err}')
    if idx >= 0:
        # Get context around it
        start = content.rfind('output_fn(', max(0, idx - 100), idx)
        end = content.find(')', idx) + 1
        # Find the actual closing of this output_fn call
        # Count parens
        paren_depth = 0
        scan_start = start
        actual_end = -1
        for i in range(scan_start, min(len(content), scan_start + 300)):
            if content[i] == '(':
                paren_depth += 1
            elif content[i] == ')':
                paren_depth -= 1
                if paren_depth == 0:
                    actual_end = i + 1
                    break
        
        if actual_end > 0:
            old_call = content[start:actual_end]
            print(f"[FIX] Found broken call ({len(old_call)} chars):")
            print(repr(old_call[:200]))
            
            new_call = '''output_fn(
                f"\\033[1;33m[⚠️ 视觉临摹] 处理失败: {_distill_err}\\n"
                "将使用默认参数继续。\\033[0m"
            )'''
            content = content[:start] + new_call + content[actual_end:]
            print("[FIX] Replaced broken call")
        else:
            print("[FIX] ERROR: Could not find end of broken call")
    else:
        print("[FIX] ERROR: Could not find broken string")

with open("mathart/cli_wizard.py", "wb") as f:
    f.write(content.encode("utf-8"))
print("[FIX] Done")
