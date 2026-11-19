"""Claude: decide which English text is post-production caption vs product/scene
text (vision), and write humorous Taiwanese Traditional Chinese translations."""
from __future__ import annotations

import base64
import json
import os
from typing import Optional

import anthropic
import cv2
import numpy as np

from . import settings

TONES = {
    "humor": "幽默風趣、有梗、像台灣網紅/團購主在講話，可以適度玩梗、誇飾、口語（例如：根本、超扯、欸、也太、CP值爆表），但不能偏離原意",
    "natural": "自然流暢、口語化的台灣用語，親切但不刻意搞笑",
    "pro": "專業、精簡、有質感的品牌文案語氣",
}

SYSTEM = """你是台灣電商短影音的在地化專家兼爆款文案寫手。
任務：處理英文商品短影音中「後製加上去」的英文字幕/標題/文案，翻譯成台灣繁體中文。

規則：
1. 只翻譯後製疊加的文字（標題、字幕、促銷文案、行動呼籲等）。商品本身印刷的字、品牌 Logo、包裝上的字、型號規格、場景中招牌/螢幕上的字，一律 is_caption=false，不翻譯。
   判斷線索：後製字幕通常水平、描邊/陰影明顯、位置固定在畫面上方或下方、不隨商品透視變形；商品文字會隨商品移動/旋轉、印在物體表面上、通常是品牌名或型號。
2. 翻譯要「改寫」而不是直譯：符合台灣人的說法與用字（影片、品質、軟體、網路、手機、充電線…不要用中國用語如 視頻、質量、軟件、網絡），保留原意與賣點，數字價格單位保持正確（$19.99 可寫成 $19.99 或 美金19.99）。
3. 中文要精簡好讀，長度大約是英文字數的 0.5～0.8 倍字元數，單句盡量不超過 18 個中文字；原文有換行可在中文用「\\n」分兩行。
4. 不要使用 emoji、不要加引號、不要加註解。品牌名/產品名保持英文原樣。
5. 上下文要連貫：同一支影片的字幕可能是一句話被拆成好幾段，翻譯時要一起考慮語意。
6. 若 OCR 文字有明顯錯字（如漏字母），在 corrected_english 給出修正後的英文；沒錯就原樣填入。"""

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "is_caption": {"type": "boolean"},
                    "corrected_english": {"type": "string"},
                    "translation": {"type": "string"},
                },
                "required": ["id", "is_caption", "corrected_english", "translation"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


class AIUnavailable(RuntimeError):
    pass


def available() -> bool:
    return bool(settings.api_key())


def _client() -> anthropic.Anthropic:
    key = settings.api_key()
    if not key:
        raise AIUnavailable("尚未設定 Anthropic API Key（請到「設定」頁面輸入，或設定環境變數 ANTHROPIC_API_KEY）")
    return anthropic.Anthropic(api_key=key, max_retries=3, timeout=300)


def _create(**kw):
    """messages.create with server-side refusal fallback; plain call if the beta is rejected."""
    client = _client()
    try:
        return client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kw)
    except anthropic.BadRequestError as e:
        if "fallback" not in str(e).lower():
            raise
        return client.messages.create(**kw)


def _text_of(resp) -> str:
    if resp.stop_reason == "refusal":
        raise RuntimeError("AI 拒絕處理此內容")
    return next(b.text for b in resp.content if b.type == "text")


def _annotated_frames(analysis: dict, tracks: list[dict], max_frames: int = 8) -> list[bytes]:
    """Pick sample frames that cover all tracks and draw numbered boxes on them."""
    jpgs: dict[int, bytes] = analysis["_sample_jpgs"]
    sb = analysis["_scale_back"]
    samples = sorted(jpgs)
    if not samples:
        return []

    def visible(tr, f):
        return tr["start_frame"] <= f <= tr["end_frame"]

    todo = {tr["id"] for tr in tracks}
    chosen = []
    while todo and len(chosen) < max_frames:
        best = max(samples, key=lambda f: sum(1 for tr in tracks if tr["id"] in todo and visible(tr, f)))
        cover = {tr["id"] for tr in tracks if tr["id"] in todo and visible(tr, best)}
        if not cover:
            break
        chosen.append(best)
        todo -= cover
    out = []
    for f in sorted(chosen):
        img = cv2.imdecode(np.frombuffer(jpgs[f], np.uint8), cv2.IMREAD_COLOR)
        for tr in tracks:
            if not visible(tr, f):
                continue
            k = min(tr["keyframes"], key=lambda k: abs(k[0] - f))
            x, y, w, h = [v / sb for v in k[1:5]]
            p1, p2 = (int(x), int(y)), (int(x + w), int(y + h))
            cv2.rectangle(img, p1, p2, (0, 255, 255), 2)
            label = f"#{tr['id']}"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)
            ly = max(th + 4, int(y) - 4)
            cv2.rectangle(img, (int(x), ly - th - 4), (int(x) + tw + 6, ly + 2), (0, 0, 0), -1)
            cv2.putText(img, label, (int(x) + 3, ly - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        hh, ww = img.shape[:2]
        s = min(1.0, 1024 / max(hh, ww))
        if s < 1:
            img = cv2.resize(img, (int(ww * s), int(hh * s)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        out.append((f, buf.tobytes()))
    return out


def classify_and_translate(analysis: dict, info, tone: str = "humor", extra: str = "") -> dict[int, dict]:
    tracks = analysis["tracks"]
    if not tracks:
        return {}
    W, H, fps = info.width, info.height, info.fps
    desc = []
    for tr in tracks:
        k = tr["keyframes"][len(tr["keyframes"]) // 2]
        desc.append({
            "id": tr["id"],
            "ocr_text": tr["text"],
            "time": f"{tr['start_frame'] / fps:.2f}s ~ {(tr['end_frame'] + 1) / fps:.2f}s",
            "box_center_pct": [round((k[1] + k[3] / 2) / W * 100), round((k[2] + k[4] / 2) / H * 100)],
            "text_height_pct": round(k[5] / H * 100, 1),
            "tilt_deg": tr["angle"],
            "moves_pct": round(tr["motion"] * 100, 1),
        })
    content = []
    for f, jpg in _annotated_frames(analysis, tracks):
        content.append({"type": "text", "text": f"畫面 @ {f / fps:.2f}s（黃框編號 = 文字 id）"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                    "data": base64.standard_b64encode(jpg).decode()}})
    content.append({"type": "text", "text": (
        f"影片尺寸 {W}x{H}，長度 {info.duration:.1f} 秒。以下是 OCR 偵測到的英文文字（依出現時間排序）：\n"
        + "\n".join(json.dumps(d, ensure_ascii=False) for d in desc)
        + f"\n\n翻譯語氣：{TONES.get(tone, TONES['humor'])}。"
        + (f"\n額外要求：{extra}" if extra else "")
        + "\n請對每一個 id 回傳結果（is_caption=false 的 translation 填空字串）。")})
    resp = _create(
        model=settings.model(), max_tokens=16000, system=SYSTEM,
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": content}],
    )
    data = json.loads(_text_of(resp))
    return {int(it["id"]): it for it in data["items"]}


def translate_one(english: str, context: list[str], tone: str = "humor", instruction: str = "") -> str:
    schema = {"type": "object", "properties": {"translation": {"type": "string"}},
              "required": ["translation"], "additionalProperties": False}
    prompt = (f"整支影片的英文字幕（供上下文參考）：\n" + "\n".join(f"- {c}" for c in context) +
              f"\n\n請翻譯這一段：{english}\n語氣：{TONES.get(tone, TONES['humor'])}" +
              (f"\n使用者額外要求：{instruction}" if instruction else ""))
    resp = _create(
        model=settings.model(), max_tokens=4000, system=SYSTEM,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": prompt}],
    )
    return json.loads(_text_of(resp))["translation"].strip()


def heuristic_is_caption(tr: dict, H: int) -> bool:
    """Fallback when no AI: horizontal, reasonably large text that is either static
    or animated with a consistent trend (slide / zoom). Product text that drifts
    back and forth with the camera is rejected."""
    k = tr["keyframes"][0]
    if abs(tr["angle"]) >= 4 or k[5] < 0.022 * H or tr["n_obs"] < 2:
        return False
    if tr["motion"] < 0.012:
        return True
    kf = np.array(tr["keyframes"], float)
    if len(kf) < 4:
        return False
    f = kf[:, 0]
    series = [kf[:, 1] + kf[:, 3] / 2, kf[:, 2] + kf[:, 4] / 2, kf[:, 5]]
    trend = max(abs(np.corrcoef(f, s)[0, 1]) if np.std(s) > 1e-6 else 0 for s in series)
    return bool(trend > 0.9)
