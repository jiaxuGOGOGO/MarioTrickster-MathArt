"""Fix the UX banner to include SESSION-179 specific lines."""

with open("mathart/factory/mass_production.py", "rb") as f:
    content = f.read().decode("utf-8")

# The old banner ends with JIT Resolution Hydration line
old_end = '''        f"\\033[1;35m    \\u2514\\u2500 SESSION-172 JIT Resolution Hydration: "
        f"\\u63a8\\u6d41\\u524d\\u7f6e 512 \\u5185\\u5b58\\u4e0a\\u91c7\\u6837\\u5df2\\u6fc0\\u6d3b\\uff0c"
        f"\\u82f1\\u6587\\u63d0\\u793a\\u8bcd\\u91cd\\u7532\\u5df2\\u88c5\\u8f7d\\033[0m"
    )'''

new_end = '''        f"\\033[1;35m    \\u251c\\u2500 SESSION-172 JIT Resolution Hydration: "
        f"\\u63a8\\u6d41\\u524d\\u7f6e 512 \\u5185\\u5b58\\u4e0a\\u91c7\\u6837\\u5df2\\u6fc0\\u6d3b\\uff0c"
        f"\\u82f1\\u6587\\u63d0\\u793a\\u8bcd\\u91cd\\u7532\\u5df2\\u88c5\\u8f7d\\033[0m\\n"
        f"\\033[1;35m    \\u251c\\u2500 SESSION-179 SparseCtrl Time-Window Clamping: "
        f"end_percent 0.4~0.6 \\u9650\\u5e45\\u5df2\\u6fc0\\u6d3b\\uff0c\\u957f\\u955c\\u5934\\u95ea\\u70c1\\u5df2\\u6839\\u6cbb\\033[0m\\n"
        f"\\033[1;35m    \\u2514\\u2500 SESSION-179 cancel_futures Global Meltdown: "
        f"OOM \\u5168\\u5c40\\u7194\\u65ad\\u5df2\\u5347\\u7ea7\\uff0cexecutor.shutdown(cancel_futures=True)\\033[0m"
    )'''

if old_end in content:
    content = content.replace(old_end, new_end)
    print("[UX BANNER] SESSION-179 lines added to banner")
else:
    print("[UX BANNER] WARNING: Could not find old banner end")
    # Try with actual unicode chars
    # Let's find the _ux_msg block boundaries
    idx_start = content.find('_ux_msg = (')
    if idx_start >= 0:
        idx_end = content.find('\n    )', idx_start)
        if idx_end >= 0:
            block = content[idx_start:idx_end + len('\n    )')]
            print(f"[DEBUG] Found _ux_msg block at {idx_start}:{idx_end + 5}")
            # Check if SESSION-179 already in block
            if 'SESSION-179' in block:
                print("[UX BANNER] SESSION-179 already in banner")
            else:
                # Find the last └─ line and change it to ├─, then add two new lines
                last_line_marker = '└─'
                if last_line_marker in block:
                    block = block.replace('└─', '├─', 1)
                    # Find the closing )
                    close_paren = block.rfind(')')
                    # Insert before closing paren
                    insert = '''
        f"\\n"
        f"\\033[1;35m    ├─ SESSION-179 SparseCtrl Time-Window Clamping: "
        f"end_percent 0.4~0.6 限幅已激活，长镜头闪烁已根治\\033[0m\\n"
        f"\\033[1;35m    └─ SESSION-179 cancel_futures Global Meltdown: "
        f"OOM 全局熔断已升级，executor.shutdown(cancel_futures=True)\\033[0m"'''
                    # Replace the last line's \\033[0m" with \\033[0m\\n" and add new lines
                    new_block = block[:close_paren] + insert + '\n    )'
                    content = content[:idx_start] + new_block + content[idx_end + len('\n    )'):]
                    print("[UX BANNER] SESSION-179 lines injected via fallback method")

with open("mathart/factory/mass_production.py", "wb") as f:
    f.write(content.encode("utf-8"))
