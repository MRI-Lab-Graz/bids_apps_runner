import re


def parse_help_sections(help_output: str):
    parts = re.split(r"\n(?=[A-Z][^:]+:)", help_output)
    sections = []

    for part in parts:
        lines = part.strip().split("\n")
        if not lines:
            continue

        header = lines[0].strip().rstrip(":")
        if "usage" in header.lower():
            continue

        content = "\n".join(lines[1:])
        if "--" not in content:
            continue

        options = []
        arg_blocks = re.split(r"\n\s+(?=--)", "\n  " + content)
        for block in arg_blocks:
            flag_match = re.search(r"(--[a-zA-Z0-9-]+)", block)
            if not flag_match:
                continue

            flag = flag_match.group(1)
            block_lines = block.split("\n")
            description = " ".join(line.strip() for line in block_lines[1:])
            description = re.sub(r"\s+", " ", description).strip()
            options.append({"flag": flag, "description": description})

        if options:
            sections.append({"title": header, "options": options})

    return sections


def test_parse_help_sections_extracts_flags_and_descriptions():
    output = """
usage: qsiprep [-h] [--skip-bids-validation]
               [--output-resolution RESOLUTION]

Options for workflow:
  --output-resolution {1.2,2,3}
                        Output resolution. (default: 1.25)
  --participant-label PARTICIPANT_LABEL [PARTICIPANT_LABEL ...]
                        One or more participant identifiers.

Options for filtering BIDS queries:
  --skip-bids-validation
                        Skip BIDS validation.
"""

    sections = parse_help_sections(output)

    assert [s["title"] for s in sections] == [
        "Options for workflow",
        "Options for filtering BIDS queries",
    ]
    assert sections[0]["options"][0]["flag"] == "--output-resolution"
    assert "Output resolution" in sections[0]["options"][0]["description"]
    assert sections[1]["options"][0]["flag"] == "--skip-bids-validation"


def test_parse_help_sections_ignores_text_without_flags():
    output = """
Description:
  This section has no argparse flags.

More Notes:
  Also plain text.
"""

    assert parse_help_sections(output) == []

