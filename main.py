#!/usr/bin/env python3
"""도로포장 전문용어집 PDF에서 용어와 설명만 추출하여 CSV로 저장한다.

기본 사용법
-----------
python extract_pavement_terms.py hmec_glossary.pdf -o hmec_pavement_terms.csv

보다 넓은 범위(품질관리/지반/시험 지원용어 포함)
---------------------------------------------
python extract_pavement_terms.py hmec_glossary.pdf -o hmec_pavement_terms_broad.csv --mode broad

출력 CSV 열은 term, description 두 개뿐이다.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path
from typing import Iterable

import fitz  # PyMuPDF


# -----------------------------------------------------------------------------
# 1. 분류 기준
#    - strict: 포장 구조, 아스팔트/콘크리트 재료, 시공, 파손, 포장시험 중심
#    - broad : strict + 품질관리, 통계적 수락, 지반/재료 지원용어
#
# 필요할 경우 아래 표현을 추가/삭제하면 된다.
# -----------------------------------------------------------------------------
STRICT_TERM_PATTERNS = [
    # 포장 및 구조층
    r"\bpavement\b", r"\basphalt\b", r"\broadbed\b", r"\bbase course\b",
    r"\bsurface course\b", r"\bsubbase\b", r"\bsubgrade\b",
    r"\bstructural number\b", r"\bback calculation\b",
    r"\benhanced integrated climatic model\b", r"\bmechanistic[- ]empirical\b",
    r"\bMEPDG\b", r"\bESAL\b", r"\bload equivalency\b",
    r"\bload transfer efficiency\b", r"\baverage annual daily traffic\b",
    r"\baverage annual daily truck traffic\b", r"\bAADT\b", r"\bAADTT\b",

    # 아스팔트·콘크리트·골재 및 재생재료
    r"\basphalt concrete\b", r"\bhot mix asphalt\b", r"\bwarm mix asphalt\b",
    r"\breclaimed asphalt pavement\b", r"\bRAP\b", r"\bHIP\b",
    r"\bPortland cement concrete\b", r"\bPCC\b", r"\bCRCP\b",
    r"\bJPCP\b", r"\bJRCP\b", r"\bpervious concrete\b",
    r"\blean concrete\b", r"\bhigh-performance concrete\b",
    r"\baggregate\b", r"\baggregates\b", r"\bgravel\b", r"\bslag\b",
    r"\bpozzolan", r"\bfly ash\b", r"\badmixtures?\b",
    r"\brecycled aggregate\b", r"\bfines\b", r"\bblaine fineness\b",
    r"\bcement-treated\b",
    r"\bopen-graded aggregate base\b", r"\bpermeable base",

    # 플랜트·생산·시공
    r"\badditive silo\b", r"\bbatch tower\b", r"\bpugmill\b",
    r"\bdraindown\b", r"\bdensification\b", r"\bcompaction\b",

    # 파손 및 성능
    r"\bcracking\b", r"\brutting\b", r"\braveling\b", r"\bshoving\b",
    r"\bbleeding\b", r"\bflushing\b", r"\bfaulting\b", r"\bpunchouts?\b",
    r"\bfatigue resistance\b", r"\bseparation\b",

    # 시험·물성·설계 입력
    r"\bfalling weight deflectometer\b", r"\bFWD\b",
    r"\bCalifornia bearing ratio\b", r"\bCBR\b",
    r"\bHubbard-Field stability test\b",
    r"\bspecific gravity\b", r"\bvoids in mineral aggregate\b",
    r"\bvoids in total mix\b", r"\bVMA\b", r"\bVTM\b",
    r"\bductility\b", r"\brheology\b", r"\bviscoelasticity\b",
    r"\bshear susceptibility\b", r"\bvolatilization\b",
    r"\bcoefficient of thermal expansion\b", r"\bzero-stress temperature\b",
    r"\bmodulus of elasticity\b", r"\breliability\b", r"\btensile strength\b",
    r"\bcompressive strength\b", r"\bfatigue cracking\b",
    r"\bpermeability\b", r"\bsoundness\b", r"\bcleanliness\b",
    r"\bdeleterious materials\b", r"\bsieve\b", r"\bplasticity\b",

    # 토목섬유 보강
    r"\bgeogrid\b", r"\bgeotextile\b", r"\bgeosynthetic\b",

    # 기타 포장재료
    r"\bpitch\b", r"\btar\b",
]

BROAD_EXTRA_TERM_PATTERNS = [
    # 품질보증·수락·시방
    r"\bacceptable quality level\b", r"\bacceptance\b", r"\bacceptance plan\b",
    r"\badjusted payment\b", r"\bbuyer'?s risk\b", r"\bseller'?s risk\b",
    r"\bquality assurance\b", r"\bquality control\b", r"\bquality management\b",
    r"\bindependent assurance\b", r"\bpercent within limits\b",
    r"\bspecification", r"\bverification\b", r"\bvalidation\b",
    r"\bdispute resolution\b", r"\breliability\b",

    # 샘플링·통계적 품질관리
    r"\bsample\b", r"\bsampling\b", r"\blot\b", r"\bsublots?\b",
    r"\bcontrol charts?\b", r"\bcoefficient of variation\b",
    r"\bstandard deviation\b", r"\bvariance\b", r"\bprecision\b", r"\baccuracy\b",
    r"\bbias\b", r"\boutlier\b",

    # 지반 및 기초적 재료 지원용어
    r"\bAASHTO classification system\b", r"\bunified soil classification system\b",
    r"\bsoil mechanics\b", r"\bin-situ testing\b", r"\bcohesion\b",
    r"\bcreep\b", r"\berosion\b", r"\bliquefaction\b",
    r"\bcoarse grained\b", r"\bfine grained\b",
]

# 도로포장과 무관한 항목이 잘못 선택되는 것을 막기 위한 우선 제외 표현
EXCLUDE_TERM_PATTERNS = [
    r"\bamine blush\b", r"\bblistering\b", r"\bchalking\b",
    r"\bdry spray\b", r"\bfisheyes\b", r"\bmetallizing\b",
    r"\bmud cracking\b", r"\bpinpoint rusting\b", r"\brust undercutting\b",
    r"\bsagging\b", r"\bdrilled shaft foundation\b", r"\bpile foundation\b",
    r"\bspread footing\b", r"\bscour\b", r"\bNEPA\b", r"\bRCRA\b",
    r"\bwrought iron\b", r"\bsteel\b",
]

# 설명에 포장 관련 맥락이 명확하게 나타날 때 보조 판정에 사용
PAVEMENT_CONTEXT_PATTERNS = [
    r"\bpavement\b", r"\basphalt\b", r"\broadway\b", r"\broad\b",
    r"\bbase course\b", r"\bsurface course\b", r"\bsubbase\b", r"\bsubgrade\b",
    r"\bwheel path\b", r"\btraffic load", r"\bpaving mixture\b",
    r"\basphalt binder\b", r"\basphalt plant\b", r"\bPCC slab\b",
]


def compile_patterns(patterns: Iterable[str]) -> list[re.Pattern[str]]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


STRICT_RX = compile_patterns(STRICT_TERM_PATTERNS)
BROAD_RX = compile_patterns(BROAD_EXTRA_TERM_PATTERNS)
EXCLUDE_RX = compile_patterns(EXCLUDE_TERM_PATTERNS)
CONTEXT_RX = compile_patterns(PAVEMENT_CONTEXT_PATTERNS)


def normalize_pdf_text(text: str) -> str:
    """PDF 추출 과정에서 생길 수 있는 특수문자와 줄바꿈을 정리한다."""
    replacements = {
        "\u00ad": "",   # soft hyphen
        "\ufffe": "",   # 잘못 추출된 제어문자
        "\ufffd": "",   # replacement character
        "\xa0": " ",    # non-breaking space
        "\r": "\n",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)

    # 줄 끝 하이픈으로 분리된 단어를 다시 결합: "non-\nrandom" -> "nonrandom"
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text)
    return text


def extract_entries(pdf_path: Path) -> list[tuple[str, str]]:
    """PDF에서 '용어: 설명' 항목을 읽어 (용어, 설명) 목록으로 반환한다."""
    entries: list[tuple[str, str]] = []

    with fitz.open(pdf_path) as doc:
        for page_no, page in enumerate(doc, start=1):
            text = normalize_pdf_text(page.get_text("text"))

            cleaned_lines: list[str] = []
            for line in text.splitlines():
                stripped = line.strip()

                # 반복 머리말과 페이지 번호 제거
                if stripped == "HMEC Glossary":
                    continue
                if re.fullmatch(rf"{page_no}", stripped):
                    continue

                cleaned_lines.append(line.rstrip())

            page_text = "\n".join(cleaned_lines).strip()

            # 원문은 항목 사이에 빈 줄이 있으므로 빈 줄 단위로 분리
            blocks = re.split(r"\n\s*\n+", page_text)

            for block in blocks:
                block = re.sub(r"\s+", " ", block).strip()
                if not block or ":" not in block:
                    continue

                term, description = block.split(":", 1)
                term = term.strip(" -\t")
                description = description.strip()

                # 잘못 분리된 블록 방지
                if not term or not description:
                    continue
                if len(term) > 160:
                    continue

                entries.append((term, description))

    # 중복 용어 제거(처음 등장한 정의 유지)
    unique: dict[str, str] = {}
    for term, description in entries:
        key = re.sub(r"\s+", " ", term).strip().casefold()
        unique.setdefault(key, (term, description))

    return list(unique.values())


def matches_any(text: str, patterns: list[re.Pattern[str]]) -> bool:
    return any(rx.search(text) for rx in patterns)


def context_match_count(text: str) -> int:
    return sum(1 for rx in CONTEXT_RX if rx.search(text))


def is_pavement_entry(term: str, description: str, mode: str) -> bool:
    """용어와 설명을 바탕으로 도로포장 관련 항목인지 판정한다."""
    if matches_any(term, EXCLUDE_RX):
        return False

    if matches_any(term, STRICT_RX):
        return True

    if mode == "broad" and matches_any(term, BROAD_RX):
        return True

    # 용어 자체가 일반적이어도 설명에 포장 맥락이 강하면 포함
    # strict는 서로 다른 포장 맥락 2개 이상, broad는 1개 이상을 요구한다.
    needed = 2 if mode == "strict" else 1
    return context_match_count(description) >= needed


def write_csv(rows: list[tuple[str, str]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["term", "description"])
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="용어집 PDF에서 도로포장 용어와 설명을 추출해 CSV로 저장합니다."
    )
    parser.add_argument("pdf", type=Path, help="입력 PDF 경로")
    parser.add_argument(
        "-o", "--output", type=Path, default=Path("pavement_terms.csv"),
        help="출력 CSV 경로(기본값: pavement_terms.csv)",
    )
    parser.add_argument(
        "--mode", choices=["strict", "broad"], default="strict",
        help="strict=핵심 포장용어, broad=품질관리·지반 지원용어까지 포함",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not args.pdf.exists():
        print(f"오류: 입력 파일을 찾을 수 없습니다: {args.pdf}", file=sys.stderr)
        return 1

    try:
        all_entries = extract_entries(args.pdf)
        selected = [
            (term, description)
            for term, description in all_entries
            if is_pavement_entry(term, description, args.mode)
        ]
        selected.sort(key=lambda x: x[0].casefold())
        write_csv(selected, args.output)
    except Exception as exc:
        print(f"처리 중 오류가 발생했습니다: {exc}", file=sys.stderr)
        return 2

    print(f"전체 용어 수: {len(all_entries)}")
    print(f"선별된 도로포장 용어 수: {len(selected)}")
    print(f"저장 위치: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
