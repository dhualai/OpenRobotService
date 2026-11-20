"""Clean up file references from overview and diagnosis docs.
Remove `-> \`xxx.md\`` patterns, keep keyword anchors for retrieval density.
Also remove entire "涉及模块" sections from diagnosis files.
"""
import re, os, glob

KB = r'D:\Code\OpenRobotService_Data\kb\team\usp'

def process_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    original = content

    # 1. Remove '-> `xxx.md`' — space before arrow is optional (overview: arrow adjacent to text)
    content = re.sub(r'(?:→|->) `[^`]+\.md`', '', content)

    # 2. Remove '-> xxx.md' without backticks
    content = re.sub(r'(?:→|->) [a-z_]+\.md', '', content)

    # 3. Remove "### 涉及模块" section entirely
    content = re.sub(r'\n### 涉及模块\n(?:(?!###? ).*\n?)*', '', content)

    # 4. Clean up orphaned lines that are just "- "
    content = re.sub(r'- \s*\n', '', content)

    # 5. Collapse excessive blank lines
    content = re.sub(r'\n{3,}', '\n\n', content)

    if content != original:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        return True
    return False

overview_files = glob.glob(os.path.join(KB, 'overview', '*.md'))
diagnosis_files = glob.glob(os.path.join(KB, 'diagnosis', '*.md'))
all_files = overview_files + diagnosis_files

count = 0
for fp in all_files:
    if process_file(fp):
        count += 1
        print(f'  OK  {os.path.basename(fp)}')

print(f'\nDone: {count}/{len(all_files)} files modified')
