## 打包命令（PowerShell）

```powershell
pip install pyinstaller
pyinstaller --noconfirm --onefile --console --name OpenRobotDeploy deploy.py
```

产物：`d:\CODE\9_2\OpenRobotService\deploy\dist\OpenRobotDeploy.exe`（单文件，双击即打开 GUI）。

## 参数说明

| 参数 | 作用 |
|------|------|
| `--onefile` | 打成单个 exe（符合"独立可执行"） |
| `--console` | 保留控制台：GUI 模式日志走界面组件、CLI 模式（带参数运行）输出到控制台可见；代价是双击 GUI 时会多一个黑色控制台窗口 |
| `--name OpenRobotDeploy` | exe 名称，可改 |
| `--noconfirm` | 覆盖上次产物不交互确认 |

## 关键注意事项

1. **已修复 frozen 路径**：[default_project_path()](file:///d:/CODE/9_2/OpenRobotService/deploy/deploy.py#L92-98) 现在判断 `sys.frozen`，打包后默认项目路径指向 exe 所在目录（而非临时解压目录 `_MEIPASS`）；首次运行请在界面里填真实项目路径并点"保存配置"。

2. **外部工具不打包**：exe 依赖目标机器 PATH 中的 `tar`/`scp`/`ssh`/`npm`。Windows 10+ 自带 tar 与 OpenSSH（ssh/scp）；`npm` 需单独安装 Node.js。tkinter 由 PyInstaller 自动打包，无需额外处理。

3. **双击运行 = GUI**：因为 `len(sys.argv)==1` 走 GUI；从命令行带参数运行（如 `OpenRobotDeploy.exe -e test -c all --dry-run`）走 CLI。frozen exe 的 `sys.argv` 行为一致。

4. **可选替代**：
   - 嫌双击时控制台窗口难看、且只用 GUI：把 `--console` 换成 `--windowed`（但 CLI 模式输出将不可见）。
   - 想更快启动、不在乎单文件：把 `--onefile` 换成 `--onedir`（产出 `dist\OpenRobotDeploy\` 文件夹）。
   - 强制干净重建：加 `--clean`。

5. **清理构建残留**（可选，避免误提交）：`build\`、`dist\`、`OpenRobotDeploy.spec` 建议加入 `.gitignore`。

需要我直接执行上述命令生成 exe 吗？（会先安装 pyinstaller，构建约需 1–2 分钟，产物约 30–50MB）