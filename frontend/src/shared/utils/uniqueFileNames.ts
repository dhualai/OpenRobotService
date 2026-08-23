/**
 * 附件重名去重工具
 *
 * 场景：讨论区/评论区一次粘贴或选择多张图片时，剪贴板/相册图片往往都叫
 * 相同默认名（如 image.png），而上传到后端的对象名由 `temp_id/文件名` 决定，
 * 同名会被 MinIO 覆盖写 → 多条附件路径相同 → 界面显示"三张一样的图"。
 *
 * 解决：在上传前把同一批文件里重名的文件自动改名（image.png → image1.png、
 * image2.png），保证文件名唯一，避免后端覆盖，也保留可读的文件名。
 */

/** 拆分主干名与扩展名，返回 [base, ext]，无扩展时 ext 为空串 */
function splitName(filename: string): [string, string] {
  const idx = filename.lastIndexOf('.');
  if (idx <= 0) return [filename, ''];
  return [filename.slice(0, idx), filename.slice(idx)];
}

/**
 * 对同一批文件去重命名：出现重名时从 1 开始找第一个不冲突的序号，
 * 例如第二个 image.png → image1.png、第三个 → image2.png。
 * 返回新的 File[]，未冲突的文件原样返回，不改变调用方原有对象。
 */
export function dedupeFileNames(files: File[]): File[] {
  if (files.length < 2) return files;

  const used = new Set<string>(); // 已占用的完整文件名（含原始与改名后的）
  const result: File[] = [];

  for (const file of files) {
    let newName = file.name;
    if (used.has(file.name)) {
      // 已有重名，从 1 开始找第一个未被占用的 baseN.ext
      const [base, ext] = splitName(file.name);
      let n = 1;
      while (used.has(`${base}${n}${ext}`)) n += 1;
      newName = `${base}${n}${ext}`;
    }
    used.add(newName);
    // 重命名时才需要重建 File；未冲突沿用原对象
    result.push(newName === file.name ? file : new File([file], newName, { type: file.type }));
  }

  return result;
}
