# 开发说明

## 环境

需要 Windows 和 Python 3.12。模型、密钥、用户录音和本地打包产物不进入 Git 仓库。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m saywell.app
```

精确依赖版本保存在 `requirements-lock.txt`。运行 `scripts/build.ps1` 会先跑测试，再用 PyInstaller 构建目录式便携程序 `dist/Saywell/`。本地构建可双击 `启动好好说.cmd` 在托盘启动。

## API 与密钥

API 地址支持 Base URL（如 `https://api.example.com/v1`）或完整 `/chat/completions` 地址；模型名称使用服务商提供的 ID。DeepSeek 只是默认值。密钥按 API 地址分别保存在 Windows 凭据管理器，切换地址时需要该服务的密钥。

开发测试可将 `SAYWELL_API_KEY` 与匹配的 `SAYWELL_API_BASE_URL` 环境变量一起使用。旧 `DEEPSEEK_API_KEY` 和根目录 `deepseek api key.txt` 仅供 DeepSeek 官方接口使用。提交前可运行 `scripts/verify_private_files.py` 检查已跟踪文件中没有密钥和私有素材。

## 命令行与脚本

```powershell
.\.venv\Scripts\python.exe -m saywell.cli download-models --asr-model canary
.\.venv\Scripts\python.exe -m saywell.cli transcribe '录音.m4a' --asr-model xasr --clean --output private/result.json
.\.venv\Scripts\python.exe scripts/evaluate_text.py   # 调用真实 API，会计费
.\.venv\Scripts\python.exe scripts/capture_ui.py      # 离屏渲染界面截图到 artifacts/screenshots
```

普通 pytest 不访问网络，也不读取测试 Key。

## 图标

源图在 `src/saywell/assets/`：`icon.png` 是带底板的彩色图标（窗口、任务栏、浅色任务栏的托盘），`tray-white.png` 是深色任务栏托盘用的白色图形，均为 1024×1024 透明 PNG。替换源图后运行 `.venv\Scripts\python.exe scripts\make_icon.py` 重新生成 `assets/app.ico`（exe 和安装包图标，含 16～256 共 10 个尺寸）。

## 语音模型与引擎

精选模型在 `models.py` 的 `CATALOG`，扩展模型在 `PLAZA`。每个模型固定 Hugging Face 提交版本并记录每个文件的大小和哈希（LFS 文件用 SHA-256，小文件用 git blob SHA-1），`kind` 对应 `sherpa_onnx.OfflineRecognizer.from_<kind>`，`inputs` 和 `options` 是传给它的文件与参数。

`kind="llama_server"` 的模型（目前是 Qwen3-ASR Q8_0 GGUF）由 `llama.py` 运行：llama.cpp 官方 Windows CPU 发布包作为模型文件之一固定版本下载并校验，首次使用时只把 `llama-server.exe`、DLL 和许可文件解压到模型文件夹的 `llama.cpp/`。程序在 127.0.0.1 的随机端口启动 llama-server，带随机 API Key，进程放进 Windows 作业对象，程序退出或崩溃时一并结束。识别仍走 `LocalRecognizer` 的分段逻辑，每段以 WAV 通过 `/v1/chat/completions` 的 `input_audio` 发送，回答中 `<asr_text>` 之后的部分是识别文字。`seconds_per_minute` 是本机实测识别 60 秒语音的耗时，卡片上统一显示为“60 秒语音约 X 秒”。新增模型时补上分类、实测速度和模型主页，并实测能下载、校验、加载和识别；许可协议记录在 THIRD_PARTY_NOTICES.md。

## 发布安装包

一次性准备：`winget install JRSoftware.InnoSetup`（按用户安装即可）；如需命令行发布，再安装 GitHub CLI 并运行 `gh auth login`。

1. 修改 `pyproject.toml` 和 `src/saywell/__init__.py` 中的版本号（两处必须一致），在 `docs/releases/` 写好该版本的发布说明并提交。
2. 退出正在运行的好好说（打包会覆盖 `dist`），运行 `scripts\build_installer.ps1`。它会跑完测试、构建程序、检查程序目录中没有模型和密钥，然后生成 `release\Saywell-Setup-版本号.exe` 及对应的 `.sha256` 文件。
3. 在干净环境（Windows 沙盒或新的 Windows 用户）中安装、完成首次使用流程、再卸载一遍。
4. 打标签并发布：

```powershell
git tag v0.2.0
git push origin v0.2.0
gh release create v0.2.0 release\Saywell-Setup-0.2.0.exe release\Saywell-Setup-0.2.0.exe.sha256 --title "好好说 Saywell 0.2.0" --notes-file docs\releases\v0.2.0.md
```

也可以在 GitHub 网页的 Releases → Draft a new release 中选择标签、填写说明并上传这两个文件。

安装包按用户安装到 `%LOCALAPPDATA%\Programs\Saywell`，不需要管理员权限；安装和卸载时只关闭该目录中运行的程序，卸载只移除指向该目录的开机自启项，用户数据保留在 `%LOCALAPPDATA%\Saywell`。

v0.1.0 发布时英文名叫 Better Voice Input。安装脚本保留同一个 AppId，所以新安装包会原地升级旧版本（沿用旧的安装文件夹），并删除旧的 `BetterVoiceInput.exe`、“好好说”快捷方式，把指向该目录的旧开机自启项改成新名字。程序首次启动时把 `%LOCALAPPDATA%\BetterVoiceInput` 改名为 `Saywell` 并修正设置中指向旧文件夹的模型路径（旧版本仍在运行、文件夹被占用时暂时沿用旧文件夹，下次启动再迁移），把凭据管理器里 `better-voice-input` 下的 Key 移到 `saywell`，把旧名字的开机自启项换成指向当前程序的新项。安装向导使用 Inno Setup 官方仓库中社区维护的简体中文翻译（`installer/ChineseSimplified.isl`，对应 Inno Setup 6.7.3）。

## 设计原则

- 音频在本机识别；转写文本和相关词库发送至设置中选定的 API。
- 清理表达，不回答口述问题、不执行口述命令。
- 快捷键录音直接输入整理结果，疑点不阻断；API 整理失败时输入识别原文。原文和结果可在主窗口查看。
- 录音由用户主动开始和结束，停顿不自动提交。
- 只输入文字，不自动按回车发送。
- 录音仅在内存中，本地识别后释放；默认不保存文字历史，开启后最多保留最近 5 条（最长 7 天）。
- 精选模型保证实时听写；所有模型文件固定版本并校验摘要。
- 使用统计只记录按天汇总的数字，不记录内容，可关闭。

## 文档

- [实现规划与界面设计](PLAN.md)
- [使用指南](USER_GUIDE.md)
- [验证记录](VALIDATION.md)
- [第三方组件](../THIRD_PARTY_NOTICES.md)
