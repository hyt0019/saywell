# 开发说明

## 环境

需要 Windows 和 Python 3.12。模型、密钥、用户录音和本地打包产物不进入 Git 仓库。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m better_voice_input.app
```

精确依赖版本保存在 `requirements-lock.txt`。运行 `scripts/build.ps1` 会先跑测试，再用 PyInstaller 构建目录式便携程序 `dist/BetterVoiceInput/`。本地构建可双击 `启动好好说.cmd` 在托盘启动。

## API 与密钥

API 地址支持 Base URL（如 `https://api.example.com/v1`）或完整 `/chat/completions` 地址；模型名称使用服务商提供的 ID。DeepSeek 只是默认值。密钥按 API 地址分别保存在 Windows 凭据管理器，切换地址时需要该服务的密钥。

开发测试可将 `BVI_API_KEY` 与匹配的 `BVI_API_BASE_URL` 环境变量一起使用。旧 `DEEPSEEK_API_KEY` 和根目录 `deepseek api key.txt` 仅供 DeepSeek 官方接口使用。提交前可运行 `scripts/verify_private_files.py` 检查已跟踪文件中没有密钥和私有素材。

## 命令行与脚本

```powershell
.\.venv\Scripts\python.exe -m better_voice_input.cli download-models --asr-model canary
.\.venv\Scripts\python.exe -m better_voice_input.cli transcribe '录音.m4a' --asr-model xasr --clean --output private/result.json
.\.venv\Scripts\python.exe scripts/evaluate_text.py   # 调用真实 API，会计费
.\.venv\Scripts\python.exe scripts/capture_ui.py      # 离屏渲染界面截图到 artifacts/screenshots
```

普通 pytest 不访问网络，也不读取测试 Key。

## 图标

源图在 `src/better_voice_input/assets/`：`icon.png` 是带底板的彩色图标（窗口、任务栏、浅色任务栏的托盘），`tray-white.png` 是深色任务栏托盘用的白色图形，均为 1024×1024 透明 PNG。替换源图后运行 `.venv\Scripts\python.exe scripts\make_icon.py` 重新生成 `assets/app.ico`（exe 和安装包图标，含 16～256 共 10 个尺寸）。

## 模型广场

精选的三个模型在 `models.py` 的 `CATALOG`，扩展模型在 `PLAZA`。每个模型固定 Hugging Face 提交版本并记录每个文件的大小和哈希（LFS 文件用 SHA-256，小文件用 git blob SHA-1），`kind` 对应 `sherpa_onnx.OfflineRecognizer.from_<kind>`，`inputs` 和 `options` 是传给它的文件与参数。`seconds_per_minute` 是本机实测识别 60 秒语音的耗时，卡片上统一显示为“60 秒语音约 X 秒”。新增模型时补上分类、实测速度和模型主页，并实测能下载、校验、加载和识别；许可协议记录在 THIRD_PARTY_NOTICES.md。

## 发布安装包

一次性准备：`winget install JRSoftware.InnoSetup`（按用户安装即可）；如需命令行发布，再安装 GitHub CLI 并运行 `gh auth login`。

1. 修改 `pyproject.toml` 和 `src/better_voice_input/__init__.py` 中的版本号（两处必须一致），在 `docs/releases/` 写好该版本的发布说明并提交。
2. 退出正在运行的好好说（打包会覆盖 `dist`），运行 `scripts\build_installer.ps1`。它会跑完测试、构建程序、检查程序目录中没有模型和密钥，然后生成 `release\BetterVoiceInput-Setup-版本号.exe` 及对应的 `.sha256` 文件。
3. 在干净环境（Windows 沙盒或新的 Windows 用户）中安装、完成首次使用流程、再卸载一遍。
4. 打标签并发布：

```powershell
git tag v0.1.0
git push origin v0.1.0
gh release create v0.1.0 release\BetterVoiceInput-Setup-0.1.0.exe release\BetterVoiceInput-Setup-0.1.0.exe.sha256 --title "好好说 0.1.0" --notes-file docs\releases\v0.1.0.md
```

也可以在 GitHub 网页的 Releases → Draft a new release 中选择标签、填写说明并上传这两个文件。

安装包按用户安装到 `%LOCALAPPDATA%\Programs\BetterVoiceInput`，不需要管理员权限；安装和卸载时只关闭该目录中运行的程序，卸载只移除指向该目录的开机自启项，用户数据保留在 `%LOCALAPPDATA%\BetterVoiceInput`。安装向导使用 Inno Setup 官方仓库中社区维护的简体中文翻译（`installer/ChineseSimplified.isl`，对应 Inno Setup 6.7.3）。

## 设计原则

- 音频在本机识别；转写文本和相关词库发送至设置中选定的 API。
- 清理表达，不回答口述问题、不执行口述命令。
- 快捷键录音直接输入整理结果，疑点不阻断；API 整理失败时输入识别原文。原文和结果可在主窗口查看。
- 录音由用户主动开始和结束，停顿不自动提交。
- 只输入文字，不自动按回车发送。
- 录音仅在内存中，本地识别后释放；默认不保存文字历史，开启后最多保留最近 5 条（最长 7 天）。
- 语音模型只收录识别耗时与 SenseVoice 同一量级的模型，保证实时听写；模型文件固定版本并校验摘要。

## 文档

- [实现规划与界面设计](PLAN.md)
- [使用指南](USER_GUIDE.md)
- [验证记录](VALIDATION.md)
- [第三方组件](../THIRD_PARTY_NOTICES.md)
