import sys

# git rebase -i 的 todo 编辑器：把前两个 pick 改为 reword（即要改消息的两个乱码提交）
path = sys.argv[1]
with open(path, encoding='utf-8') as f:
    lines = f.readlines()

converted = 0
for i, line in enumerate(lines):
    if line.startswith('pick ') and converted < 2:
        lines[i] = 'reword ' + line[5:]
        converted += 1

with open(path, 'w', encoding='utf-8') as f:
    f.writelines(lines)
