#!/usr/bin/env python3
"""Builds tests/fixtures/sample_data_doc.pdf — a synthetic ICORE 2026-round
Data-document fixture, written to match the exact label/caption wording
extract_icore.py's regexes look for (SPEC.md §6.4). Not a real ICORE PDF —
built because none exists in this repo — but every line here mirrors the
verified field format from the SPEC. Re-run this file to regenerate the
fixture if extract_icore.py's patterns change.
"""
from pathlib import Path

import pymupdf

OUT = Path(__file__).parent / "sample_data_doc.pdf"

PAGE1 = """\
INITIAL DETAILS

Title: Fixture International Conference on Testing
Acronym: FICT
Rank: A
Requested Rank: A*

Size in terms of full published research papers each year (most recent first): 40, 38, 35
Estimated number of attendees: 200-500
Number of submitted papers each year (most recent first) 120, 115, 100
Acceptance rates (most recent first): 33%, 33%, 35%
H-index of highest ranked program/general chair each year (most recent first) 45, 44, 40
"""

PAGE2 = """\
IMPACT

Citation Centiles

Information contained within these graphs is derived using the Elsevier Scopus Database 2025.
Percentages are calculated separately for 3 consecutive years (2022, 2023, 2024)
The percentage of papers in each rank within the relevant
Field of Research code(s) 4606.

FICT has 30% papers in the top 25% overall. A* papers have 66%, A papers
have 43%, B papers have 22%, C papers have 11%.

Google Scholar Data

h5 index: 42
h5 index of 20th item in category: 55
Position in sub-category: 21st
Sub-category url: https://scholar.google.com/citations?view_op=top_venues&vq=eng_fictionalcategory

Author strength

FICT has 28% papers in the top 25% overall. A* papers have 60%, A papers
have 40%, B papers have 20%, C papers have 10%.
"""

PAGE3 = """\
PROGRAM COMMITTEE DATA

Median h-index of the members of the whole committee: 18.5
12 of the 30 recognised PC members were classed as established
Top 3 venues where PC established researchers publish: FICT, OtherConf, ThirdConf
This conference was in position 3 for PC established researchers

EXTENT OF STRONG PEOPLE INVOLVED

20 Area Leaders were chosen by the submitters, using the following selection method:
top-cited authors in the field. The intent and instruction was to identify leaders
by citation count.

Jane A. Smith (52); John B. Doe (48); Ana C. Garcia (44);

Top 3 venues where the selected leaders publish: FICT, LeaderConf, OtherConf
This conference was in position 2 for the selected leaders
"""

PAGE4 = """\
ADDITIONAL INFORMATION

Conference Details

Most Recent Year
Year: 2025
Location: Sydney, Australia
Papers submitted: 120
Papers published: 40
Acceptance rate: 33%

Second Most Recent Year
Year: 2024
Location: Auckland, New Zealand
Papers submitted: 115
Papers published: 38
Acceptance rate: 33%

Relationship to similar conferences
FICT is the flagship venue for testing research and has no direct
alternative venue covering the same scope.
Flagship area of this conference
FICT defines the flagship area of software testing research internationally.
Other Information
Other relevant information: FICT has run continuously since 2005 with
consistently strong attendance.

Attachments

Linked artifact: https://portal.core.edu.au/core/media/2025/wpp_reports/fict_wpp.txt
"""


def build():
    doc = pymupdf.open()
    for text in [PAGE1, PAGE2, PAGE3, PAGE4]:
        page = doc.new_page()
        page.insert_text((36, 50), text, fontsize=10, fontname="helv")
    doc.set_metadata({"creator": "LaTeX with hyperref"})
    doc.save(OUT)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    build()
