# LQA Helper

Windows 本地化测试工作台。按快捷键记录现场，按需画框或写备注，Codex 在后台对照项目词条整理待处理问题。

## 从源码启动

在 Windows 上安装 Python 3.10 或更高版本，然后在仓库根目录双击 **Start.cmd**。首次启动会下载并校验 ShareX 17.1.0。仓库已包含编译好的前端和 Excel 读取组件。使用 Windows 便携包时，直接解压并运行 **Start.cmd** 即可。

## 开始测试

1. 将 Windows x64 便携包解压到可写目录，双击 **Start.cmd**。工作台会在浏览器中打开。便携包已包含 Python 和 ShareX。
2. 选择 **新建项目**，填写名称。可以指定项目归档目录、目标语言和给 Codex 的检查要求。在 **参考文本** 下点 **选择文件…**，或直接拖入 Excel 术语表（`.xlsx`、`.xls`、`.xlsm`）。也支持 CSV、TSV、TXT 和 Markdown。参考文件分块导入，没有固定大小上限。词条表建议包含 ID、原文、译文三列。
3. 点击左下角的 **Codex → 连接 Codex**。应用复用本机官方 Codex CLI 的登录。若需要登录，按弹出的官方登录流程操作。后台使用 **GPT-6.2 Sol**，每次分析开启一个临时会话。在 **Codex → 推理强度** 中可选择 Low、Medium、High、Extra High、Max 或 Ultra，默认是 **Extra High**。选择会自动保存，所有项目从下一次开始分析的截图起生效。截图卡片的 **分析详情** 会显示本次使用的强度。暂时未连接时，截图先在本地排队。
4. 返回测试窗口，按 **F8** 截取当前窗口，或按 **F9** 框选区域。这是全新安装的默认键位。要改键，打开 **截屏设置**，点对应动作的 **录入**、按想用的单键或组合键，再点 **保存**。也可以在框内直接输入，比如 `Ctrl+Q` 或 `PrintScreen`。改键即时生效，重启后会记住。旧版升级后会先沿用原来的键位。
5. 右上角预览默认停留 **3 秒**，可在 **截屏设置** 调整。预览消失后才开始分析。点开预览后可以画红框、画箭头、裁剪、加标签或写备注，后台会等你保存或关闭编辑器。点 **保存标注** 或按 **Ctrl + Enter** 保存。裁剪时拖出要保留的范围，双击可恢复整张。工作台里的截图也可以重新打开。
   误截时，在编辑器右上角点 **删除截图** 并确认。工作台会一并删除这张截图的归档图片、标注和对应问题。
6. 在 **待处理问题** 逐项查看问题、标为已处理或导出 CSV。切换到 **相关条目**，可按语言包的 Key 和中文原文查看这些问题。展开条目即可看到问题、截图和来源行。点击该页的 **导出 CSV**，每个 Key 与中文原文的组合占一行。默认只导出有关联待处理问题的条目，打开 **显示已处理** 可一并导出已处理问题。同一问题可关联多个词条，同一词条也可关联多张截图。未核实到语言包词条的问题仍可在问题视图查看。在截图卡片下展开 **分析详情** 或 **查看失败原因** 可以重新分析。

截屏后若文件未能归档，工作台首页会显示收件箱里的文件名和失败原因。若截图已归档、分析失败，展开截图卡片上的 **查看失败原因** 即可查看详情。

关闭浏览器后，启动窗口继续运行截屏和后台队列。选择 **截屏设置 → 退出工作台** 可以停止截屏，正在分析的任务会完成收尾。重新打开后会继续处理排队或上次中断的截图。

## 本地文件

默认数据目录为应用旁的 `.lqa`。点 **截屏设置 → 打开项目目录**，即可查看这两个同级文件夹。

- `captures/<截图编号>/` 保存原图、编辑后的图片和 `capture.json`，其中记录标签、备注、裁剪范围、标注坐标和分析结果。保存裁剪后，缩略图、该版本的 Codex 分析和 CSV 截图路径都采用裁剪结果，原图可用于重新编辑。
- `references/` 保存项目参考文件。选择 Excel 时，原始工作簿和供 Codex 检索的 TSV 索引都在这里。

工作台在导入 Excel 时一次性为各工作表中的非空数据行建立索引。每行都带有工作表名和 Excel 原始行号，公式使用工作簿中已保存的计算结果。后台对照这份文本定位词条。

仓库用 `.gitignore` 列出可提交的顶层文件和目录。真实截图、项目参考表、导出 CSV、ShareX 本机设置和便携 ZIP 默认留在本地。`tests/fixtures/` 里有三个随仓库分发的合成样本。

## 复用的组件

前端以桌面大屏为默认工作环境，按 1440 和 1920 像素宽度验收。项目栏常驻左侧，截图编辑器使用横向工具栏。通用控件优先从 shadcn/ui 引入，交互使用 Radix UI，图标使用 Lucide。主题颜色、字体和间距集中在 `frontend/src/theme.css`。[shadcn/ui](https://ui.shadcn.com/docs/components) · [Lucide](https://lucide.dev/)

便携包自带编译后的界面、Inter 字体和许可证。修改前端时在 `frontend` 目录执行 `npm ci` 和 `npm run build`，产物写入 `web`。运行工作台可直接双击 **Start.cmd**。

- 截屏使用 ShareX 17.1.0 的官方便携二进制和 Windows 的 .NET Framework 4.8。随包保留了截图和区域标注所用的组件，省去了 FFmpeg、录屏设备安装器和 ExifTool。可通过 `setup-sharex.ps1` 从官方包校验 SHA-256 并重新提取。[正式发布](https://github.com/ShareX/ShareX/releases/tag/v17.1.0) · [对应源码](https://github.com/ShareX/ShareX/tree/v17.1.0)
- LQA 类别、严重度、问题字段和检查清单改编自 GameLQA 的 MIT 版本，许可证在 `resources/GameLQA-LICENSE.txt`。[对应源码](https://github.com/Grayson0130/GameLQA/tree/265e7492b291f2c7913acfcc0a89c39bbc5a7cf6)
- 便携版使用官方 CPython 3.14.10 Windows x64 嵌入式运行时。后台调用本机 Codex CLI 的非交互模式。[Python 发布](https://www.python.org/downloads/release/python-31410/) · [Codex 非交互模式](https://developers.openai.com/codex/noninteractive/)
- Excel 读取组件随便携包提供，许可证包含在 `vendor/excel/` 的文件中。[openpyxl](https://pypi.org/project/openpyxl/3.1.5/) · [et_xmlfile](https://pypi.org/project/et-xmlfile/2.0.0/) · [xlrd](https://pypi.org/project/xlrd/2.0.2/)
- 文件上传使用 multipart 分块读取，许可证在 `vendor/formdata/LICENSE`。[multipart](https://pypi.org/project/multipart/2.0.1/)
- ShareX 截图保留 PNG，超过传输阈值时可用 JPG。工作台同时接受这两种格式，并用 imagesize 读取 JPG 尺寸。[imagesize](https://pypi.org/project/imagesize/1.4.1/)

在源码目录运行 `python -m unittest discover -s tests -v` 可以在临时项目中验证队列、归档、项目归属和参考文件处理。便携版可使用 `vendor\python\python.exe -m unittest discover -s tests -v`。
