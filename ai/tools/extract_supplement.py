"""Extract all supplement PDFs to text."""
import fitz, os
from pathlib import Path

src_dir = Path(r"D:\Code\OpenRobotService_Data\机器人产品增项")
out_dir = Path(r"C:\Users\PAJ26020\.claude\tmp_robot_texts\supplement")
os.makedirs(out_dir, exist_ok=True)

pdfs = sorted(src_dir.rglob("*.pdf"))
print(f"Found {len(pdfs)} PDF files")

for pdf_path in pdfs:
    rel_path = pdf_path.relative_to(src_dir)
    # Use forward slashes in output name
    rel_str = str(rel_path).replace("\\", "/")
    out_name = rel_str.replace("/", "_")
    out_file = out_dir / (out_name + ".txt")

    doc = fitz.open(str(pdf_path))
    pages = []
    for i in range(doc.page_count):
        text = doc[i].get_text()
        if text.strip():
            pages.append(f"--- PAGE {i+1} ---")
            pages.append(text)
    doc.close()

    with open(out_file, "w", encoding="utf-8") as f:
        f.write("\n".join(pages))
    print(f"  {len(pages)//2:>3}p -> {out_file.name}")

print("Done!")
