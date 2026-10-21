#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""质性论文"报告要素"扫描器（无第三方依赖）。

用途：对一份质性论文稿件做机械核对——看方法节是否交代了该交代的东西，
以及是否出现了已知的失分表述。它不评价研究质量，只报告"有没有写"。

用法：
    python quality_scan.py 稿件.md
    python quality_scan.py 稿件.txt --json
    python quality_scan.py 稿件.md --quiet     # 只输出缺失项

说明：
    - 支持 .txt / .md / .markdown（UTF-8，失败时回退 GBK）。
    - .docx 请先另存为 .txt 或 .md 再扫描。
    - 关键词命中只代表"文中出现过相关表述"，不等于"做到了"；须人工复核。
"""

import argparse
import json
import os
import re
import sys

# (键, 显示名, 正则组, 是否必需)
CHECKS = [
    ("orientation", "取向与理由",
     r"(主题分析|扎根理论|现象学|民族志|网络民族志|案例研究|叙事分析|话语分析|内容分析|定性比较分析|过程追踪|行动研究|thematic analysis|grounded theory|phenomenolog|ethnograph|case study|narrative|discourse analysis|content analysis)",
     True),
    ("paradigm", "范式/认识论立场",
     r"(范式|认识论|本体论|建构主义|批判实在论|后实证|诠释|paradigm|epistemolog|ontolog|constructivis)",
     True),
    ("sampling", "抽样逻辑与维度",
     r"(目的性抽样|立意抽样|理论抽样|滚雪球|最大变异|分层|purposive|theoretical sampling|snowball|maximum variation|stratified)",
     True),
    ("samplesize", "样本量与停止判据",
     r"(饱和|信息力量|saturation|information power|样本量|sample size|停止|不再出现新)",
     True),
    ("collection", "收集过程（时长/媒介/录音）",
     r"(半结构|半结构化|深度访谈|焦点小组|参与观察|时长|分钟|小时|录音|转写|转录|semi-structured|in-depth interview|focus group|observation|audio|transcri)",
     True),
    ("analysis", "分析步骤（编码轮次/从码到主题）",
     r"(开放编码|初始编码|聚焦编码|轴向编码|编码本|代码本|备忘录|主题地图|\d+\s*轮编码|编码|coding|codebook|memo|theme map)",
     True),
    ("quality", "质量动作（可信性/审计轨迹）",
     r"(可信性|可迁移性|可依赖性|可确证性|三角验证|审计轨迹|同行审议|负面个案|反例|credibility|transferability|dependability|confirmability|triangulat|audit trail|peer debrief|negative case|disconfirming)",
     True),
    ("reflexivity", "反身性",
     r"(反身|反思自身|研究者立场|位置性|reflexiv|positionality|situated)",
     True),
    ("ethics", "伦理（同意/匿名/审查）",
     r"(知情同意|伦理审查|伦理委员会|匿名|化名|保密|informed consent|ethics (review|committee|approval)|anonym|confidential)",
     True),
    ("software", "软件与工具（用了/没做什么）",
     r"(NVivo|Nvivo|nvivo|ATLAS|MAXQDA|Dedoose|Krippendorff|软件|CAQDAS)",
     False),
    ("ai", "AI 使用披露",
     r"(生成式|大语言模型|语言模型|ChatGPT|GPT|LLM|人工智能辅助|AI ?辅助|algorithm)",
     False),
    ("limitation", "局限与不声称什么",
     r"(局限|限制|不声称|无法|不足以|limitation|not generaliz|caveat)",
     True),
]

RED_FLAGS = [
    ("主题浮现", r"主题(自然)?浮现|themes? emerged|emerged from the data",
     "改用主动表述：主题由研究者在数据、理论假设与技艺交汇处生成"),
    ("成员核查当验真", r"成员核查|member check",
     "在建构主义框架下宜改为「成员反思（member reflection）」，其目的是产生新资料而非验真"),
    ("编码者间信度用于反身性取向", r"编码者间信度|inter-?coder reliability|Kappa|Cohen",
     "先确认所属取向；反身性取向不宜以一致率作为质量判据"),
    ("以饱和代替论证", r"达到饱和|数据饱和|saturation was reached",
     "补「单位—判据—阈值—设计者」四要素"),
    ("可推广性表述过强", r"可以推广到|具有普遍意义|代表全国|generalizab",
     "改为分析性概括或可迁移性表述，并说明结论层级"),
    ("主题数量清单式呈现", r"共(计)?\s*(获得|提炼|形成)?\s*\d+\s*个主题",
     "检查这些「主题」是否有统合性核心概念，避免把编码当主题"),
]

NUM_PATTERNS = [
    ("受访者人数", r"(\d+)\s*(名|位|个)?\s*(受访者|参与者|被访者|informants?|participants?)"),
    ("访谈次数", r"(\d+)\s*(次|个)?\s*(访谈|interview)"),
    ("焦点小组场次", r"(\d+)\s*(场|个)?\s*(焦点小组|focus group)"),
    ("主题数量", r"(\d+)\s*(个)?\s*主题"),
    ("编码数量", r"(\d+)\s*(个)?\s*(码|编码|codes?)"),
]


def read_text(path):
    for enc in ("utf-8", "utf-8-sig", "gbk"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read(), enc
        except UnicodeDecodeError:
            continue
    raise SystemExit("无法解码文件：请另存为 UTF-8 后重试。")


def snippet(text, m, width=28):
    s = max(0, m.start() - width)
    t = text[s:m.end() + width].replace("\n", " ")
    return re.sub(r"\s+", " ", t).strip()


def scan(text):
    result = {"checks": [], "flags": [], "numbers": {}}
    for key, label, pat, required in CHECKS:
        ms = list(re.finditer(pat, text, flags=re.I))
        result["checks"].append({
            "key": key, "label": label, "required": required,
            "found": bool(ms), "hits": len(ms),
            "sample": snippet(text, ms[0]) if ms else "",
        })
    for label, pat, advice in RED_FLAGS:
        ms = list(re.finditer(pat, text, flags=re.I))
        if ms:
            result["flags"].append({
                "label": label, "hits": len(ms),
                "sample": snippet(text, ms[0]), "advice": advice,
            })
    for label, pat in NUM_PATTERNS:
        vals = sorted({int(x.group(1)) for x in re.finditer(pat, text, flags=re.I)})
        if vals:
            result["numbers"][label] = vals
    body = re.sub(r"\s+", "", text)
    result["stats"] = {
        "chars_no_space": len(body),
        "lines": text.count("\n") + 1,
        "paragraphs": len([p for p in re.split(r"\n\s*\n", text) if p.strip()]),
    }
    return result


def main():
    ap = argparse.ArgumentParser(description="质性论文报告要素扫描（只报有没有写，不评质量）")
    ap.add_argument("path", help="稿件路径（.txt/.md）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--quiet", action="store_true", help="只列缺失项与红旗")
    args = ap.parse_args()

    if not os.path.isfile(args.path):
        raise SystemExit("文件不存在：%s" % args.path)
    if args.path.lower().endswith(".docx"):
        raise SystemExit("请先把 .docx 另存为 .txt 或 .md 再扫描。")

    text, enc = read_text(args.path)
    r = scan(text)

    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0

    st = r["stats"]
    print("文件：%s（编码 %s）" % (os.path.basename(args.path), enc))
    print("规模：%d 字（不含空白）／%d 行／约 %d 段" % (st["chars_no_space"], st["lines"], st["paragraphs"]))
    print("")

    missing = [c for c in r["checks"] if c["required"] and not c["found"]]
    shown = [c for c in r["checks"] if c["required"]] if not args.quiet else missing
    print("【报告要素】")
    for c in shown:
        mark = "缺失" if not c["found"] else "已写(%d 处)" % c["hits"]
        print("  %-28s %s" % (c["label"], mark))
        if c["found"] and not args.quiet and c["sample"]:
            print("      例：%s" % c["sample"][:80])

    optional = [c for c in r["checks"] if not c["required"] and c["found"]]
    if optional and not args.quiet:
        print("\n【选报项（已出现）】")
        for c in optional:
            print("  %-28s 已写(%d 处)" % (c["label"], c["hits"]))

    if r["flags"]:
        print("\n【需要复核的表述】")
        for f in r["flags"]:
            print("  ! %s（%d 处）" % (f["label"], f["hits"]))
            print("    原文：%s" % f["sample"][:90])
            print("    建议：%s" % f["advice"])

    if r["numbers"] and not args.quiet:
        print("\n【文中出现的数量表述】（核对单位是否一致）")
        for k, v in r["numbers"].items():
            print("  %-12s %s" % (k, v))

    print("\n结论：%s" % ("必报项已齐全（仍需人工核对内容是否属实）" if not missing
                         else "有 %d 项必报要素未见表述：%s" % (
                             len(missing), "、".join(c["label"] for c in missing))))
    print("提醒：本工具只做关键词级核对，不判断研究质量；命中也可能是空话。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
