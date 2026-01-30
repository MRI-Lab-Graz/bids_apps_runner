import re

output = """
usage: qsiprep [-h] [--skip-bids-validation]
               [--output-resolution RESOLUTION]
               [--participant-label PARTICIPANT_LABEL [PARTICIPANT_LABEL ...]]
               
Options for workflow:
  --output-resolution {1.2,2,3}
                        Output resolution. (default: 1.25)
  --participant-label PARTICIPANT_LABEL [PARTICIPANT_LABEL ...]
                        One or more participant identifiers.

Options for filtering BIDS queries:
  --skip-bids-validation
                        Skip BIDS validation.
"""

def parse(output):
    parts = re.split(r'\n(?=[A-Z][^:]+:)', output)
    sections = []
    for part in parts:
        lines = part.strip().split('\n')
        if not lines: continue
        header = lines[0].strip().rstrip(':')
        if "usage" in header.lower(): continue
        content = '\n'.join(lines[1:])
        if '--' not in content: continue

        options = []
        # Fix: Better splitting of argument blocks
        arg_blocks = re.split(r'\n\s+(?=--)', "\n  " + content)
        
        for block in arg_blocks:
            flag_match = re.search(r'(--[a-zA-Z0-9-]+)', block)
            if not flag_match: continue
            flag = flag_match.group(1)
            
            choices = []
            choice_match = re.search(r'\{([^}]+)\}', block)
            if choice_match:
                choices = [c.strip() for c in choice_match.group(1).split(',')]
            
            # IMPROVED: Clean up description parsing
            # The description usually starts after the flag/metavar block
            # In argparse, it's often separated by multiple spaces or a newline+indent
            
            # Find the first line after the flag line
            block_lines = block.split('\n')
            if len(block_lines) > 1:
                description = " ".join([l.strip() for l in block_lines[1:]])
            else:
                description = ""
            
            description = re.sub(r'\s+', ' ', description)
            
            options.append({
                'flag': flag,
                'description': description
            })
        if options:
            sections.append({'title': header, 'options': options})
    return sections

import json
print(json.dumps(parse(output), indent=2))
