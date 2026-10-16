from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Callable

import httpx

from .cleanup import Cancelled


@dataclass(frozen=True)
class ModelFile:
    name: str
    url: str
    size: int
    digest: str
    git_blob: bool = False


@dataclass(frozen=True)
class AsrModel:
    id: str
    name: str
    kind: str
    # Sub-folder under the download location. SenseVoice keeps its original
    # place directly in the models folder so existing installs stay untouched.
    folder: str
    tagline: str
    strengths: tuple[str, ...]
    caveat: str
    languages: str
    files: tuple[ModelFile, ...]
    # Keyword arguments for sherpa_onnx.OfflineRecognizer.from_<kind>: model files
    # (paths inside the model folder) and fixed options.
    inputs: tuple[tuple[str, str], ...] = ()
    options: tuple[tuple[str, str | bool], ...] = ()
    homepage: str = ""
    # Measured seconds to recognize 60 s of speech on the reference PC (docs/VALIDATION.md).
    seconds_per_minute: float = 0.0
    # Plaza (模型广场) filter group; empty for the curated models.
    category: str = ""

    @property
    def size(self) -> int:
        return sum(file.size for file in self.files)

    @property
    def speed_label(self) -> str:
        seconds = self.seconds_per_minute
        return f"60 秒语音约 {seconds:.1f} 秒" if seconds < 10 else f"60 秒语音约 {seconds:.0f} 秒"

    @property
    def size_label(self) -> str:
        megabytes = self.size / 1_000_000
        return f"约 {megabytes / 1000:.1f} GB" if megabytes >= 950 else f"约 {round(megabytes, -1):.0f} MB"


def _hf(repo: str, revision: str) -> Callable[..., ModelFile]:
    def file(name: str, size: int, digest: str, git_blob: bool = False) -> ModelFile:
        return ModelFile(name, f"https://huggingface.co/{repo}/resolve/{revision}/{name}", size, digest, git_blob)

    return file


VAD = ModelFile(
    "silero_vad.onnx",
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx",
    643854,
    "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6",
)

_sense = _hf("csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17", "2365bae")
_xasr = _hf(
    "csukuangfj2/sherpa-onnx-x-asr-zipformer-transducer-zh-en-punct-int8-2026-06-03",
    "ce812cc05d858539a68dd42e86f4fd7a9ce1acf0",
)
_canary = _hf(
    "csukuangfj/sherpa-onnx-nemo-canary-180m-flash-en-es-de-fr-int8", "9077164e0d3dd1d5353743e89ceaa1d3a770838c"
)

SENSE_VOICE = AsrModel(
    id="sensevoice",
    name="SenseVoice Small",
    kind="sense_voice",
    folder="",
    tagline="均衡默认",
    strengths=("中文准确，自带标点", "支持中、英、日、韩、粤语", "体积小，老电脑也流畅"),
    caveat="中文里的英文词和人名偶尔听成近音词，例如 DeepSeek、王五。",
    languages="中 · 英 · 日 · 韩 · 粤",
    files=(
        _sense("model.int8.onnx", 239233841, "c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51"),
        _sense("tokens.txt", 315894, "2cfc92fc2ff26aaa690b7c01fd96b41109413881", True),
        VAD,
    ),
    inputs=(("model", "model.int8.onnx"), ("tokens", "tokens.txt")),
    options=(("language", "auto"), ("use_itn", True)),
    homepage="https://huggingface.co/FunAudioLLM/SenseVoiceSmall",
    seconds_per_minute=0.88,
)

X_ASR = AsrModel(
    id="xasr",
    name="X-ASR 中英",
    kind="transducer",
    folder="x-asr-zh-en",
    tagline="体积最小",
    strengths=("英文和中英混说更准", "自带标点，英文大小写规范", "精选模型中体积最小"),
    caveat="2026 年新发布，社区验证较少；个别中文近音词仍会出错。",
    languages="中 · 英",
    files=(
        _xasr(
            "encoder-epoch-99-avg-1.int8.onnx",
            161744450,
            "fe0198b52626f2a1012ebb6d6a7259c5137c4c964f3a524135e353f4687b5d14",
        ),
        _xasr(
            "decoder-epoch-99-avg-1.onnx", 11309084, "a9fe7c320337e6f510809c028a8accf074d45f42aab852e2d29830babc41a3f9"
        ),
        _xasr(
            "joiner-epoch-99-avg-1.int8.onnx",
            2581422,
            "aedb7fa697b2ab43f20499826fff7c997eea7d67db77be97769aeeeb726e63b3",
        ),
        _xasr("tokens.txt", 58806, "8ab5e8e441ec0c2028fa05b200ad1514d19b134e", True),
        VAD,
    ),
    inputs=(
        ("encoder", "encoder-epoch-99-avg-1.int8.onnx"),
        ("decoder", "decoder-epoch-99-avg-1.onnx"),
        ("joiner", "joiner-epoch-99-avg-1.int8.onnx"),
        ("tokens", "tokens.txt"),
    ),
    homepage="https://huggingface.co/GilgameshWind/X-ASR-zh-en",
    seconds_per_minute=0.79,
)

CANARY = AsrModel(
    id="canary",
    name="Canary 180M Flash",
    kind="nemo_canary",
    folder="canary-180m-flash",
    tagline="英文最强",
    strengths=(
        "英文准确率超过 Whisper-large-v3",
        "自带标点和大小写",
        "体积小",
    ),
    caveat="只识别英文（另支持德、法、西班牙语），说中文时请切回其他模型。",
    languages="英 · 德 · 法 · 西",
    files=(
        _canary("encoder.int8.onnx", 132678643, "7a75b4e2a5857a6dcc0819503bbe3fad66943db4a3ccf21d3f27c633667d303f"),
        _canary("decoder.int8.onnx", 74437848, "e41a2ab9c0c2fe81a1e8ade5a45fb02a74bc4db7d1f91b89a54a25e2cf79cba2"),
        _canary("tokens.txt", 53555, "da5e7fa593e5f2e3d7a6daa6688da7adebc8336a", True),
        VAD,
    ),
    inputs=(("encoder", "encoder.int8.onnx"), ("decoder", "decoder.int8.onnx"), ("tokens", "tokens.txt")),
    options=(("src_lang", "en"), ("tgt_lang", "en")),
    homepage="https://huggingface.co/nvidia/canary-180m-flash",
    seconds_per_minute=3.59,
)

# llama.cpp runs GGUF speech models such as Qwen3-ASR in a local llama-server process.
LLAMA_CPP = ModelFile(
    "llama-b11696-bin-win-cpu-x64.zip",
    "https://github.com/ggml-org/llama.cpp/releases/download/b11696/llama-b11696-bin-win-cpu-x64.zip",
    19575690,
    "1e7c47dff58467540d53e6cf302d83951bcb165afb252c2952ed3872190fc60d",
)
# The server and its libraries are unpacked here, inside the model folder, on first use.
ENGINE_DIR = "llama.cpp"
_qwen3 = _hf("ggml-org/Qwen3-ASR-0.6B-GGUF", "928ab958557df9aa2ef1c93e0e83c7ad0933fae2")

QWEN3_ASR = AsrModel(
    id="qwen3-asr",
    name="Qwen3-ASR 0.6B",
    kind="llama_server",
    folder="qwen3-asr-0.6b-q8",
    tagline="准确抗噪",
    strengths=("中文准确，自带标点", "嘈杂环境、语速快时也较稳", "支持 52 种语言和方言"),
    caveat="精选模型中体积最大；偶尔把“呃”听成“而”。",
    languages="多语言 · 方言",
    files=(
        _qwen3("Qwen3-ASR-0.6B-Q8_0.gguf", 804749248, "bca259818b50ca7c4c05e9bdb35a5dc04fa039653a6d6f3f0f331f96f6aa1971"),
        _qwen3(
            "mmproj-Qwen3-ASR-0.6B-Q8_0.gguf", 214392480, "41a342b5e4c514e968cb756de6cd1b7be39eff43c44c57a2ef5fc6522e36603d"
        ),
        LLAMA_CPP,
        VAD,
    ),
    inputs=(
        ("model", "Qwen3-ASR-0.6B-Q8_0.gguf"),
        ("mmproj", "mmproj-Qwen3-ASR-0.6B-Q8_0.gguf"),
        ("engine", LLAMA_CPP.name),
    ),
    homepage="https://huggingface.co/Qwen/Qwen3-ASR-0.6B",
    seconds_per_minute=4.16,
)

# ---------- 模型广场：精选模型之外的扩展选择 ----------

PLAZA_CATEGORIES = ("中文", "方言", "英文与多语言")

_firered_aed = _hf(
    "csukuangfj2/sherpa-onnx-fire-red-asr2-zh_en-int8-2026-02-26", "374cff185e952c40fcf2f6da972a3b6cf340608d"
)
_firered_ctc = _hf(
    "csukuangfj2/sherpa-onnx-fire-red-asr2-ctc-zh_en-int8-2026-02-25", "423be1acfdc5d8aaa5a485fb6263fe4ea8570b06"
)
_funasr_nano = _hf("csukuangfj/sherpa-onnx-funasr-nano-int8-2025-12-30", "6f16bd378457e13f36ccf3910df9017f96c346fb")
_paraformer = _hf("csukuangfj/sherpa-onnx-paraformer-zh-2024-03-09", "906992d326ebf0c5171cde675aa0902be9e5bc6c")
_dolphin = _hf(
    "csukuangfj/sherpa-onnx-dolphin-base-ctc-multi-lang-int8-2025-04-02", "1f3a53d0ecf658f8b0974e2cfde368eee40732fa"
)
_telespeech = _hf(
    "csukuangfj/sherpa-onnx-telespeech-ctc-int8-zh-2024-06-04", "d6ae7e614f84da611d9895867fdf4274fe07a00a"
)
_parakeet = _hf("csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8", "2bda32ec70b097a55adaa07d9a7173915b43cc78")
_cohere = _hf(
    "csukuangfj2/sherpa-onnx-cohere-transcribe-14-lang-int8-2026-04-01", "156a470cf08eefe706a0004f3c52d9ee567ca7a0"
)
_whisper = _hf("csukuangfj/sherpa-onnx-whisper-turbo", "2ca6ff69fc878651b770880507669577ac41c2ff")

FIRERED_AED = AsrModel(
    id="firered-aed",
    name="FireRedASR2-AED",
    kind="fire_red_asr",
    folder="firered-asr2-aed",
    tagline="中文最准",
    strengths=("中文准确率在开源模型中名列前茅", "普通话、方言、歌声都有专门评测", "支持中英混说"),
    caveat="速度很慢；不带标点，英文全为大写。",
    languages="中 · 英 · 方言",
    files=(
        _firered_aed(
            "encoder.int8.onnx", 817286833, "54048d66b6e8f3c80ea7ce95efe794587b0fd81d7271651d0decd3803852ae82"
        ),
        _firered_aed(
            "decoder.int8.onnx", 417291928, "b840ce7196ae4a14d05ae84bbf56082b6b61ccec5610fda907dddbcea37354ff"
        ),
        _firered_aed("tokens.txt", 79172, "50ac9cbd43d638b58f875e90b118ad8c9be51718", True),
        VAD,
    ),
    inputs=(("encoder", "encoder.int8.onnx"), ("decoder", "decoder.int8.onnx"), ("tokens", "tokens.txt")),
    category="中文",
    homepage="https://huggingface.co/FireRedTeam/FireRedASR2-AED",
    seconds_per_minute=17.12,
)

FIRERED_CTC = AsrModel(
    id="firered-ctc",
    name="FireRedASR2-CTC",
    kind="fire_red_asr_ctc",
    folder="firered-asr2-ctc",
    tagline="FireRed 提速版",
    strengths=("与 AED 版同源，速度快一倍", "中文准确率仍然很高", "支持中英混说"),
    caveat="仍然偏慢；不带标点，英文全为大写。",
    languages="中 · 英 · 方言",
    files=(
        _firered_ctc("model.int8.onnx", 775861420, "ca3dbabd82170110cc0b343c2890866d449984bc9cd92b9a18371ff80a81bb99"),
        _firered_ctc("tokens.txt", 79172, "50ac9cbd43d638b58f875e90b118ad8c9be51718", True),
        VAD,
    ),
    inputs=(("model", "model.int8.onnx"), ("tokens", "tokens.txt")),
    category="中文",
    homepage="https://github.com/FireRedTeam/FireRedASR2S",
    seconds_per_minute=8.31,
)

FUNASR_NANO = AsrModel(
    id="funasr-nano",
    name="Fun-ASR-Nano",
    kind="funasr_nano",
    folder="funasr-nano",
    tagline="大模型识别",
    strengths=("中、英、日，另支持 7 种方言和 26 种口音", "中文准确，自带标点", "远场、嘈杂场景表现好"),
    caveat="速度偏慢；中英夹杂的句子偶尔出错。",
    languages="中 · 英 · 日 · 方言",
    files=(
        _funasr_nano(
            "encoder_adaptor.int8.onnx", 237792748, "f36dea2e30fbc33b5db1d7a7265cc976c5e5586c77b042d5adb1ad27c72db422"
        ),
        _funasr_nano("llm.int8.onnx", 600356593, "dfbf9aa3be41bccc257587f151e15c63fbe1b549f2b517f5ccd5bdce3bf4322a"),
        _funasr_nano(
            "embedding.int8.onnx", 155584380, "95e61cd0c9c3b9543339a4cf973c95c116815e745ccc1e0285cbd81f76d18644"
        ),
        _funasr_nano("Qwen3-0.6B/merges.txt", 1671853, "31349551d90c7606f325fe0f11bbb8bd5fa0d7c7", True),
        _funasr_nano(
            "Qwen3-0.6B/tokenizer.json", 11422654, "aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4"
        ),
        _funasr_nano(
            "Qwen3-0.6B/vocab.json", 2776833, "ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910"
        ),
        VAD,
    ),
    inputs=(
        ("encoder_adaptor", "encoder_adaptor.int8.onnx"),
        ("llm", "llm.int8.onnx"),
        ("embedding", "embedding.int8.onnx"),
        ("tokenizer", "Qwen3-0.6B"),
    ),
    category="中文",
    homepage="https://huggingface.co/FunAudioLLM/Fun-ASR-Nano-2512",
    seconds_per_minute=7.82,
)

PARAFORMER_ZH = AsrModel(
    id="paraformer-zh",
    name="Paraformer 中文",
    kind="paraformer",
    folder="paraformer-zh",
    tagline="老牌中文",
    strengths=("阿里达摩院经典中文模型，久经验证", "中文和四川话测试都很准", "体积与默认模型相当"),
    caveat="不带标点，英文全为小写；AI 整理时会补上标点。",
    languages="中 · 少量英文",
    files=(
        _paraformer("model.int8.onnx", 227330205, "90bc03034ae1bef9575f8cc798cd1519c8be8aa9e8b458a033e32017ff4d584c"),
        _paraformer("tokens.txt", 75354, "a9216726314d8a0ea179afcede272f20a0c97bed", True),
        VAD,
    ),
    inputs=(("paraformer", "model.int8.onnx"), ("tokens", "tokens.txt")),
    category="中文",
    homepage="https://huggingface.co/funasr/paraformer-zh",
    seconds_per_minute=0.68,
)

DOLPHIN = AsrModel(
    id="dolphin-base",
    name="Dolphin Base",
    kind="dolphin_ctc",
    folder="dolphin-base",
    tagline="方言最全",
    strengths=("40 种东方语言 + 22 种中文方言", "体积只有约 100 MB，加载最快", "带部分标点"),
    caveat="不擅长英文；中文人名偶有近音字。",
    languages="多语言 · 方言",
    files=(
        _dolphin("model.int8.onnx", 103729802, "a3aa46c97f3f60f135ff949793cb05fabe7a0b3c484dc2e3cc699d354ee11b76"),
        _dolphin("tokens.txt", 504662, "868a04e8fd16545f873c46d53d4c7760d424431a", True),
        VAD,
    ),
    inputs=(("model", "model.int8.onnx"), ("tokens", "tokens.txt")),
    category="方言",
    homepage="https://huggingface.co/DataoceanAI/dolphin-base",
    seconds_per_minute=0.60,
)

TELESPEECH = AsrModel(
    id="telespeech",
    name="TeleSpeech",
    kind="telespeech_ctc",
    folder="telespeech",
    tagline="方言口音",
    strengths=("用方言数据集专门训练", "适合方言口音较重的普通话", "速度快"),
    caveat="不识别英文、不带标点；标准普通话不如其他模型准。",
    languages="中 · 方言",
    files=(
        _telespeech("model.int8.onnx", 340990949, "2bf52b42c971696dc02a2db94c52c8fc75fab321ac4cf10b5a2d6863a73a33b3"),
        _telespeech("tokens.txt", 66642, "b65434b25b7a11759d7f394f1fe73206a28c4e85", True),
        VAD,
    ),
    inputs=(("model", "model.int8.onnx"), ("tokens", "tokens.txt")),
    category="方言",
    homepage="https://github.com/Tele-AI/TeleSpeech-ASR",
    seconds_per_minute=1.30,
)

PARAKEET_V3 = AsrModel(
    id="parakeet-v3",
    name="Parakeet TDT 0.6B v3",
    kind="transducer",
    folder="parakeet-tdt-0.6b-v3",
    tagline="欧洲语言",
    strengths=("英语和 24 种欧洲语言，自动判断语种", "英文公开榜单排名靠前", "自带标点和大小写"),
    caveat="不识别中文。",
    languages="英 · 欧洲语言",
    files=(
        _parakeet("encoder.int8.onnx", 652184281, "acfc2b4456377e15d04f0243af540b7fe7c992f8d898d751cf134c3a55fd2247"),
        _parakeet("decoder.int8.onnx", 11845275, "179e50c43d1a9de79c8a24149a2f9bac6eb5981823f2a2ed88d655b24248db4e"),
        _parakeet("joiner.int8.onnx", 6355277, "3164c13fc2821009440d20fcb5fdc78bff28b4db2f8d0f0b329101719c0948b3"),
        _parakeet("tokens.txt", 93939, "fc43e1c723e262df60b70e1919614417162d1fe2", True),
        VAD,
    ),
    inputs=(
        ("encoder", "encoder.int8.onnx"),
        ("decoder", "decoder.int8.onnx"),
        ("joiner", "joiner.int8.onnx"),
        ("tokens", "tokens.txt"),
    ),
    options=(("model_type", "nemo_transducer"),),
    category="英文与多语言",
    homepage="https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3",
    seconds_per_minute=2.04,
)

COHERE_TRANSCRIBE = AsrModel(
    id="cohere-transcribe",
    name="Cohere Transcribe",
    kind="cohere_transcribe",
    folder="cohere-transcribe",
    tagline="英文榜单第一",
    strengths=("发布时英文开源识别榜单第一", "英文准确率高于 Canary 和 Whisper", "自带标点和大小写"),
    caveat="只识别英文，说中文时请切回其他模型；体积最大。",
    languages="英",
    files=(
        _cohere("encoder.int8.onnx", 3090822, "cf704f8cfa90e3f0a76f9ffc05998bdf00ba9ae983192c14a85a3a5eb008b367"),
        _cohere(
            "encoder.int8.onnx.data", 2731503072, "bcf1b7148c8518ae52df1ad2d2fc2b4e89261ea23e6c874eef1d9f55bcbaa4a3"
        ),
        _cohere("decoder.int8.onnx", 153250705, "8372ca6c8ff4db8b916ca3592f5c757a715e691b9edec751ba19b29fc854baf9"),
        _cohere("tokens.txt", 207437, "35dd289528904a08a07b2c12db98b73e8a871472", True),
        VAD,
    ),
    inputs=(("encoder", "encoder.int8.onnx"), ("decoder", "decoder.int8.onnx"), ("tokens", "tokens.txt")),
    # Cohere cannot detect the language; in Chinese mode it trails every other model here.
    options=(("language", "en"),),
    category="英文与多语言",
    homepage="https://huggingface.co/CohereLabs/cohere-transcribe-03-2026",
    seconds_per_minute=6.72,
)

WHISPER_TURBO = AsrModel(
    id="whisper-turbo",
    name="Whisper Turbo",
    kind="whisper",
    folder="whisper-turbo",
    tagline="名气最大",
    strengths=("支持 99 种语言", "自动判断语种", "英文准确，自带标点"),
    caveat="速度很慢；中文常漏字、不带标点。",
    languages="99 种语言",
    files=(
        _whisper(
            "turbo-encoder.int8.onnx", 674716297, "b02dcdf54f348741e93fe732b67d933c8dcb6735655f710640143081db38878b"
        ),
        _whisper(
            "turbo-decoder.int8.onnx", 361080764, "20accd02388482eb3a46bd615631adfdc85e1eb2c7db9ea3f02a40ffe6b81547"
        ),
        _whisper("turbo-tokens.txt", 816730, "a4edf0b719c10d28cd83f8c294449f99ff5d5dc0", True),
        VAD,
    ),
    inputs=(
        ("encoder", "turbo-encoder.int8.onnx"),
        ("decoder", "turbo-decoder.int8.onnx"),
        ("tokens", "turbo-tokens.txt"),
    ),
    options=(("language", ""), ("task", "transcribe")),
    category="英文与多语言",
    homepage="https://huggingface.co/openai/whisper-large-v3-turbo",
    seconds_per_minute=13.56,
)

PLAZA = (
    FIRERED_AED,
    FIRERED_CTC,
    FUNASR_NANO,
    PARAFORMER_ZH,
    DOLPHIN,
    TELESPEECH,
    PARAKEET_V3,
    COHERE_TRANSCRIBE,
    WHISPER_TURBO,
)

CATALOG = (SENSE_VOICE, X_ASR, QWEN3_ASR, CANARY)
# Every model the app can download and load: the curated models plus the plaza.
ALL_MODELS = CATALOG + PLAZA
DEFAULT_MODEL = SENSE_VOICE.id
# Kept for callers that predate the model catalog.
FILES = SENSE_VOICE.files


def get_model(model_id: str) -> AsrModel:
    for model in ALL_MODELS:
        if model.id == model_id:
            return model
    return SENSE_VOICE


class ModelError(RuntimeError):
    pass


def verify_file(path: Path, spec: ModelFile) -> bool:
    if not path.is_file() or path.stat().st_size != spec.size:
        return False
    hasher = hashlib.sha1() if spec.git_blob else hashlib.sha256()
    if spec.git_blob:
        hasher.update(f"blob {spec.size}\0".encode())
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest() == spec.digest


def models_ready(directory: Path, verify: bool = False, model: str | AsrModel = DEFAULT_MODEL) -> bool:
    spec = get_model(model) if isinstance(model, str) else model
    try:
        return all(
            verify_file(directory / file.name, file)
            if verify
            else (directory / file.name).is_file() and (directory / file.name).stat().st_size == file.size
            for file in spec.files
        )
    except OSError:
        return False


def download_model(
    model: AsrModel,
    directory: Path,
    progress: Callable[[int, str], None] | None = None,
    cancel: Event | None = None,
) -> None:
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise ModelError("无法创建模型文件夹，请选择有写入权限的位置。") from None
    total = model.size
    done = 0
    with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(60, connect=20)) as client:
        for spec in model.files:
            if cancel and cancel.is_set():
                raise Cancelled("已取消下载。")
            target = directory / spec.name
            if verify_file(target, spec):
                done += spec.size
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_name(f"{target.name}.{uuid.uuid4().hex}.part")
            received = 0
            try:
                with client.stream("GET", spec.url) as response:
                    response.raise_for_status()
                    with temp.open("wb") as file:
                        for chunk in response.iter_bytes(256 * 1024):
                            if cancel and cancel.is_set():
                                raise Cancelled("已取消下载。")
                            received += len(chunk)
                            if received > spec.size:
                                raise ModelError("下载大小与预期不符，请重试。")
                            file.write(chunk)
                            if progress:
                                progress(int((done + received) * 100 / total), spec.name)
                if not verify_file(temp, spec):
                    raise ModelError(f"{spec.name} 校验失败，请重新下载。")
                temp.replace(target)
                done += spec.size
            except httpx.HTTPError:
                raise ModelError("模型下载失败，请检查网络或代理后重试。") from None
            except OSError:
                raise ModelError("模型写入失败，请检查磁盘空间和文件夹权限。") from None
            finally:
                temp.unlink(missing_ok=True)
    if progress:
        progress(100, "模型已就绪")


def download_models(
    directory: Path, progress: Callable[[int, str], None] | None = None, cancel: Event | None = None
) -> None:
    download_model(SENSE_VOICE, directory, progress, cancel)
