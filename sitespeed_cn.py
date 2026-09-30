"""Read a sitespeed.io HTML report folder and write a one-page Chinese summary.

Nothing here calls the network or Cursor. Copy this folder to another computer
and run it with Python 3.

  py -3 sitespeed_cn.py "报告文件夹"
  py -3 sitespeed_cn.py "报告文件夹" -o "D:\\结论.html" --no-open
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import webbrowser
from collections import defaultdict
from dataclasses import dataclass, field
from html import escape, unescape
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit


GOOD_LCP_MS = 2500
POOR_LCP_MS = 4000
GOOD_CLS = 0.1
POOR_CLS = 0.25
GOOD_TBT_MS = 200
POOR_TBT_MS = 600
GOOD_SI_MS = 3400
POOR_SI_MS = 5800

NET_CN = {
    "native": "本机网络，没有限速",
    "cable": "模拟有线宽带",
    "3g": "模拟 3G",
    "3gfast": "模拟较快的 3G",
    "3gem": "模拟 3G",
    "lte": "模拟 4G",
    "2g": "模拟 2G",
}

APP_HANDLE_CN = (
    ("region-restriction", "地区限制应用"),
    ("ip-blocker", "地区限制应用"),
    ("reviews.io", "Reviews.io 评论"),
    ("reviews-co-uk", "Reviews.io 评论"),
    ("reviewsio", "Reviews.io 评论"),
    ("judge-me", "Judge.me 评论"),
    ("judgeme", "Judge.me 评论"),
    ("loox", "Loox 评论"),
    ("yotpo", "Yotpo 评论"),
    ("klaviyo", "Klaviyo"),
    ("gorgias", "Gorgias 客服"),
    ("privy", "Privy 弹窗"),
    ("omnisend", "Omnisend"),
    ("aftership", "AfterShip"),
    ("tidio", "Tidio 客服"),
    ("reamaze", "Reamaze 客服"),
    ("mailchimp", "Mailchimp"),
    ("inbox", "Shopify Inbox"),
    ("wishlist", "愿望清单应用"),
    ("analyzify", "Analyzify 统计"),
)

HOST_CN = (
    ("googletagmanager.com", "analytics", "Google 统计"),
    ("google-analytics.com", "analytics", "Google 统计"),
    ("doubleclick.net", "analytics", "Google 广告统计"),
    ("googleadservices.com", "analytics", "Google 广告统计"),
    ("facebook.net", "analytics", "Facebook 像素"),
    ("facebook.com", "analytics", "Facebook"),
    ("tiktok.com", "analytics", "TikTok 像素"),
    ("hotjar.com", "app", "Hotjar 热图"),
    ("clarity.ms", "app", "Microsoft Clarity"),
    ("klaviyo.com", "app", "Klaviyo"),
    ("reamaze.com", "app", "Reamaze 客服"),
    ("gorgias.chat", "app", "Gorgias 客服"),
    ("gorgias.io", "app", "Gorgias 客服"),
    ("judge.me", "app", "Judge.me 评论"),
    ("yotpo.com", "app", "Yotpo 评论"),
    ("loox.io", "app", "Loox 评论"),
    ("privy.com", "app", "Privy 弹窗"),
    ("omnisend.com", "app", "Omnisend"),
    ("aftership.com", "app", "AfterShip"),
    ("icons8.com", "app", "Icons8 图标"),
    ("youtube.com", "app", "YouTube"),
    ("ytimg.com", "app", "YouTube"),
    ("vimeo.com", "app", "Vimeo"),
    ("fonts.googleapis.com", "font", "Google 字体"),
    ("fonts.gstatic.com", "font", "Google 字体"),
    ("shop.app", "platform", "Shopify Shop 按钮"),
    ("shopifycloud.com", "platform", "Shopify 平台"),
    ("shopifysvc.com", "platform", "Shopify 平台"),
    ("shopifycdn.net", "platform", "Shopify 平台"),
)


@dataclass
class Asset:
    url: str
    host: str
    transfer: int
    resource_type: str
    mime: str
    ext: str
    group: str
    label: str


@dataclass
class Run:
    url: str
    lcp: float | None
    fcp: float | None
    cls: float | None
    tbt: float | None
    ttfb: float | None
    speed_index: float | None
    onload: float | None
    connectivity: str
    assets: list[Asset] = field(default_factory=list)

    @property
    def transfer(self) -> int:
        return sum(item.transfer for item in self.assets)

    @property
    def requests(self) -> int:
        return len(self.assets)


@dataclass
class Advice:
    rule: str
    severity: str
    title: str
    text: str
    offenders: list[str]


@dataclass
class PageReport:
    url: str
    runs: list[Run]
    coach: dict[str, int]
    advice: list[Advice]
    order: int = 0

    def median(self, name: str) -> float | None:
        values = [getattr(run, name) for run in self.runs if getattr(run, name) is not None]
        if not values:
            return None
        return float(statistics.median(values))

    def representative(self) -> Run:
        target = self.median("lcp")
        if target is None:
            return self.runs[0]

        def distance(run: Run) -> float:
            if run.lcp is None:
                return 10**12
            return abs(run.lcp - target)

        return min(self.runs, key=distance)


def median(values: list[float]) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))


def num(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def fmt_ms(value: float | None) -> str:
    if value is None:
        return "—"
    if value >= 10000:
        return f"{value / 1000:.1f} 秒"
    if value >= 100:
        return f"{round(value)} 毫秒"
    text = f"{value:.1f}".rstrip("0").rstrip(".")
    return f"{text} 毫秒"


def fmt_cls(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.3f}"


def fmt_bytes(value: int | None) -> str:
    if value is None:
        return "—"
    if value >= 1024 * 1024:
        text = f"{value / 1024 / 1024:.1f}".rstrip("0").rstrip(".")
        return f"{text} MB"
    if value >= 1024:
        return f"{round(value / 1024)} KB"
    return f"{value} B"


def band_high(value: float | None, good: float, poor: float) -> str:
    if value is None:
        return "unknown"
    if value <= good:
        return "good"
    if value >= poor:
        return "poor"
    return "warn"


def band_low(value: float | None, good: float, poor: float) -> str:
    if value is None:
        return "unknown"
    if value >= good:
        return "good"
    if value <= poor:
        return "poor"
    return "warn"


def band_label(band: str) -> str:
    return {"good": "好", "warn": "注意", "poor": "差"}.get(band, "—")


def worst_band(bands: list[str]) -> str:
    if "poor" in bands:
        return "poor"
    if "warn" in bands:
        return "warn"
    if "good" in bands:
        return "good"
    return "unknown"


def e(value: object) -> str:
    return escape(str(value), quote=True)


def short_url(url: str) -> str:
    path = urlsplit(url).path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    if len(path) > 52:
        path = path[:24] + "…" + path[-22:]
    return path or "/"


def file_label(url: str) -> str:
    parts = urlsplit(url)
    name = unquote(Path(parts.path).name) or parts.path or url
    if name in {"js", "css"} or len(name) <= 3:
        segments = [segment for segment in parts.path.split("/") if segment]
        name = "/".join(segments[-2:]) if len(segments) >= 2 else name
    query = parse_qs(parts.query)
    bits = []
    if "width" in query:
        bits.append(f"宽 {query['width'][0]}")
    if "id" in query and len(query["id"][0]) <= 28:
        bits.append(query["id"][0])
    extra = f"（{'，'.join(bits)}）" if bits else ""
    if len(name) > 42:
        name = name[:18] + "…" + name[-16:]
    return name + extra


def is_first_party(host: str, site_host: str) -> bool:
    host = (host or "").lower()
    site = (site_host or "").lower()
    if not host or not site:
        return False
    return host == site or host.endswith("." + site) or site.endswith("." + host)


def extension_label(path: str) -> str | None:
    match = re.search(r"/extensions/[^/]+/([^/]+)/", path)
    if not match:
        return None
    handle = match.group(1).lower()
    for needle, label in APP_HANDLE_CN:
        if needle in handle:
            return label
    pretty = re.sub(r"-?\d+", "", handle)
    pretty = re.sub(r"[\s-]+", " ", pretty).strip(" -")
    return f"应用：{pretty}" if pretty else "Shopify 应用"


def script_identity(url: str, host: str, site_host: str) -> tuple[str, str]:
    path = urlsplit(url).path.lower()
    name = Path(path).name
    if "mailsturbo" in name or "mailsturbo" in path:
        return "app", "Mailsturbo 邮件"
    if is_first_party(host, site_host):
        if "/cdn/shopifycloud/" in path or "/cdn/shop/checkouts" in path:
            return "platform", "Shopify 平台脚本"
        if "/cdn/shop/t/" in path or "/cdn/shop/files/" in path:
            return "theme", "主题脚本"
        return "theme", "店铺脚本"
    if host.endswith("cdn.shopify.com") or host == "cdn.shopify.com":
        found = extension_label(path)
        if found:
            return "app", found
        return "platform", "Shopify 文件库"
    for needle, group, label in HOST_CN:
        if host == needle or host.endswith("." + needle):
            if group == "font":
                return "font", label
            return group, label
    if host.endswith(".cloudfront.net"):
        stem = Path(name).stem.replace(".min", "")
        return "app", f"CloudFront 上的 {stem}" if stem else "CloudFront 上的应用"
    return "other", host or "其他"


def classify(url: str, host: str, resource_type: str, mime: str, site_host: str) -> tuple[str, str]:
    if resource_type == "prefetch":
        return "prefetch", "预取其他页面"
    if resource_type == "stylesheet" or mime.startswith("text/css"):
        return "css", "样式"
    if resource_type == "font" or "font" in mime:
        return "font", "字体"
    if resource_type == "image" or mime.startswith("image/"):
        return "image", "图片"
    if resource_type == "document" or mime.startswith("text/html"):
        return "document", "当前页 HTML"
    if resource_type == "script" or "javascript" in mime or mime.endswith("/ecmascript"):
        return script_identity(url, host, site_host)
    identity = script_identity(url, host, site_host)
    if identity[0] in {"analytics", "app", "platform"}:
        return identity
    return "other", "其他"


def transfer_size(entry: dict) -> int:
    response = entry.get("response") or {}
    size = response.get("_transferSize")
    if isinstance(size, (int, float)) and size > 0:
        return int(size)
    body = response.get("bodySize")
    if isinstance(body, (int, float)) and body > 0 and not response.get("fromDiskCache"):
        return int(body)
    return 0


def parse_run(page: dict, entries: list[dict], site_host: str) -> Run:
    vitals = page.get("_googleWebVitals") or {}
    visual = page.get("_visualMetrics") or {}
    timings = page.get("pageTimings") or {}
    connectivity = ""
    meta = page.get("_meta") or {}
    raw_net = meta.get("connectivity")
    if isinstance(raw_net, str):
        connectivity = raw_net
    elif isinstance(raw_net, dict):
        connectivity = str(raw_net.get("profile") or raw_net.get("alias") or "")
    assets: list[Asset] = []
    for entry in entries:
        request = entry.get("request") or {}
        url = request.get("url") or ""
        if not url.startswith("http"):
            continue
        response = entry.get("response") or {}
        mime = ((response.get("content") or {}).get("mimeType") or "").split(";")[0].strip().lower()
        host = (urlsplit(url).hostname or "").lower()
        path = urlsplit(url).path.lower()
        ext = path.rsplit(".", 1)[-1] if "." in Path(path).name else ""
        resource_type = (entry.get("_resourceType") or "").lower()
        group, label = classify(url, host, resource_type, mime, site_host)
        assets.append(
            Asset(
                url=url,
                host=host,
                transfer=transfer_size(entry),
                resource_type=resource_type,
                mime=mime,
                ext=ext,
                group=group,
                label=label,
            )
        )
    return Run(
        url=page.get("_url") or page.get("title") or "",
        lcp=num(vitals.get("largestContentfulPaint")) or num(timings.get("_largestContentfulPaint")),
        fcp=num(vitals.get("firstContentfulPaint")) or num(timings.get("_firstContentfulPaint")),
        cls=num(vitals.get("cumulativeLayoutShift")),
        tbt=num(vitals.get("totalBlockingTime")),
        ttfb=num(vitals.get("ttfb")),
        speed_index=num(visual.get("SpeedIndex")),
        onload=num(timings.get("onLoad")),
        connectivity=connectivity,
        assets=assets,
    )


def load_har(path: Path) -> list[Run]:
    har = json.loads(path.read_text(encoding="utf-8-sig"))
    log = har.get("log") or {}
    by_ref: dict[str, list[dict]] = defaultdict(list)
    for entry in log.get("entries") or []:
        by_ref[entry.get("pageref") or ""].append(entry)
    runs: list[Run] = []
    for page in log.get("pages") or []:
        url = page.get("_url") or ""
        site_host = (urlsplit(url).hostname or "").lower()
        runs.append(parse_run(page, by_ref.get(page.get("id") or "", []), site_host))
    return [run for run in runs if run.url]


DETAIL_RE = re.compile(
    r'<details class="coach-advice" id="[^"]+">(.*?)</details>',
    re.S,
)
SCORE_RE = re.compile(
    r'class="coach-score-label">([^<]+)</span><span class="coach-score-value">(\d+)</span>'
)


def parse_coach(path: Path) -> tuple[dict[str, int], list[Advice]]:
    if not path.is_file():
        return {}, []
    text = path.read_text(encoding="utf-8", errors="replace")
    scores: dict[str, int] = {}
    start = text.find("coach-summary-scores")
    chunk = text[start : start + 2000] if start >= 0 else ""
    labels = {
        "overall": "overall",
        "performance": "performance",
        "best practice": "bestpractice",
        "privacy": "privacy",
    }
    for label, value in SCORE_RE.findall(chunk):
        key = labels.get(label.strip().lower())
        if key:
            scores[key] = int(value)
    advice: list[Advice] = []
    for block in DETAIL_RE.findall(text):
        severity = re.search(r"coach-severity--(error|warn|info)", block)
        title = re.search(r'class="coach-advice-title">([^<]+)</span>', block)
        rule = re.search(r'class="coach-advice-rule">([^<]+)</code>', block)
        body = re.search(r'class="coach-advice-text">([^<]*)</p>', block)
        if not (severity and title and rule):
            continue
        offenders = []
        for href in re.findall(r'href="(https?://[^"]+)"', block):
            if href not in offenders:
                offenders.append(unescape(href))
            if len(offenders) == 3:
                break
        advice.append(
            Advice(
                rule=rule.group(1),
                severity=severity.group(1),
                title=unescape(title.group(1)),
                text=unescape(body.group(1)).strip() if body else "",
                offenders=offenders,
            )
        )
    return scores, advice


def find_hars(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("browsertime.har") if "data" in path.parts)


def page_order(root: Path) -> list[str]:
    index = root / "index.html"
    if not index.is_file():
        return []
    text = index.read_text(encoding="utf-8", errors="replace")
    keys = []
    for href in re.findall(r'href="(pages/[^"]+/index\.html)"', text):
        key = str(Path(href).parent).replace("\\", "/")
        if key not in keys:
            keys.append(key)
    return keys


def report_meta(root: Path) -> dict[str, str]:
    meta = {"tested": "", "browser": "", "version": "", "site": ""}
    index = root / "index.html"
    if not index.is_file():
        return meta
    text = index.read_text(encoding="utf-8", errors="replace")
    tested = re.search(r"Tested ([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9:]{8})", text)
    browser = re.search(r"using ([A-Za-z0-9.]+)", text)
    version = re.search(r"sitespeed\.io</a>\s*([0-9.]+)", text)
    site = re.search(r"analysed for (https?://[^<\s]+)", text)
    if tested:
        meta["tested"] = tested.group(1)
    if browser:
        meta["browser"] = browser.group(1)
    if version:
        meta["version"] = version.group(1)
    if site:
        meta["site"] = site.group(1).rstrip("/")
    return meta


def load_report(root: Path) -> tuple[list[PageReport], dict[str, str]]:
    order = page_order(root)
    grouped: dict[str, PageReport] = {}
    for har_path in find_hars(root):
        page_dir = har_path.parent.parent
        try:
            relative = page_dir.relative_to(root).as_posix()
        except ValueError:
            relative = page_dir.as_posix()
        coach, advice = parse_coach(page_dir / "index.html")
        for run in load_har(har_path):
            url = run.url.split("#")[0]
            current = grouped.get(url)
            if current is None:
                grouped[url] = PageReport(
                    url=url,
                    runs=[run],
                    coach=coach,
                    advice=advice,
                    order=order.index(relative) if relative in order else 1000 + len(grouped),
                )
            else:
                current.runs.append(run)
                current.coach = current.coach or coach
                current.advice = current.advice or advice
    pages = sorted(grouped.values(), key=lambda page: (page.order, page.url))
    return pages, report_meta(root)


def core_band(page: PageReport) -> str:
    return worst_band(
        [
            band_high(page.median("lcp"), GOOD_LCP_MS, POOR_LCP_MS),
            band_high(page.median("cls"), GOOD_CLS, POOR_CLS),
            band_high(page.median("tbt"), GOOD_TBT_MS, POOR_TBT_MS),
        ]
    )


def image_format_note(assets: list[Asset]) -> str:
    images = [item for item in assets if item.group == "image"]
    if not images:
        return ""
    modern = [item for item in images if item.mime in {"image/webp", "image/avif"}]
    legacy_url = [item for item in modern if item.ext in {"jpg", "jpeg", "png", "gif"}]
    if modern and len(modern) / len(images) >= 0.8 and legacy_url:
        return (
            f"图片有 {len(images)} 个请求。其中 {len(legacy_url)} 个网址仍以 jpg 或 png 结尾，"
            f"响应类型已经是 WebP 或 AVIF。Coach 按网址后缀扣分，文件本身已经是现代格式。"
        )
    if modern and len(modern) / len(images) >= 0.8:
        return f"图片有 {len(images)} 个请求，响应类型已经是 WebP 或 AVIF。"
    old = [item for item in images if item.mime in {"image/jpeg", "image/png", "image/gif"}]
    if old:
        return f"有 {len(old)} 个图片请求的响应仍是 JPEG、PNG 或 GIF。"
    return ""


def group_rows(assets: list[Asset]) -> list[dict]:
    buckets: dict[tuple[str, str], list[Asset]] = defaultdict(list)
    for item in assets:
        buckets[(item.group, item.label)].append(item)
    rows = []
    for (group, label), items in buckets.items():
        top = max(items, key=lambda asset: asset.transfer)
        rows.append(
            {
                "group": group,
                "label": label,
                "transfer": sum(item.transfer for item in items),
                "requests": len(items),
                "top": top,
                "pages": sorted({short_url(item.url) for item in items}),
            }
        )
    rows.sort(key=lambda row: row["transfer"], reverse=True)
    return rows


def finding_for_row(row: dict, speed_good: bool) -> dict | None:
    group = row["group"]
    transfer = row["transfer"]
    top: Asset = row["top"]
    if group == "analytics" and transfer >= 30 * 1024:
        return {
            "bytes": transfer,
            "where": "统计代码",
            "title": f"{row['label']}是体积里很大的一块脚本",
            "body": (
                f"这一页上，{row['label']}大约 {fmt_bytes(transfer)}"
                f"（{row['requests']} 个请求）。最大的文件是 {file_label(top.url)}，"
                f"大约 {fmt_bytes(top.transfer)}。"
                + (
                    "打开速度已经在好的范围内。以后想减体积，先看统计代码要不要晚一点加载。"
                    if speed_good
                    else "它和页面正文抢带宽。可以让统计代码在页面主要内容出来之后再加载。"
                )
            ),
        }
    if group == "app" and transfer >= 80 * 1024:
        return {
            "bytes": transfer,
            "where": "应用",
            "title": f"{row['label']}大约 {fmt_bytes(transfer)}",
            "body": (
                f"最大的文件是 {file_label(top.url)}。"
                "这是安装在店铺里的应用，开关在应用自己的设置里。"
                + ("以当前的打开速度，这个应用可以留着。" if speed_good else "如果这个应用不是必须马上出现，可以改成晚一点加载。")
            ),
        }
    if group == "theme" and transfer >= 150 * 1024:
        return {
            "bytes": transfer,
            "where": "主题",
            "title": f"主题脚本大约 {fmt_bytes(transfer)}",
            "body": f"最大的文件是 {file_label(top.url)}，大约 {fmt_bytes(top.transfer)}。主题文件在主题代码里。",
        }
    if group == "image" and top.transfer >= 150 * 1024:
        return {
            "bytes": top.transfer,
            "where": "图片",
            "title": f"最大的一张图大约 {fmt_bytes(top.transfer)}",
            "body": (
                f"文件是 {file_label(top.url)}。"
                + (
                    "响应已经是 WebP。"
                    if top.mime in {"image/webp", "image/avif"}
                    else "响应仍是传统图片格式。"
                )
                + (
                    "最大内容绘制已经在好的范围内，缩这张图只会再少一点体积。"
                    if speed_good
                    else "它很可能拖住最大内容绘制，优先把宽度降下来。"
                )
            ),
        }
    return None


def vital_findings(pages: list[PageReport]) -> list[dict]:
    findings = []
    names = (
        ("lcp", "最大内容绘制", GOOD_LCP_MS, POOR_LCP_MS, fmt_ms),
        ("cls", "布局偏移", GOOD_CLS, POOR_CLS, fmt_cls),
        ("tbt", "主线程阻塞", GOOD_TBT_MS, POOR_TBT_MS, fmt_ms),
    )
    for page in pages:
        for key, label, good, poor, formatter in names:
            value = page.median(key)
            band = band_high(value, good, poor)
            if band not in {"warn", "poor"}:
                continue
            line = "差" if band == "poor" else "注意"
            standard = formatter(good)
            findings.append(
                {
                    "bytes": 10**15 if band == "poor" else 10**14,
                    "where": "速度",
                    "title": f"{short_url(page.url)} 的{label}是{line}",
                    "body": f"中位数 {formatter(value)}。好的范围是 {standard} 以内。",
                }
            )
    return findings


def build_actions(pages: list[PageReport], rows: list[dict], speed_good: bool) -> list[dict]:
    findings = vital_findings(pages)
    for row in rows:
        found = finding_for_row(row, speed_good)
        if found:
            findings.append(found)
    findings.sort(key=lambda item: item["bytes"], reverse=True)
    kept = []
    seen = set()
    for item in findings:
        if item["title"] in seen:
            continue
        seen.add(item["title"])
        kept.append(item)
        if len(kept) == 5:
            break
    return kept


def advice_by_rule(pages: list[PageReport]) -> dict[str, Advice]:
    found: dict[str, Advice] = {}
    rank = {"error": 3, "warn": 2, "info": 1}
    for page in pages:
        for advice in page.advice:
            current = found.get(advice.rule)
            if current is None or rank[advice.severity] > rank[current.severity]:
                found[advice.rule] = advice
    return found


def build_ignores(pages: list[PageReport], assets: list[Asset], rows: list[dict], speed_good: bool) -> list[str]:
    notes = []
    if speed_good:
        lcp = pooled(pages, "lcp")
        cls = pooled(pages, "cls")
        tbt = pooled(pages, "tbt")
        notes.append(
            f"最大内容绘制中位数 {fmt_ms(lcp)}，布局偏移 {fmt_cls(cls)}，主线程阻塞 {fmt_ms(tbt)}。"
            "这三项都在好的范围内。"
        )
    coaches = [page.coach.get("performance") for page in pages if page.coach.get("performance") is not None]
    if coaches and speed_good and min(coaches) < 90:
        notes.append(
            f"Coach 性能分是 {min(coaches)} 到 {max(coaches)}，它自己的通过线是 90。"
            "这个分数低，只说明没跨过 Coach 的线。"
        )
    format_note = image_format_note(assets)
    if format_note and "已经是" in format_note:
        notes.append(format_note)
    theme = next((row for row in rows if row["group"] == "theme"), None)
    analytics = [row for row in rows if row["group"] == "analytics"]
    if theme and analytics and theme["transfer"] < 80 * 1024:
        notes.append(
            f"主题脚本大约 {fmt_bytes(theme['transfer'])}。"
            f"脚本体积的大头在 {'、'.join(row['label'] for row in analytics)}。"
        )
    prefetch = next((row for row in rows if row["group"] == "prefetch"), None)
    if prefetch and prefetch["transfer"] >= 40 * 1024:
        target = urlsplit(prefetch["top"].url).path or prefetch["top"].url
        notes.append(
            f"有大约 {fmt_bytes(prefetch['transfer'])} 是提前去取另一个页面 {target}。"
            "这是预取，当前页的正文不含这一段。"
        )
    small_apps = [row for row in rows if row["group"] == "app" and row["transfer"] < 80 * 1024]
    if small_apps:
        names = "、".join(f"{row['label']} {fmt_bytes(row['transfer'])}" for row in small_apps)
        notes.append(f"这些应用在这一页上体积不大：{names}。")
    css_rows = [row for row in rows if row["group"] == "css"]
    if css_rows and css_rows[0]["requests"] >= 25 and css_rows[0]["top"].transfer < 30 * 1024:
        css = css_rows[0]
        notes.append(
            f"样式有 {css['requests']} 个请求，最大的一个只有 {fmt_bytes(css['top'].transfer)}。"
            "数量多，单个文件不大。"
        )
    platform = [row for row in rows if row["group"] == "platform"]
    if platform:
        total = sum(row["transfer"] for row in platform)
        notes.append(f"Shopify 平台相关的文件大约 {fmt_bytes(total)}，属于店铺平台自带。")
    rules = advice_by_rule(pages)
    if any(rule in rules for rule in (
        "strictTransportSecurityHeader",
        "contentSecurityPolicyHeader",
        "referrerPolicy",
        "referrerPolicyHeader",
        "permissionsPolicyHeader",
        "crossOriginOpenerPolicyHeader",
        "crossOriginEmbedderPolicyHeader",
        "crossOriginResourcePolicyHeader",
    )):
        privacy = [page.coach.get("privacy") for page in pages if page.coach.get("privacy") is not None]
        privacy_text = f"隐私分 {min(privacy)}。" if privacy else ""
        notes.append(
            privacy_text
            + "扣分来自 HSTS、内容安全策略、Referrer 这类响应头。它们要在平台或服务器上设置，和这次的打开速度是两件事。"
        )
    if "imageAltText" in rules or "pageTitle" in rules or "metaDescription" in rules:
        notes.append("图片替代文本、标题长度、描述文字影响的是检索和无障碍。")
    if "decodingAsync" in rules and speed_good:
        notes.append("Coach 建议给图片加 decoding=async。主线程阻塞已经在好的范围内。")
    if "mimeTypes" in rules:
        offender = rules["mimeTypes"].offenders[0] if rules["mimeTypes"].offenders else ""
        name = file_label(offender) if offender else "一个小文件"
        notes.append(f"有 1 个文件的类型声明和 Coach 的期望不一致（{name}）。页面能正常打开。")
    return notes


def pooled(pages: list[PageReport], name: str) -> float | None:
    values = [
        getattr(run, name)
        for page in pages
        for run in page.runs
        if getattr(run, name) is not None
    ]
    return median(values)


def focus_page(pages: list[PageReport]) -> PageReport:
    ordered = sorted(pages, key=lambda page: page.representative().transfer)
    return ordered[len(ordered) // 2]


def conclusion(pages: list[PageReport], meta: dict[str, str]) -> tuple[str, str]:
    host = urlsplit(pages[0].url).hostname if pages else meta.get("site") or "这个站"
    bands = [core_band(page) for page in pages]
    overall = worst_band(bands)
    lcp_values = [run.lcp for page in pages for run in page.runs if run.lcp is not None]
    lcp = pooled(pages, "lcp")
    cls = pooled(pages, "cls")
    tbt = pooled(pages, "tbt")
    word = {"good": "快", "warn": "中等", "poor": "慢"}.get(overall, "有结果")
    lead = f"{host} 这 {len(pages)} 个页面，打开速度是{word}的。"
    page_lcps = [page.median("lcp") for page in pages if page.median("lcp") is not None]
    slowest_page = max(page_lcps) if page_lcps else None
    if slowest_page is not None and slowest_page <= GOOD_LCP_MS:
        lcp_sentence = (
            f"全部 {len(lcp_values)} 次测试的最大内容绘制中位数是 {fmt_ms(lcp)}。"
            f"各页中位数里最慢的是 {fmt_ms(slowest_page)}，在 2.5 秒的好范围以内。"
        )
    else:
        lcp_sentence = (
            f"全部 {len(lcp_values)} 次测试的最大内容绘制中位数是 {fmt_ms(lcp)}。"
            f"各页中位数里最慢的是 {fmt_ms(slowest_page)}。好的范围是 2.5 秒以内。"
        )
    detail = f"{lcp_sentence}布局偏移中位数 {fmt_cls(cls)}，主线程阻塞中位数 {fmt_ms(tbt)}。"
    weak = [page for page in pages if core_band(page) != "good"]
    if weak:
        names = "、".join(short_url(page.url) for page in weak)
        detail += f"需要看的页面是 {names}。"
    coaches = [page.coach.get("performance") for page in pages if page.coach.get("performance") is not None]
    if coaches:
        detail += f"Coach 性能分 {min(coaches)} 到 {max(coaches)}，通过线是 90。"
    return lead, detail


def speed_is_good(pages: list[PageReport]) -> bool:
    return bool(pages) and all(core_band(page) == "good" for page in pages)


def cell(value: str, band: str) -> str:
    return f'<td class="num {e(band)}">{e(value)}<i>{e(band_label(band))}</i></td>'


def render(pages: list[PageReport], meta: dict[str, str], source: Path) -> str:
    runs = [page.representative() for page in pages]
    focus = focus_page(pages)
    assets = focus.representative().assets
    rows = group_rows(assets)
    good = speed_is_good(pages)
    actions = build_actions(pages, rows, good)
    ignores = build_ignores(pages, assets, rows, good)
    lead, detail = conclusion(pages, meta)
    host = urlsplit(pages[0].url).hostname if pages else ""
    runs_each = max((len(page.runs) for page in pages), default=0)
    connectivity = next((run.connectivity for run in runs if run.connectivity), "")
    net = NET_CN.get(connectivity, connectivity)
    tested = meta.get("tested") or "时间未写入报告"
    browser = meta.get("browser") or "Chrome"
    version = f"sitespeed {meta['version']}" if meta.get("version") else "sitespeed"

    page_rows = []
    for page in pages:
        coach = page.coach.get("performance")
        coach_band = band_low(coach, 90, 70) if coach is not None else "unknown"
        verdict = core_band(page)
        page_rows.append(
            "<tr>"
            f"<td><a href=\"{e(page.url)}\">{e(short_url(page.url))}</a></td>"
            f"<td class=\"num {e(verdict)}\"><i>{e(band_label(verdict))}</i></td>"
            + cell(fmt_ms(page.median("lcp")), band_high(page.median("lcp"), GOOD_LCP_MS, POOR_LCP_MS))
            + cell(fmt_cls(page.median("cls")), band_high(page.median("cls"), GOOD_CLS, POOR_CLS))
            + cell(fmt_ms(page.median("tbt")), band_high(page.median("tbt"), GOOD_TBT_MS, POOR_TBT_MS))
            + cell(fmt_ms(page.median("speed_index")), band_high(page.median("speed_index"), GOOD_SI_MS, POOR_SI_MS))
            + f"<td class=\"num\">{e(fmt_bytes(int(median([run.transfer for run in page.runs]) or 0)))}</td>"
            + f"<td class=\"num\">{e(str(round(median([run.requests for run in page.runs]) or 0)))}</td>"
            + (
                cell(str(coach), coach_band)
                if coach is not None
                else "<td class=\"num unknown\">—</td>"
            )
            + "</tr>"
        )

    volume_rows = []
    for row in rows:
        if row["transfer"] < 2048 and row["group"] == "other":
            continue
        top = row["top"]
        volume_rows.append(
            "<tr>"
            f"<td>{e(row['label'])}</td>"
            f"<td class=\"num\">{e(fmt_bytes(row['transfer']))}</td>"
            f"<td class=\"num\">{row['requests']}</td>"
            f"<td>{e(file_label(top.url))}<small>{e(fmt_bytes(top.transfer))}</small></td>"
            "</tr>"
        )

    focus_label = short_url(focus.url)
    action_html = []
    for index, action in enumerate(actions, start=1):
        action_html.append(
            "<article>"
            f"<span>{index}</span>"
            "<div>"
            f"<h3>{e(action['title'])}</h3>"
            f"<p>{e(action['body'])}</p>"
            f"<em>{e(action['where'])}</em>"
            "</div></article>"
        )
    if not action_html:
        action_html.append("<p class=\"quiet\">没有单笔大到需要单独写出来的项目。</p>")

    ignore_html = "".join(f"<li>{e(note)}</li>" for note in ignores) or "<li>这次没有额外要搁置的项。</li>"
    action_intro = (
        "打开速度已经在好的范围内。下面是体积的主要来源，方便以后想减体积时知道先看哪里。"
        if good
        else "下面按影响从大到小排列，最多 5 条。"
    )
    action_title = "体积里比较大的几项" if good else "建议先处理"
    net_line = f"网络条件：{net}。" if net else ""
    if connectivity == "native":
        net_line += "这个数字用来看页面本身重不重。顾客用手机、走慢网络时会更长。"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(host)} 速度结论</title>
<style>
:root {{
  --ink: #1d1a16;
  --muted: #665e55;
  --line: #e3d9cc;
  --paper: #f7f3ec;
  --card: #fffdf9;
  --good: #1d6b45;
  --good-bg: #e7f3ec;
  --warn: #8a5a12;
  --warn-bg: #f8efd8;
  --poor: #8d322c;
  --poor-bg: #f8e6e3;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background: var(--paper);
  color: var(--ink);
  font: 16px/1.6 "Segoe UI", "Microsoft YaHei", sans-serif;
}}
main {{ max-width: 1040px; margin: 0 auto; padding: 32px 20px 64px; }}
header h1 {{ font-size: 32px; line-height: 1.25; margin: 0 0 8px; font-weight: 650; }}
header p, .lead p {{ color: var(--muted); margin: 0; }}
.lead {{
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 16px;
  padding: 22px 24px;
  margin: 22px 0;
}}
.lead h2 {{ margin: 0 0 8px; font-size: 22px; }}
h2 {{ font-size: 20px; margin: 32px 0 8px; }}
.section-note {{ color: var(--muted); margin: 0 0 12px; }}
table {{ width: 100%; border-collapse: collapse; background: var(--card); }}
th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--line); vertical-align: top; }}
th {{ font-size: 13px; color: var(--muted); font-weight: 600; white-space: nowrap; }}
td.num {{ white-space: nowrap; font-variant-numeric: tabular-nums; }}
td small, article em {{ display: block; color: var(--muted); font-style: normal; font-size: 13px; }}
td.good i, td.warn i, td.poor i {{
  display: inline-block; margin-left: 6px; font-style: normal; font-size: 12px;
  padding: 0 6px; border-radius: 999px;
}}
td.good i {{ background: var(--good-bg); color: var(--good); }}
td.warn i {{ background: var(--warn-bg); color: var(--warn); }}
td.poor i {{ background: var(--poor-bg); color: var(--poor); }}
.wrap {{ overflow-x: auto; border: 1px solid var(--line); border-radius: 14px; }}
.wrap table {{ min-width: 880px; }}
article {{
  display: grid; grid-template-columns: 36px 1fr; gap: 12px;
  background: var(--card); border: 1px solid var(--line); border-radius: 14px;
  padding: 14px 16px; margin: 10px 0;
}}
article span {{
  width: 36px; height: 36px; border-radius: 50%; background: var(--ink); color: white;
  display: grid; place-items: center; font-size: 14px;
}}
article h3 {{ margin: 0 0 4px; font-size: 17px; }}
article p {{ margin: 0; }}
ul {{ margin: 8px 0 0; padding-left: 18px; }}
li {{ margin: 6px 0; }}
footer {{ margin-top: 28px; color: var(--muted); font-size: 13px; }}
a {{ color: inherit; }}
.quiet {{ color: var(--muted); }}
</style>
</head>
<body>
<main>
<header>
<h1>{e(host)} 的中文结论</h1>
<p>{e(tested)} · {e(browser)} · {e(version)} · 每页 {runs_each} 次，表内是中位数</p>
<p>{e(net_line)}</p>
</header>
<section class="lead">
<h2>{e(lead)}</h2>
<p>{e(detail)}</p>
</section>
<h2>各页面</h2>
<p class="section-note">「好 / 注意 / 差」按 Google 的公开范围。Coach 另有一条 90 分的线，写在最后一列。</p>
<div class="wrap"><table>
<thead><tr>
<th>页面</th><th>速度</th><th>最大内容绘制 LCP</th><th>布局偏移 CLS</th><th>主线程阻塞 TBT</th>
<th>速度指数</th><th>传输</th><th>请求</th><th>Coach 性能</th>
</tr></thead>
<tbody>
{''.join(page_rows)}
</tbody>
</table></div>
<h2>体积花在哪</h2>
<p class="section-note">下表是传输体积居中的那一页：{e(focus_label)}。数字来自最接近中位数的那一次，小于 2 KB 的杂项不单列。</p>
<div class="wrap"><table>
<thead><tr><th>来源</th><th>体积</th><th>请求</th><th>最大的一个文件</th></tr></thead>
<tbody>
{''.join(volume_rows)}
</tbody>
</table></div>
<h2>{e(action_title)}</h2>
<p class="section-note">{e(action_intro)}</p>
{''.join(action_html)}
<h2>可以先不动</h2>
<ul>
{ignore_html}
</ul>
<footer>
<p>好的范围：最大内容绘制 2.5 秒以内，布局偏移 0.1 以内，主线程阻塞 200 毫秒以内。超过 4 秒、0.25、600 毫秒算差。速度指数沿用这份报告自己的线：3.4 秒以内为好，超过 5.8 秒为差。Coach 90 分是它自己的通过线。</p>
<p>由报告目录生成：{e(source)}</p>
</footer>
</main>
</body>
</html>
"""


def resolve_root(raw: str) -> Path:
    path = Path(raw).expanduser().resolve()
    if path.is_file():
        path = path.parent
    if not path.is_dir():
        raise SystemExit(f"找不到这个目录：{path}")
    if not find_hars(path):
        raise SystemExit("这个目录里没有 browsertime.har。请选择 sitespeed 解压后的报告文件夹。")
    return path


def pick_folder() -> str:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    chosen = filedialog.askdirectory(title="选择 sitespeed 报告文件夹")
    root.destroy()
    return chosen


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="把 sitespeed 报告写成一页中文结论")
    parser.add_argument("report", nargs="?", help="sitespeed 报告文件夹")
    parser.add_argument("-o", "--output", help="输出 HTML 路径，默认写到报告目录里的中文结论.html")
    parser.add_argument("--no-open", action="store_true", help="生成后不打开浏览器")
    args = parser.parse_args(argv)
    raw = args.report
    if not raw:
        raw = pick_folder()
        if not raw:
            print("没有选择文件夹。")
            return 1
    root = resolve_root(raw)
    pages, meta = load_report(root)
    if not pages:
        raise SystemExit("报告里没有读到页面数据。")
    output = Path(args.output).expanduser().resolve() if args.output else root / "中文结论.html"
    output.write_text(render(pages, meta, root), encoding="utf-8")
    print(f"已生成：{output}")
    if not args.no_open:
        if sys.platform == "win32":
            os.startfile(output)  # noqa: S606
        else:
            webbrowser.open(output.as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
