# 开发说明

## 环境

需要 Windows 和 python.org 的 Python 3.12。模型、密钥、用户录音和本地打包产物不进入 Git 仓库。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m saywell.app
```

精确依赖版本保存在 `requirements-lock.txt`。运行 `scripts/build.ps1` 会先跑测试，再用 PyInstaller 构建目录式便携程序 `dist/Saywell/`。本地构建可双击 `启动好好说.cmd` 在托盘启动。

虚拟环境必须基于 python.org 的 Python。被其他工具改过标准库的 Python 在开发时一切正常，打包后却可能无法联网：0.3.0 就是用 Codex 自带的运行时打包的，它的 `ssl.py` 会读取一个不会被打包的证书文件，结果程序建立不了任何 HTTPS 连接。`scripts/build.ps1` 打包后会运行 `Saywell.exe --smoke`，其中会创建一次 HTTPS 客户端，失败即中止构建。

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

下载：Hugging Face 上的文件先连 hf-mirror.com 再连 huggingface.co，GitHub 上的文件（silero_vad.onnx、llama.cpp 发布包）先连 github.com，再依次加上 `GITHUB_PROXIES` 里的加速地址前缀；内容由固定摘要校验，镜像无法替换。连不上或 HTTP 错误立即换下一个地址，传输中断带 Range 从 `<文件名>.part` 续传，同一地址连续 `RETRIES` 次没有进展才换下一个。手动取消删除 `.part`，退出程序保留，下次接着下。“导入…”用 `find_models` 按文件名和大小找出所选文件夹里的模型，`download_model` 的 `sources` 先从这些文件夹复制（边复制边算摘要），复制坏的文件再联网下载。手动放进下载位置的模型文件夹（任意名字，往下两层以内）由 `Settings.with_found_models` 找出并记进 `model_paths`，原地使用；当前模型不可用时换成找到的第一个。主窗口在启动、窗口激活和录音前发现当前模型缺失时调用它，模型窗口在激活和更改下载位置时调用，扫描只看文件名和大小，几毫秒。

`kind="llama_server"` 的模型（目前是 Qwen3-ASR Q8_0 GGUF）由 `llama.py` 运行：llama.cpp 官方 Windows CPU 发布包作为模型文件之一固定版本下载并校验，首次使用时只把 `llama-server.exe`、DLL 和许可文件解压到模型文件夹的 `llama.cpp/`。程序在 127.0.0.1 的随机端口启动 llama-server，带随机 API Key，进程放进 Windows 作业对象，程序退出或崩溃时一并结束。识别仍走 `LocalRecognizer` 的分段逻辑，每段以 WAV 通过 `/v1/chat/completions` 的 `input_audio` 发送，回答中 `<asr_text>` 之后的部分是识别文字。

GPU 加速：`AsrModel.gpu=True` 的模型（Qwen3-ASR）同时带有 llama.cpp 的 Vulkan 发布包，解压到 `llama.cpp-vulkan/`。设置中开启 GPU 加速后，先用 Vulkan 版加 `-ngl 999 -lv 4` 启动；只有日志里出现 `using device …` 才算用上了显卡（没有可用显卡时它照样打印“offloaded N/N layers to GPU”，不能作为依据），否则关掉它、把日志留成 `llama-server-gpu.log`，改用 CPU 版启动，并在结果下方提示一次。有独显和核显时 llama.cpp 自己选独显。Vulkan 在新机器上第一次识别要编译着色器（RTX 5080 上约 5 秒），之后由驱动缓存。卡片上的速度仍是 CPU 实测值，不写 GPU 速度。

`seconds_per_minute` 是本机实测识别 60 秒语音的耗时，卡片上统一显示为“60 秒语音约 X 秒”。新增模型时补上分类、实测速度和模型主页，并实测能下载、校验、加载和识别；许可协议记录在 THIRD_PARTY_NOTICES.md。

## 快捷键

`shortcuts.py` 定义快捷键名称和规则，`windows.py` 负责监听，`app.py` 里的 `activate_hotkey` / `solo_*` 方法把两者接起来。

- **组合键和单键**（`Alt+X`、`Ctrl+Alt+K`、`F8`）：用 `RegisterHotKey` 注册，名称按 Ctrl、Alt、Shift、Win 的顺序保存。`shortcut_problem` 拒绝会影响打字的单键（字母、数字、空格、方向键，或只加 Shift）、常用编辑和系统快捷键，以及补输用的 `Alt+V`；F1–F24、Pause、ScrollLock 可以单独用。设置里的“自定义…”用 `HotkeyRecorder` 读取按键的 Windows 虚拟键码；Qt 在修饰键自身按下时会翻转它的修饰位，所以单独记一次。
- **单独按住的键**（`RightCtrl`、`RightAlt`、`CapsLock`，默认 `RightCtrl`）：`RegisterHotKey` 不能注册单独的修饰键，由 `SoloKeyHook` 的低级键盘钩子（`WH_KEYBOARD_LL`）监听。
  - 钩子放在自己的线程和消息循环里。Windows 每次按键都要等低级钩子返回，放在界面线程上，界面一卡就会让全系统打字卡顿。Windows 还会悄悄摘掉响应太慢的钩子，所以每 30 秒重新装一次。
  - 钩子只报告按下、松开、按住期间又按了别的键（组合）三种事件，计时在界面线程：按下后 `SOLO_HOLD_MS`（300 毫秒）内松开或按了别的键，就是普通按键，不录音。
  - 右 Ctrl 和右 Alt 照常传给其他程序，所以右 Ctrl+C 不受影响。单独按住再松开 Alt 会打开当前窗口的菜单栏、抢走输入框焦点，所以开始录音时补发一次未分配的虚拟键 0xE8（AutoHotkey 的 menu mask 同理）。实测：不补发时，一个带菜单栏的窗口在松开右 Alt 后菜单被激活；补发后不会。
  - CapsLock 被钩子拦下，不传给其他程序，否则按住说话会切换大小写；轻按或组合时由程序补发一次，所以轻按照常切换。
  - 程序自己发出的按键（粘贴的 Ctrl+V、0xE8、补发的 CapsLock）在 `dwExtraInfo` 带 `OWN_INPUT` 标记，钩子只跳过这些。远程桌面、屏幕键盘等其他注入的按键照常识别，也方便用 `SendInput` 实测钩子。
- **轻按模式**（`hold_to_talk=False`，设置里的“轻按开始/结束”）：单独的键在 `SOLO_TAP_MS`（500 毫秒）内松开、中间没按别的键才算一次轻按，松开时开始或结束录音；按得更久状态变为 `held`，键保留原来的功能（CapsLock 由程序补发）。右 Alt 在这个模式下由钩子线程在每次按下时立即补发 0xE8：松开的消息会先送到其他程序，等界面线程知道这是一次轻按时已来不及。组合键和单键本来就是按一下切换。
- 按住说话时，组合键用 `GetAsyncKeyState` 判断是否松开；单独按住的键看钩子记下的状态（CapsLock 被拦下后系统里的按键状态不会变）。设置窗口打开期间暂停录音快捷键，录新快捷键和在窗口里按右 Ctrl 都不会开始录音；保存时先试着启用新快捷键，失败则不改设置。
- 旧版本保存的 `Alt+X` 等名称照常解析，升级不改用户的选择；新安装默认是按住右 Ctrl。

## 输出格式

每个结果（AI 整理结果、整理失败时的识别原文、“文字”页的翻译）显示和输入前都经过 `core.finish_text`：

- 以中文为主的文字去掉中文字符和英文单词、数字之间的空格，英文单词之间的空格保留。是否以中文为主看有没有中文标点，没有时比较汉字数和英文单词数，所以纯英文和夹着一个中文名字的英文句子不变。日文假名按中文处理，韩文本来用空格分词，不处理。先处理空格，再去标点，因为要靠标点判断。
- 开启“去掉标点”（`no_punctuation`）时，先删掉单独成句的“嗯、呃”等，再去掉中文标点；两个英文单词或数字之间的标点换成空格。单个破折号和波浪号保留，它们也表示范围（3～5）。句中、句末的语气词交给 AI：提示词里加一条 `NO_PUNCTUATION_RULE`，本地不删，以免误删“酒吧”“干嘛”这类词。

## 词库

`vocabulary.py` 管个人词库和导入的专业词库。

- **导入**：`read_lexicon` 识别 txt（含权重、词频、jieba、搜狗/QQ/百度文本导出等写法）、csv（一列是词，两列是“错写,正写”）、json（词语列表或带 `text` 的对象列表）、Rime `.dict.yaml` 和搜狗 `.scel`。`.scel` 按 ciku / 深蓝词库转换公开的结构自行解析（名称在 0x130，拼音表从 0x1540 开始，其后是同音词组；末尾 `DELTBL` 段是删除词，不导入），在三个官方细胞词库上与 ciku 的结果逐词一致。导入后转成每行一个词（替换规则为“错写<Tab>正写”）的 UTF-8 文件，存到数据目录的 `lexicons/<id>.txt`；设置里的 `lexicons` 只记 id、名称、开关和词数。设置窗口里导入或删除的词库在保存后才生效，没保存的导入和被删除的词库文件由 `open_settings` 按 id 删除，不会扫描整个文件夹，设置读取失败时也不会误删。
- **比对**：词库太大，不能整份发送。`TermIndex` 用 pypinyin 把每个中文词的每种读音（多音字最多 16 种组合）建成无声调拼音索引，转写里读音相同、写法不同的片段就是候选（龚玉晨 → 龚彧宸）；英文词按去掉大小写和空格后的拼写比对（deep seek → DeepSeek），替换规则按原文精确匹配。专业词库的中文词三字起参与比对，个人词库两字起：两个字的通用词和日常用语撞音太多（6 万词的 THUOCL 在一段日常录音里能撞出 50 个，如“重要/中药”“费用/肺痈”）。索引按启用的词库文件缓存，启动和保存设置后在后台预建；6.2 万词约 1.3 秒，比对一句不到 1 毫秒。
- **使用**：AI 整理时发送整份个人词库加最多 30 个比对到的专业词（`GLOSSARY_LIMIT = 130`）。Qwen3-ASR 第一遍识别不带上下文；某段文字比对到词时，带上这些词作为 system 消息再识别这一段，由声音决定写法。不能每段都带上下文：只要有上下文，Qwen3-ASR 就会在部分段落的中英文之间加空格，与分隔符无关。sherpa-onnx 的模型不接受文字上下文，只靠整理这一步。
- **打包**：pypinyin 的字典是 JSON 数据文件，PyInstaller 没有现成钩子，由 `Saywell.spec` 的 `collect_data_files('pypinyin')` 收进程序；`--smoke` 会实际比对一次读音，缺文件时构建失败。

## 动效

界面控件都由 `ui.py` 自己绘制，状态变化用 `Tween` 过渡：它是一个会滑向新目标的数值，中途改目标时从当前位置接着走，连点两下开关会平滑折返而不是跳变。

- 缓动只用两种：`ease_out`（快起慢停，用于悬停、淡入淡出等不该回弹的变化）和 `spring`（阻尼弹簧，阻尼小于 1 时落位带一点回弹，用于开关滑块、分段控件的白色滑块、按钮松开、浮窗出现）。
- 开关：滑块弹过去，轨道颜色同时变蓝；按住时滑块朝目标方向拉长。按钮：按下时缩小约 3.5%，松开后弹回；选中状态（筛选标签、分段文字）渐变。
- 录音按钮从蓝色经过极光色里的紫、粉过渡到红色，麦克风缩小淡出、停止方块放大淡入；处理中转为灰底加旋转弧线。录音卡片边缘的光、底部浮窗的状态点颜色都是渐变切换。
- 屏幕底部的浮窗出现时上浮约 16 像素并淡入，消失时淡出下沉（`dismiss`）；消失途中再次显示会直接折返。
- 控件不可见时（窗口隐藏、设置窗口还没打开）状态直接到位，不播放动画。

## 发布安装包

一次性准备：`winget install JRSoftware.InnoSetup`（按用户安装即可）；如需命令行发布，再安装 GitHub CLI 并运行 `gh auth login`。

1. 修改 `pyproject.toml` 和 `src/saywell/__init__.py` 中的版本号（两处必须一致），在 `docs/releases/` 写好该版本的发布说明并提交。
2. 退出正在运行的好好说（打包会覆盖 `dist`），运行 `scripts\build_installer.ps1`。它会跑完测试、构建程序、检查程序目录中没有模型和密钥，然后生成 `release\Saywell-Setup-版本号.exe` 及对应的 `.sha256` 文件。
3. 在干净环境（Windows 沙盒或新的 Windows 用户）中安装、完成首次使用流程、再卸载一遍。
4. 打标签并发布：

```powershell
git tag v0.6.1
git push origin v0.6.1
gh release create v0.6.1 release\Saywell-Setup-0.6.1.exe release\Saywell-Setup-0.6.1.exe.sha256 --title "好好说 Saywell 0.6.1" --notes-file docs\releases\v0.6.1.md
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
