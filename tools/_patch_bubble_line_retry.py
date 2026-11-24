from pathlib import Path
p=Path("ocr_engine/local_rapidocr.py")
s=p.read_text(encoding="utf-8")
old_pad="""            pad_x = max(8, round(line_h * 0.22))
            pad_y = max(6, round(line_h * 0.28))
"""
new_pad="""            pad_x = max(4, round(line_h * 0.12))
            pad_y = max(3, round(line_h * 0.10))
"""
assert s.count(old_pad)==1
s=s.replace(old_pad,new_pad,1)
old_join='        candidate = " ".join(candidate_lines)\n'
new_join="""        candidate_parts = []
        for line in candidate_lines:
            if (candidate_parts
                    and re.search(r"[-‐‑–—]$", candidate_parts[-1].rstrip())
                    and re.match(r"^[A-Za-z]", line.lstrip())):
                candidate_parts[-1] = candidate_parts[-1].rstrip() + line.lstrip()
            else:
                candidate_parts.append(line)
        candidate = " ".join(candidate_parts)
"""
assert s.count(old_join)==1
s=s.replace(old_join,new_join,1)
p.write_text(s,encoding="utf-8")
print("Tightened line crops and preserved hyphenated wraps.")
