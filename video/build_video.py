r"""把静态画面 + 真实界面录屏 + 旁白合成为演示视频（MP4 / H.264 / 1080p）。

前置：
    1. video/make_slides.py        生成 6 张 1920×1080 静态画面
    2. video/record_demo.py        录出 raw-ui.webm 与 marks.json（时间轴）
用法：
    .\.tools\rptvenv\Scripts\python.exe video\build_video.py
    .\.tools\rptvenv\Scripts\python.exe video\build_video.py --probe   # 只报告时长与体积
产物：
    video/课径-演示视频.mp4

时长控制策略：
- 每个场景时长 = max(该场景旁白时长 + 0.8s, 画面最短停留)。
- 界面录屏场景按需变速（setpts）撑满旁白时长；静态画面则是定格。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import wave
from pathlib import Path

import imageio_ffmpeg

HERE = Path(__file__).resolve().parent
BUILD = HERE / ".build"
RAW = BUILD / "raw-ui.webm"
MARKS = BUILD / "marks.json"
VOICE_DIR = BUILD / "voice"
SEG_DIR = BUILD / "seg"
OUT = HERE / "课径-演示视频.mp4"
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

FPS = 30
W, H = 1920, 1080

# ---- 旁白（照 video/演示视频脚本.md 的顺序，按场景踩点）
NARRATION = {
    "s1": "这是课径，一个建立在 Dify 之上的问答应用。它回答的不是通用问题，而是两个学生每天都在问、"
          "却常常没人能一次说清的问题：我还差多少学分，以及这门课我到底能不能选。",
    "s2": "现有工具在这类问题上通常有三个硬伤。第一，文档被切片之后按相似度返回片段，"
          "跨页的学分表格被切成碎片，学生拿到的是一段文字，而不是一行结论。"
          "第二，学分交给大模型去求和，减法会错，超额还会被当成可以冲抵。"
          "第三，答案没有页码和行号，学生没法逐条核对，错了也说不清错在哪一步。",
    "s3": "我们的做法是把算和说分开。Dify 应用层负责意图分流、检索与呈现；"
          "自研服务 campus-kb 负责解析表格、混合检索、确定性核算与可复现评测。"
          "整个集成只用官方机制：compose 覆盖文件、环境变量、控制台接口，没有修改 Dify 的源码。",
    "s4": "先看真实界面。打开应用，填入已修课程代码，开始对话。",
    "s5": "第一个问题是还差多少学分。回答给出三个部分：按模块的缺口、结论数字，以及检索到的原始条款。"
          "学分完全由确定性代码按模块计算，超额不冲抵，方案外的课程不认定。",
    "s6": "第二个问题是先修。系统从整句提问里解析出目标课程，再对先修表达式求值。"
          "汽车电子控制要求电工电子、程序设计和汽车构造三门同时满足。回答直接点名还缺汽车构造，并给出课程代码。",
    "s7": "第三个问题不属于可核算类型。系统不硬算，而是回到知识库，给出最相关的原文条款，"
          "并标注页码、行号与匹配度，明确说明这是原文摘录，判定以教务认定为准。",
    "s8": "每一条结论都能复跑。一百四十九项测试全部通过，评测脚本一键出数。"
          "意图加权与课程名加权把 Recall at 1 从零点六九提到一点零，学分核算与先修判定的用例全部命中，"
          "报告里的每个数字都对应一条可执行的命令。",
    "s9": "开放成果包括四部分：Apache 协议开源的服务与部署脚本、可一键导入的工作流 DSL、"
          "内核随包分发的 dify 工具插件，以及 CC BY 的合成语料与评测用例。"
          "合规上，Dify 的修改版协议两条附加条件都满足，没有引入 AGPL 组件，仓库里也没有任何真实学生数据。",
    "s10": "课径要做的，是把培养方案从能搜到，做到能算清、能核对。谢谢观看。",
}

# ---- 场景：(id, 画面, 源, 最短停留秒)
SCENES = [
    ("s1", "image", "slide-1-title.png", 6.0),
    ("s2", "image", "slide-2-pain.png", 8.0),
    ("s3", "image", "slide-3-arch.png", 8.0),
    ("s4", "clip", ("apps-list", "chat-ready"), 6.0),
    ("s5", "clip", ("q1-send", "q1-done"), 8.0),
    ("s6", "clip", ("q2-send", "q2-done"), 8.0),
    ("s7", "clip", ("q3-send", "q3-done"), 8.0),
    ("s8", "image", "slide-4-metrics.png", 8.0),
    ("s9", "image", "slide-5-open.png", 8.0),
    ("s10", "image", "slide-6-end.png", 5.0),
]

PAD = 0.8
ZOOM_SCENES = {"s5", "s6", "s7"}
MARKS_DATA: dict = {}


def run(cmd: list[str], what: str) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or "")[-1500:]
        raise SystemExit(f"{what} 失败（exit={proc.returncode}）:\n{tail}")


def tts(text: str, out_wav: Path) -> float:
    """用 Windows SAPI（中文 Huihui）合成旁白，返回时长秒。

    注意两点（都踩过）：
    - PowerShell 5.1 的 SpeechSynthesizer **没有 SpeakOnce 方法**，必须用 Speak；
    - 文本经 UTF-8 临时文件传入，避免命令行编码与引号问题。
    """
    if out_wav.exists():
        out_wav.unlink()
    txt = out_wav.with_suffix(".txt")
    txt.write_text(text, encoding="utf-8")
    ps = f"""
Add-Type -AssemblyName System.Speech
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(44100, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SelectVoice('Microsoft Huihui Desktop')
$s.Rate = -1
$s.SetOutputToWaveFile('{out_wav}', $fmt)
$s.Speak((Get-Content -Raw -Encoding UTF8 '{txt}'))
$s.SetOutputToNull()
$s.Dispose()
"""
    proc = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                          capture_output=True, text=True)
    if proc.returncode != 0 or not out_wav.exists() or out_wav.stat().st_size == 0:
        raise SystemExit(f"TTS 失败: {proc.stderr[-800:]}")
    with wave.open(str(out_wav), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def clip_span(marks: dict, key: tuple[str, str]) -> tuple[float, float]:
    return marks[key[0]], marks[key[1]]


def build_segment(scene_id: str, kind: str, source, dur: float, factor: float) -> Path:
    seg = SEG_DIR / f"{scene_id}.mp4"
    if kind == "image":
        cmd = [
            FFMPEG, "-y", "-loop", "1", "-i", str(BUILD / source),
            "-t", f"{dur:.3f}", "-r", str(FPS),
            "-vf", f"scale={W}:{H}:flags=lanczos,format=yuv420p",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            str(seg),
        ]
    else:
        start, end = clip_span(MARKS_DATA, source)  # type: ignore[arg-type]
        # 慢放：setpts 放大时间戳，再用 -t 截到目标时长
        cmd = [
            FFMPEG, "-y", "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
            "-i", str(RAW),
            # 问答场景裁到对话区再放大（1080p 下文字才看得清）；导航场景保留完整界面
            "-vf", (f"crop=1380:776:520:40,setpts=PTS*{factor:.4f},fps={FPS},"
                    f"scale={W}:{H}:flags=lanczos,format=yuv420p"
                    if scene_id in ZOOM_SCENES else
                    f"setpts=PTS*{factor:.4f},fps={FPS},scale={W}:{H}:flags=lanczos,format=yuv420p"),
            "-t", f"{dur:.3f}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            str(seg),
        ]
    run(cmd, f"生成场景 {scene_id}")
    return seg


def build_audio(scene_durs: list[tuple[str, float]]) -> Path:
    """每个场景一段旁白，尾部补静音到场景时长，再拼接成整轨。"""
    pieces = []
    for sid, dur in scene_durs:
        voice = VOICE_DIR / f"{sid}.wav"
        padded = SEG_DIR / f"{sid}-audio.wav"
        cmd = [
            FFMPEG, "-y", "-i", str(voice),
            "-af", f"apad,atrim=0:{dur:.3f},asetpts=N/SR/TB",
            "-ar", "44100", "-ac", "2", str(padded),
        ]
        run(cmd, f"旁白对齐 {sid}")
        pieces.append(padded)
    list_file = SEG_DIR / "audio-list.txt"
    list_file.write_text("\n".join(f"file '{p.as_posix()}'" for p in pieces) + "\n",
                         encoding="utf-8")
    track = SEG_DIR / "narration.wav"
    run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
         "-c", "copy", str(track)], "拼接旁白")
    return track


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="合成演示视频")
    ap.add_argument("--probe", action="store_true", help="只检查产物")
    args = ap.parse_args(argv)

    if not args.probe:
        if not RAW.exists() or not MARKS.exists():
            raise SystemExit("缺少 raw-ui.webm / marks.json，先跑 video/record_demo.py")
        global MARKS_DATA
        marks = json.loads(MARKS.read_text(encoding="utf-8"))
        MARKS_DATA = marks
        for d in (VOICE_DIR, SEG_DIR):
            d.mkdir(parents=True, exist_ok=True)

        print("语音合成与时长规划：")
        scene_durs: list[tuple[str, float]] = []
        for sid, kind, src, minimum in SCENES:
            voice = VOICE_DIR / f"{sid}.wav"
            if not voice.exists():
                nd = tts(NARRATION[sid], voice)
            else:
                with wave.open(str(voice), "rb") as w:
                    nd = w.getnframes() / float(w.getframerate())
            dur = max(nd + PAD, minimum)
            scene_durs.append((sid, round(dur, 2)))
            print(f"  {sid:4s} 旁白 {nd:6.2f}s -> 场景 {dur:6.2f}s")

        total = sum(d for _, d in scene_durs)
        print(f"  合计 {total:.1f}s")
        if not (180 <= total <= 300):
            print(f"  ⚠️ 时长 {total:.0f}s 超出 3–5 分钟，请调整旁白长度", file=sys.stderr)

        print("生成画面片段：")
        segs = []
        for (sid, kind, src, _), (_, dur) in zip(SCENES, scene_durs):
            factor = 1.0
            if kind == "clip":
                start, end = clip_span(marks, src)  # type: ignore[arg-type]
                factor = dur / max(end - start, 0.1)
            segs.append(build_segment(sid, kind, src, dur, factor))
            print(f"  {sid:4s} ok")

        list_file = SEG_DIR / "video-list.txt"
        list_file.write_text("\n".join(f"file '{p.as_posix()}'" for p in segs) + "\n",
                             encoding="utf-8")
        video_track = SEG_DIR / "video.mp4"
        run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
             "-c", "copy", str(video_track)], "拼接画面")

        audio_track = build_audio(scene_durs)

        OUT.unlink(missing_ok=True)
        run([
            FFMPEG, "-y", "-i", str(video_track), "-i", str(audio_track),
            "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart", "-shortest", str(OUT),
        ], "封装成片")

    if not OUT.exists():
        raise SystemExit("成片不存在")
    dur, size_mb = probe(OUT)
    ok = 180 <= dur <= 300 and size_mb <= 300
    print(f"成片：{OUT}")
    print(f"时长：{dur:.1f}s（门禁 180–300s）")
    print(f"体积：{size_mb:.1f} MB（门禁 ≤300MB）")
    print("A5 预检：", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def probe(path: Path) -> tuple[float, float]:
    proc = subprocess.run([FFMPEG, "-i", str(path)], capture_output=True, text=True)
    dur = 0.0
    for line in (proc.stderr or "").splitlines():
        if "Duration:" in line:
            hh, mm, ss = line.split("Duration:")[1].split(",")[0].strip().split(":")
            dur = int(hh) * 3600 + int(mm) * 60 + float(ss)
            break
    return dur, path.stat().st_size / (1024 * 1024)


if __name__ == "__main__":
    sys.exit(main())
