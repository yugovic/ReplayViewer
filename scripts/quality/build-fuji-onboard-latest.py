#!/usr/bin/env python3
"""Build the local self-contained Fuji video/before/after CG comparison.

Usage: python scripts/quality/build-fuji-onboard-latest.py --manifest MANIFEST.json --output docs/fuji-onboard-comparison-2026-10-03.html

Images are manifest-relative and re-encoded without EXIF. Only allowlisted
metadata is embedded. This script does not fetch media or publish the result.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import math
import re
import sys
from pathlib import Path, PureWindowsPath

from PIL import Image, ImageOps

TELEMETRY_KEYS = ("speedKmh", "aps", "brake", "steer", "accx", "accy")
PRIVATE_TEXT = re.compile(
    r"(?i)(?:[a-z]:[\\/]|\\\\[^\\]|/(?:users|home|mnt|tmp|private|var)/"
    r"|(?:api[_-]?key|access[_-]?token|authorization)\s*[:=]"
    r"|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)"
)


def public_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field}: expected non-empty text")
    if PRIVATE_TEXT.search(value):
        raise ValueError(f"{field}: absolute source path or credential-like metadata is not allowed")
    if len(value) > 4000:
        raise ValueError(f"{field}: metadata is too long")
    return value.strip()


def number(value: object, field: str, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field}: expected a finite number")
    result = float(value)
    if not math.isfinite(result) or (nonnegative and result < 0):
        raise ValueError(f"{field}: invalid number")
    return result


def image_file(base: Path, value: object, field: str) -> Path:
    name = public_text(value, field)
    if Path(name).is_absolute() or PureWindowsPath(name).is_absolute() or ":" in name:
        raise ValueError(f"{field}: image paths must be manifest-relative")
    resolved = (base / name).resolve()
    try:
        resolved.relative_to(base.resolve())
    except ValueError as exc:
        raise ValueError(f"{field}: image path escapes the manifest directory") from exc
    if not resolved.is_file():
        raise ValueError(f"{field}: image does not exist: {name}")
    return resolved


def jpeg_uri(file: Path, max_width: int, quality: int) -> str:
    with Image.open(file) as original:
        original.load()
        image = ImageOps.exif_transpose(original).convert("RGB")
        if image.width > max_width:
            image = image.resize(
                (max_width, round(image.height * max_width / image.width)),
                Image.Resampling.LANCZOS,
            )
        # Fresh pixels remove EXIF, filenames and other source metadata.
        clean = Image.new("RGB", image.size)
        clean.paste(image)
        data = io.BytesIO()
        clean.save(data, format="JPEG", quality=quality, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(data.getvalue()).decode("ascii")


def telemetry(value: object, field: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{field}: expected an object")
    result = {}
    for key in TELEMETRY_KEYS:
        sample = value.get(key, value.get("speed") if key == "speedKmh" else None)
        result[key] = None if sample is None else number(sample, f"{field}.{key}")
    return result


def provenance(value: object) -> list[dict]:
    if not isinstance(value, list) or not value:
        raise ValueError("provenance: source permission and SHA-256 are required")
    result = []
    for i, source in enumerate(value):
        if not isinstance(source, dict):
            raise ValueError(f"provenance[{i}]: expected an object")
        name = public_text(source.get("name"), f"provenance[{i}].name")
        if "/" in name or "\\" in name:
            raise ValueError(f"provenance[{i}].name: use a filename, not a source path")
        sha = public_text(source.get("sha256"), f"provenance[{i}].sha256")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", sha):
            raise ValueError(f"provenance[{i}].sha256: expected a 64-digit SHA-256")
        result.append({
            "raceId": public_text(source.get("raceId"), f"provenance[{i}].raceId"),
            "name": name, "sha256": sha.lower(),
            "permission": public_text(source.get("permission"), f"provenance[{i}].permission"),
        })
    if len({source["raceId"] for source in result}) != len(result):
        raise ValueError("provenance: raceId entries must be unique")
    return result


def public_qa(value: object) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("qa: expected an object")
    result = {}
    for key in ("captureMethod", "beforeRevision", "afterRevision", "gpsMode", "videoExtraction"):
        if key in value:
            result[key] = public_text(value[key], f"qa.{key}")
    for key in ("captureFps", "prerollSeconds"):
        if key in value:
            result[key] = number(value[key], f"qa.{key}", nonnegative=True)
    if "orientationDifferenceDegrees" in value:
        orientation = value["orientationDifferenceDegrees"]
        if isinstance(orientation, list):
            result["orientationDifferences"] = [
                {
                    "id": public_text(item.get("id"), f"qa.orientationDifferenceDegrees[{i}].id"),
                    "degrees": number(item.get("degrees"), f"qa.orientationDifferenceDegrees[{i}].degrees", nonnegative=True),
                }
                for i, item in enumerate(orientation)
            ]
        else:
            result["orientationDifferenceDegrees"] = number(
                orientation, "qa.orientationDifferenceDegrees", nonnegative=True
            )
    for key in ("beforeSourceSha256", "afterSourceSha256"):
        if key in value:
            sha = public_text(value[key], f"qa.{key}")
            if not re.fullmatch(r"[0-9a-fA-F]{64}", sha):
                raise ValueError(f"qa.{key}: expected a SHA-256")
            result[key] = sha.lower()
    if "notes" in value:
        notes = value["notes"]
        if isinstance(notes, str):
            notes = [notes]
        if not isinstance(notes, list):
            raise ValueError("qa.notes: expected a text list")
        result["notes"] = [public_text(note, f"qa.notes[{i}]") for i, note in enumerate(notes)]
    return result


def prepare(manifest: dict, base: Path, max_width: int, quality: int) -> dict:
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be a JSON object")
    definition = manifest.get("comparisonDefinition")
    if isinstance(definition, dict):
        definition = " / ".join(
            f"{public_text(str(k), 'comparisonDefinition key')}: "
            f"{public_text(str(v), 'comparisonDefinition value')}"
            for k, v in definition.items()
        )
    result = {
        "createdAt": public_text(manifest.get("createdAt"), "createdAt"),
        "baselineLabel": public_text(manifest.get("baselineLabel"), "baselineLabel"),
        "latestLabel": public_text(manifest.get("latestLabel"), "latestLabel"),
        "comparisonDefinition": public_text(definition, "comparisonDefinition"),
        "provenance": provenance(manifest.get("provenance")),
        "qa": public_qa(manifest.get("qa")), "shots": [],
    }
    shots = manifest.get("shots")
    if not isinstance(shots, list) or not shots:
        raise ValueError("shots: at least one scene is required")
    known_races = {source["raceId"] for source in result["provenance"]}
    seen = set()
    for i, shot in enumerate(shots):
        prefix = f"shots[{i}]"
        if not isinstance(shot, dict):
            raise ValueError(f"{prefix}: expected an object")
        public = {
            key: public_text(shot.get(key), f"{prefix}.{key}")
            for key in ("id", "raceId", "date", "label")
        }
        if public["id"] in seen:
            raise ValueError(f"{prefix}.id: duplicate scene identifier")
        seen.add(public["id"])
        if public["raceId"] not in known_races:
            raise ValueError(f"{prefix}.raceId: source provenance is missing")
        for key in ("lap", "overlayLap"):
            value = number(shot.get(key), f"{prefix}.{key}", nonnegative=True)
            if value != int(value):
                raise ValueError(f"{prefix}.{key}: expected a whole lap number")
            public[key] = int(value)
        for key in ("time", "delta", "videoPtsSeconds", "beforePlaybackSeconds", "afterPlaybackSeconds"):
            public[key] = number(shot.get(key), f"{prefix}.{key}", nonnegative=True)
        if "decodedFramePtsSeconds" in shot:
            public["decodedFramePtsSeconds"] = number(
                shot["decodedFramePtsSeconds"], f"{prefix}.decodedFramePtsSeconds", nonnegative=True
            )
        if abs(public["beforePlaybackSeconds"] - public["time"]) > 0.002:
            raise ValueError(f"{prefix}: before playback must equal displayed lap clock T")
        if abs(public["afterPlaybackSeconds"] + public["delta"] - public["time"]) > 0.002:
            raise ValueError(f"{prefix}: after playback must equal T minus delta")
        media = {"video": image_file(base, shot.get("video"), f"{prefix}.video")}
        cameras = shot.get("cameras")
        if not isinstance(cameras, dict):
            raise ValueError(f"{prefix}.cameras: cockpit and chase captures are required")
        for camera in ("cockpit", "chase"):
            pair = cameras.get(camera)
            if not isinstance(pair, dict):
                raise ValueError(f"{prefix}.cameras.{camera}: before/after captures are required")
            media[camera] = {
                state: image_file(base, pair.get(state), f"{prefix}.cameras.{camera}.{state}")
                for state in ("before", "after")
            }
        values = shot.get("telemetry")
        if not isinstance(values, dict):
            raise ValueError(f"{prefix}.telemetry: before/after values are required")
        public["telemetry"] = {
            state: telemetry(values.get(state), f"{prefix}.telemetry.{state}")
            for state in ("before", "after")
        }
        alignment = shot.get("alignment")
        if not isinstance(alignment, dict):
            raise ValueError(f"{prefix}.alignment: yaw lag and uncertainty are required")
        public["alignment"] = {
            "imageYawLagSeconds": number(
                alignment.get("imageYawLagSeconds"), f"{prefix}.alignment.imageYawLagSeconds"
            ),
            "uncertaintySeconds": number(
                alignment.get("uncertaintySeconds"), f"{prefix}.alignment.uncertaintySeconds",
                nonnegative=True,
            ),
        }
        public["video"] = jpeg_uri(media["video"], max_width, quality)
        public["cameras"] = {
            camera: {
                state: jpeg_uri(media[camera][state], max_width, quality)
                for state in ("before", "after")
            }
            for camera in ("cockpit", "chase")
        }
        result["shots"].append(public)
    return result

HTML = r"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark"><title>富士｜実車載・修正前CG・修正後CGの最新比較</title>
<style>
:root{color-scheme:dark;--bg:#101214;--panel:#191c20;--line:#353b43;--text:#ecedf0;--muted:#a9afb8;--accent:#d7e7b4;font:15px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans JP",sans-serif;background:var(--bg);color:var(--text)}
*{box-sizing:border-box}body{margin:0}main{max-width:1660px;margin:auto;padding:42px 3.5vw 60px}h1{font-size:clamp(27px,3.5vw,45px);line-height:1.3;letter-spacing:-.04em;margin:14px 0 20px}h2{font-size:22px;line-height:1.4;margin:34px 0 14px}h3{font-size:16px;margin:0}p{margin:12px 0;color:var(--muted)}a{color:var(--accent);text-underline-offset:4px}button,select{font:inherit}button{cursor:pointer}button:focus-visible,select:focus-visible,a:focus-visible,summary:focus-visible{outline:3px solid var(--accent);outline-offset:4px}.kicker{font:11px/1.5 ui-monospace,Consolas,monospace;letter-spacing:.15em;color:#c2cbd7}.header-meta{display:flex;gap:10px;flex-wrap:wrap;align-items:center}.badge{font-size:11px;border:1px solid var(--line);border-radius:99px;padding:3px 10px;color:var(--muted)}.intro{max-width:1140px;font-size:16px;color:var(--text)}.summary{display:grid;grid-template-columns:1.1fr 1fr;gap:26px;padding:22px 24px;border:1px solid var(--line);border-radius:12px;background:var(--panel)}.summary h2{margin:0 0 10px}.summary p{margin:0 0 10px}.limits{border-left:1px solid var(--line);padding-left:24px;font-size:13px}.limits strong{color:var(--text);font-weight:500}.links{display:flex;gap:20px;flex-wrap:wrap;margin:18px 0 26px;font-size:13px}.comparison{border:1px solid var(--line);border-radius:12px;background:var(--panel);overflow:hidden}.controls{padding:20px 22px;border-bottom:1px solid var(--line)}.row{display:flex;gap:14px;flex-wrap:wrap;align-items:center;justify-content:space-between}.day-tabs{display:flex;gap:7px;flex-wrap:wrap}.day-tabs button,.scene-tabs button{background:transparent;color:var(--muted);border:1px solid var(--line);border-radius:7px;padding:8px 13px;line-height:1.3}.day-tabs button[aria-pressed=true]{background:var(--accent);border-color:var(--accent);color:#1c2413}.scene-tabs{display:flex;gap:7px;flex-wrap:wrap;margin-top:16px}.scene-tabs button{min-width:106px;text-align:left;font-size:13px}.scene-tabs button span{display:block;font-size:11px;color:inherit;opacity:.7;max-width:170px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.scene-tabs button[aria-pressed=true]{background:#303730;border-color:#7c8c69;color:#e3edda}.camera{display:flex;gap:9px;align-items:center;font-size:12px;color:var(--muted)}select{background:#20242a;color:var(--text);border:1px solid var(--line);border-radius:7px;padding:8px 10px}.scene-description{padding:18px 22px 16px}.scene-heading{display:flex;gap:12px;flex-wrap:wrap;align-items:baseline}.scene-heading h3{font-size:20px;font-weight:550}.clock{font:15px/1.3 ui-monospace,Consolas,monospace;color:var(--accent)}.scene-description p{font-size:12px;margin:7px 0 0}.comparison-scroll{overflow-x:auto;overscroll-behavior-inline:contain;scrollbar-color:#58616c var(--panel)}.triptych{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));min-width:930px;gap:1px;background:var(--line)}figure{margin:0;background:var(--panel)}figcaption{padding:13px 16px;font-size:13px;font-weight:550;display:flex;justify-content:space-between;gap:12px;align-items:center}.index{font:11px ui-monospace,Consolas,monospace;color:var(--muted)}.image-button{display:block;width:100%;padding:0;border:0;background:#090a0b;cursor:zoom-in;aspect-ratio:16/9;overflow:hidden}.image-button img{display:block;width:100%;height:100%;object-fit:contain}.image-button:focus-visible{outline-offset:-4px}.image-meta{padding:12px 16px 15px;font:11px/1.7 ui-monospace,Consolas,monospace;color:var(--muted)}.comparison-help{padding:14px 22px;color:var(--muted);font-size:12px;border-top:1px solid var(--line)}.scroll-hint{display:none;color:var(--accent);margin-bottom:4px}.section-head{display:flex;gap:18px;align-items:baseline;flex-wrap:wrap;justify-content:space-between}.section-head p{font-size:12px;margin:0}.data-scroll{overflow-x:auto;border:1px solid var(--line);border-radius:12px}table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:12px 18px;border-bottom:1px solid var(--line);text-align:left}th{color:var(--muted);font-size:11px;font-weight:500;background:var(--panel)}td.numeric{font:13px ui-monospace,Consolas,monospace;white-space:nowrap}tbody tr:last-child td{border-bottom:0}.delta{color:var(--accent)}.fineprint{font-size:12px;max-width:1150px}.method{display:grid;grid-template-columns:1fr 1fr;gap:16px}.method article{border:1px solid var(--line);border-radius:12px;padding:20px 22px;background:var(--panel)}.method article p{font-size:13px;margin:8px 0 0}.formula{font:13px/1.9 ui-monospace,Consolas,monospace;color:#c2cbd7;margin-top:12px}.notice{border-left:3px solid #778864;padding:12px 18px;margin:22px 0;background:#1c221b;color:#c1cbb6;font-size:13px}.source{margin-top:24px;border:1px solid var(--line);border-radius:12px;padding:18px 22px;background:var(--panel)}summary{cursor:pointer;font-size:13px}.source-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px;padding-top:17px}.source-item{font-size:12px;color:var(--muted)}.source-item strong{color:var(--text);font-weight:500}.sha{font:10px/1.9 ui-monospace,Consolas,monospace;word-break:break-all;display:block;margin-top:6px;color:#8993a1}.qa{margin:15px 0 0;padding-left:18px;color:var(--muted);font-size:12px}.qa li{margin:5px 0}footer{margin-top:35px;padding-top:20px;border-top:1px solid var(--line);font-size:11px;color:var(--muted)}dialog{max-width:96vw;width:min(1500px,96vw);max-height:96vh;padding:0;color:var(--text);background:#101214;border:1px solid #59636f;border-radius:9px}dialog::backdrop{background:#000d}.lightbox-head{padding:12px 16px;display:flex;gap:16px;align-items:center;justify-content:space-between;font-size:13px}.lightbox-head button{color:var(--text);background:#252a30;border:1px solid #4b545e;border-radius:6px;padding:6px 12px;font-size:12px}dialog img{display:block;width:100%;height:auto;max-height:83vh;object-fit:contain;background:#08090a}.lightbox-note{padding:8px 16px;font-size:11px;color:var(--muted)}noscript p{padding:20px;background:#322a1c;color:#f0dab1}
@media(max-width:900px){main{padding:26px 20px 44px}.summary{grid-template-columns:1fr;gap:14px;padding:18px 20px}.limits{border-left:0;border-top:1px solid var(--line);padding:14px 0 0}.method{grid-template-columns:1fr}.scroll-hint{display:block}.row{gap:16px}.controls{padding:17px}.scene-description{padding:16px 17px}.comparison-help{padding:12px 17px}h2{font-size:21px}}
@media(max-width:560px){main{padding:23px 14px 40px}h1{font-size:29px}.source-list{grid-template-columns:1fr}.camera{width:100%;justify-content:space-between}.scene-tabs button{min-width:92px;padding:8px 10px}.scene-tabs{gap:6px}.links{gap:12px;font-size:12px}.section-head{gap:0}th,td{padding:10px 13px}.data-scroll table{min-width:570px}.triptych{min-width:900px}.source{padding:16px}.scene-heading h3{font-size:18px}}
</style></head><body><main>
<header><div class="header-meta"><span class="kicker">FUJI / ONBOARD × CG / LATEST COMPARISON</span><span class="badge">ローカル比較用</span><span class="badge" id="created"></span></div>
<h1>実車載と、改善前後のCGを<br>同じラップ時計で見る。</h1><p class="intro" id="intro"></p></header>
<section class="summary" aria-labelledby="summary-heading"><div><h2 id="summary-heading">要旨</h2>
<p>7/29・7/30の提供車載動画からフレームを取り直し、10/2の改善前と現在のCGを横に並べました。ラップ時計・チャンネル時刻・再生中の向き・縁石の描画を、同じ時刻で見比べるための資料です。</p>
<p class="fineprint">比較単位は<strong>画面に表示されるラップ時計 T</strong>です。修正前と修正後の内部再生時刻を、それぞれの時計の定義に合わせて設定しています。</p></div>
<div class="limits"><p><strong>見える差の範囲：</strong>CGのカメラ位置・画角は実カメラに未校正。ピクセル単位の一致や、画像だけで走行位置の改善量を判定する用途には使えません。</p>
<p><strong>残る不確定：</strong>GPS横位置の真値精度は未確定。映像の旋回とGPSの向きには別の遅れがあり、この比較では追加の時間ずらしを入れていません。</p>
<p><strong>取扱い：</strong>提供動画のフレームはローカル参照用。原動画・抽出画像の外部再配布は行わないでください。AIによる補完・超解像はありません。</p></div></section>
<nav class="links" aria-label="関連資料"><a href="fuji-improvement-evaluation-2026-10-02.html">10/2 数値による改善評価 →</a><a href="fuji-onboard-2026-09-16.html">9/16 旧版の車載比較 →</a></nav>
<section aria-labelledby="comparison-heading"><h2 id="comparison-heading">実車載・修正前CG・修正後CG</h2>
<div class="comparison"><div class="controls"><div class="row"><div id="days" class="day-tabs" role="group" aria-label="走行日を選択"></div>
<label class="camera" for="camera">CGのカメラ<select id="camera"><option value="cockpit">運転席に近い視点</option><option value="chase">追従カメラ</option></select></label></div>
<div id="scenes" class="scene-tabs" role="group" aria-label="ラップ時刻と場面を選択"></div></div>
<div class="scene-description" aria-live="polite" aria-atomic="true"><div class="scene-heading"><h3 id="scene-title"></h3><span class="clock" id="clock"></span></div><p id="scene-subtitle"></p></div>
<div class="comparison-scroll" aria-label="3列の比較画像。狭い画面では横にスクロールできます。" tabindex="0"><div class="triptych">
<figure><figcaption><span>実車載</span><span class="index">01 / VIDEO</span></figcaption><button class="image-button" id="video-button" type="button" aria-label="実車載を拡大"><img id="video" alt="同じ表示ラップ時計の実車載フレーム"></button><div class="image-meta" id="video-meta"></div></figure>
<figure><figcaption><span>修正前CG（10/2改善前）</span><span class="index">02 / BEFORE</span></figcaption><button class="image-button" id="before-button" type="button" aria-label="修正前CGを拡大"><img id="before" alt="10月2日の改善前のCG"></button><div class="image-meta" id="before-meta"></div></figure>
<figure><figcaption><span>修正後CG（現在）</span><span class="index">03 / CURRENT</span></figcaption><button class="image-button" id="after-button" type="button" aria-label="修正後CGを拡大"><img id="after" alt="現在のCG"></button><div class="image-meta" id="after-meta"></div></figure>
</div></div><div class="comparison-help"><span class="scroll-hint">← → 3列を横にスクロールして比較できます。</span>画像をクリックすると拡大します。CGの視点を切り替えても、比較するラップ時刻は変わりません。</div></div>
<noscript><p>このHTMLの画像切替にはJavaScriptが必要です。JavaScriptを有効にしたブラウザで開いてください。</p></noscript></section>
<section aria-labelledby="telemetry-heading"><div class="section-head"><h2 id="telemetry-heading">この場面の配信テレメトリ</h2><p>各CGの内部再生時刻での値 · 修正後 − 修正前</p></div>
<div class="data-scroll"><table><thead><tr><th scope="col">チャンネル</th><th scope="col">修正前CG</th><th scope="col">修正後CG</th><th scope="col">差</th></tr></thead><tbody id="telemetry"></tbody></table></div>
<p class="fineprint">値は各CGが参照した配信データです。車載動画に重ねられた計器表示の読取値ではありません。特に7/30では、チャンネル時刻の補正とラップ時計の修正が同時に反映されるため、同じ表示時計でもアクセル・ブレーキ・ステアの値が変わります。差は精度の判定値ではありません。</p></section>
<section aria-labelledby="method-heading"><h2 id="method-heading">時刻をどう揃えたか</h2><div class="method">
<article><h3>表示ラップ時計を基準にする</h3><p id="definition"></p><div class="formula">動画：PTS = 動画ラップ開始 + T<br>修正前：内部再生 t = T<br>修正後：内部再生 t = T − δ</div><p>δは計測ラインから最初のGPS標本までの時差です。修正前は最初の標本を時計の起点にしていたため、この違いも比較に含みます。</p></article>
<article><h3>再生中の向きを含めて撮る</h3><p id="capture-method">両CGは60fpsで2秒間の助走を再生してから撮影。静止した時刻ジャンプだけで撮ると消えてしまう、修正前の向きの平滑化による遅れも比較に含めています。</p><p>GPSは両方とも既定の補正を使用。縁石から推定した選択式の補正候補（?gps=kerb）は使用していません。9/16の旧CG画像は再利用していません。</p></article></div>
<div class="notice" id="alignment-note"></div>
<p class="fineprint">修正前は10/2に保存したビューアコードと7/30の配信ラップ・GPS入力を復元しています。7/29のラップは改善前後で同一のため共用。道路・車両・柵の共通素材には現在のものを使用しており、過去の全素材を復元した比較ではありません。修正前のコードは新しい縁石区分データを描画しないため、旧来の縁石描画との違いが含まれます。</p>
<p class="fineprint">この資料は代表場面の視覚照合です。全周・全コーナーの新たな精度評価、測量による横位置検証、実カメラへの画角・高さ合わせは含みません。縁石の比較も、独立した測量真値との一致を保証しません。計測結果と制約は10/2の数値評価を参照してください。</p></section>
<details class="source"><summary>撮影条件・出典・再配布の範囲</summary><div class="source-list" id="sources"></div><ul class="qa" id="qa"></ul>
<p class="fineprint">画像処理は、提供動画からのフレーム抽出と表示用の縮小・JPEG再圧縮、CGの画面取得のみです。埋込画像のEXIFは除去しています。CGの地形・設備資料はVIRTUAL SHIZUOKA 2019（静岡県、CC BY 4.0）を使用。その他の出典と加工条件はプロジェクトの既存メタデータを参照してください。Google Map Tilesのダウンロード・結合・加工は行っていません。</p></details>
<footer><span id="footer-labels"></span><br>自己完結HTML · 画像はファイル内に埋込 · ローカル参照用 · このHTMLの生成では外部公開を行っていません。</footer></main>
<dialog id="lightbox" aria-labelledby="lightbox-title"><div class="lightbox-head"><span id="lightbox-title"></span><button id="close-lightbox" type="button">閉じる · Esc</button></div><img id="large" alt=""><div class="lightbox-note">カメラ位置・画角は未校正。画像の拡大は寸法や位置精度の保証ではありません。</div></dialog>
<script type="application/json" id="payload">__PAYLOAD__</script>
<script>
'use strict';
const d=JSON.parse(document.getElementById('payload').textContent);
const $=id=>document.getElementById(id);
const raceIds=[...new Set(d.shots.map(s=>s.raceId))];
let day=raceIds[0],current=d.shots.findIndex(s=>s.raceId===day),focusBeforeZoom=null;
const f=(n,p=3)=>Number(n).toFixed(p);
function clock(n){const m=Math.floor(n/60);return m+':'+f(n-m*60).padStart(6,'0')}
function dateLabel(date){const match=/^(\d{4})-(\d{2})-(\d{2})/.exec(date);return match?Number(match[2])+'/'+Number(match[3]):date}
function sceneList(){return d.shots.map((s,i)=>({s,i})).filter(item=>item.s.raceId===day)}
function appendText(parent,tag,text,className){const el=document.createElement(tag);el.textContent=text;if(className)el.className=className;parent.append(el);return el}
function imageMetadata(id,lines){const el=$(id);el.replaceChildren();lines.forEach((line,i)=>{if(i)el.append(document.createElement('br'));el.append(document.createTextNode(line))})}
function makeDayButtons(){
  $('days').replaceChildren();
  raceIds.forEach(race=>{
    const group=d.shots.filter(s=>s.raceId===race),b=document.createElement('button');
    b.type='button';b.textContent=dateLabel(group[0].date)+' · L'+group[0].lap+' · '+group.length+'場面';
    b.dataset.race=race;b.setAttribute('aria-pressed',String(race===day));
    b.addEventListener('click',()=>{day=race;current=d.shots.findIndex(s=>s.raceId===day);makeSceneButtons();show()});
    $('days').append(b);
  });
}
function makeSceneButtons(){
  $('scenes').replaceChildren();
  sceneList().forEach(({s,i})=>{
    const b=document.createElement('button');b.type='button';b.dataset.index=String(i);
    b.append(document.createTextNode(f(s.time)+' s'));appendText(b,'span',s.label);
    b.setAttribute('aria-label',s.time+'秒、'+s.label);b.setAttribute('aria-pressed',String(i===current));
    b.addEventListener('click',()=>{current=i;show()});$('scenes').append(b);
  });
}
const channels=[
  {key:'speedKmh',label:'速度',unit:'km/h',digits:1},{key:'aps',label:'アクセル',unit:'%',digits:1},
  {key:'brake',label:'ブレーキ（配信値）',unit:'',digits:2},{key:'steer',label:'ステア',unit:'°',digits:1},
  {key:'accx',label:'前後G',unit:'g',digits:3},{key:'accy',label:'横G',unit:'g',digits:3}
];
function makeTelemetry(s){
  $('telemetry').replaceChildren();
  channels.forEach(ch=>{
    const tr=document.createElement('tr');appendText(tr,'td',ch.label);
    const a=s.telemetry.before[ch.key],b=s.telemetry.after[ch.key];
    const format=v=>v===null?'—':f(v,ch.digits)+(ch.unit?' '+ch.unit:'');
    appendText(tr,'td',format(a),'numeric');appendText(tr,'td',format(b),'numeric');
    const diff=a===null||b===null?'—':(b-a>0?'+':'')+f(b-a,ch.digits)+(ch.unit?' '+ch.unit:'');
    appendText(tr,'td',diff,'numeric delta');$('telemetry').append(tr);
  });
}
function show(){
  const s=d.shots[current],camera=$('camera').value,source=d.provenance.find(p=>p.raceId===s.raceId);
  $('video').src=s.video;$('before').src=s.cameras[camera].before;$('after').src=s.cameras[camera].after;
  $('scene-title').textContent=dateLabel(s.date)+' · '+s.label;
  $('clock').textContent='T '+clock(s.time)+' / '+f(s.time)+' s';
  $('scene-subtitle').textContent='ログ L'+s.lap+' ／ 車載の周表示 L'+s.overlayLap+' ／ δ '+f(s.delta)+' s ／ CG：'+(camera==='chase'?'追従カメラ':'運転席に近い視点');
  const videoLines=[source.name,'要求動画 PTS '+clock(s.videoPtsSeconds)+' / '+f(s.videoPtsSeconds)+' s'];
  if(s.decodedFramePtsSeconds!==undefined)videoLines.push('採用フレーム PTS '+f(s.decodedFramePtsSeconds)+' s / 差 '+f(s.decodedFramePtsSeconds-s.videoPtsSeconds)+' s');
  videoLines.push('比較する表示ラップ時計 T = '+f(s.time)+' s');
  imageMetadata('video-meta',videoLines);
  imageMetadata('before-meta',['表示ラップ時計 T = '+f(s.time)+' s','内部再生 t = '+f(s.beforePlaybackSeconds)+' s','既定GPS補正 · 改善前の配信データ']);
  imageMetadata('after-meta',['表示ラップ時計 T = '+f(s.time)+' s','内部再生 t = '+f(s.afterPlaybackSeconds)+' s','t + δ = '+f(s.afterPlaybackSeconds+s.delta)+' s · 既定GPS補正']);
  $('alignment-note').textContent='映像旋回とGPS向きの遅れの参照推定値：'+f(s.alignment.imageYawLagSeconds)+'秒。表示ラップ時計を揃えたこの比較では、この値による追加シフトは入れていません。別途、動画タイマーとフレームの時刻対応の不確かさは ±'+f(s.alignment.uncertaintySeconds)+'秒です（旋回遅れ推定値の信頼区間ではありません）。';
  for(const button of $('days').children)button.setAttribute('aria-pressed',String(button.dataset.race===day));
  for(const button of $('scenes').children)button.setAttribute('aria-pressed',String(Number(button.dataset.index)===current));
  makeTelemetry(s);
}
$('created').textContent=d.createdAt;
$('intro').textContent=raceIds.length+'走行日・'+d.shots.length+'場面を撮り直した最新版。運転席に近い視点と追従カメラを切り替え、時計の修正と配信チャンネルの違いを確認できます。';
$('definition').textContent=d.comparisonDefinition;
$('footer-labels').textContent='修正前：'+d.baselineLabel+' ／ 修正後：'+d.latestLabel;
if(d.qa.captureMethod)$('capture-method').textContent=d.qa.captureMethod;
d.provenance.forEach(source=>{
  const el=document.createElement('div');el.className='source-item';
  appendText(el,'strong',source.name);appendText(el,'div',source.permission);
  appendText(el,'code','SHA-256 '+source.sha256,'sha');$('sources').append(el);
});
const qa=[];
if(d.qa.beforeRevision)qa.push('修正前の版：'+d.qa.beforeRevision);
if(d.qa.afterRevision)qa.push('修正後の版：'+d.qa.afterRevision);
if(d.qa.captureFps!==undefined)qa.push('CG再生：'+d.qa.captureFps+' fps');
if(d.qa.prerollSeconds!==undefined)qa.push('各時刻の助走：'+d.qa.prerollSeconds+'秒');
if(d.qa.gpsMode)qa.push('GPS補正：'+d.qa.gpsMode);
if(d.qa.videoExtraction)qa.push('動画フレームの取得：'+d.qa.videoExtraction);
if(d.qa.orientationDifferenceDegrees!==undefined)qa.push('撮影時の前後CGの3D姿勢差：'+f(d.qa.orientationDifferenceDegrees)+'°（動画との誤差、カメラ校正や位置精度の指標ではありません）');
if(d.qa.orientationDifferences){const values=d.qa.orientationDifferences.map(item=>item.degrees);qa.push('撮影時の前後CGの3D姿勢差：'+f(Math.min(...values))+'〜'+f(Math.max(...values))+'°（動画との誤差ではありません）')}
if(d.qa.beforeSourceSha256)qa.push('修正前コードのSHA-256：'+d.qa.beforeSourceSha256);
if(d.qa.afterSourceSha256)qa.push('修正後コードのSHA-256：'+d.qa.afterSourceSha256);
qa.push(...(d.qa.notes||[]));qa.forEach(note=>appendText($('qa'),'li',note));
makeDayButtons();makeSceneButtons();show();
$('camera').addEventListener('change',show);
function openZoom(kind){
  const s=d.shots[current],label={video:'実車載',before:'修正前CG（10/2改善前）',after:'修正後CG（現在）'}[kind];
  focusBeforeZoom=document.activeElement;$('large').src=$(kind).src;
  $('large').alt=dateLabel(s.date)+' '+s.label+' '+label;
  $('lightbox-title').textContent=dateLabel(s.date)+' · '+label+' · T '+clock(s.time);
  if(!$('lightbox').open)$('lightbox').showModal();
}
['video','before','after'].forEach(kind=>$(kind+'-button').addEventListener('click',()=>openZoom(kind)));
$('close-lightbox').addEventListener('click',()=>$('lightbox').close());
$('lightbox').addEventListener('click',e=>{if(e.target===$('lightbox'))$('lightbox').close()});
$('lightbox').addEventListener('close',()=>{if(focusBeforeZoom)focusBeforeZoom.focus()});
document.addEventListener('keydown',e=>{
  if(e.key==='Escape'&&$('lightbox').open){$('lightbox').close();return}
  if(e.target instanceof HTMLSelectElement||e.target instanceof HTMLInputElement||$('lightbox').open)return;
  if(e.key!=='ArrowLeft'&&e.key!=='ArrowRight')return;
  if(e.target.classList&&e.target.classList.contains('comparison-scroll'))return;
  const list=sceneList(),index=list.findIndex(item=>item.i===current),next=index+(e.key==='ArrowRight'?1:-1);
  if(next>=0&&next<list.length){e.preventDefault();current=list[next].i;show()}
});
</script></body></html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-width", type=int, default=1440)
    parser.add_argument("--jpeg-quality", type=int, default=90)
    args = parser.parse_args()
    if args.max_width < 320 or args.max_width > 4096:
        parser.error("--max-width must be between 320 and 4096")
    if args.jpeg_quality < 50 or args.jpeg_quality > 98:
        parser.error("--jpeg-quality must be between 50 and 98")
    try:
        manifest_path = args.manifest.resolve(strict=True)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        data = prepare(manifest, manifest_path.parent, args.max_width, args.jpeg_quality)
        payload = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        document = HTML.replace("__PAYLOAD__", payload)
        output = args.output.resolve()
        if output.suffix.lower() != ".html":
            raise ValueError("output must be an .html file")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(document, encoding="utf-8")
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"Report generation failed: {exc}", file=sys.stderr)
        return 1
    print(f"Written {output}")
    print(f"{len(data['shots'])} scenes; {len(data['provenance'])} sources; {output.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
