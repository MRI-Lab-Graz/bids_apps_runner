#!/usr/bin/env python3
import os
import json
import argparse
from pathlib import Path

def fix_intended_for(bids_root, subject_label):
    """
    Finds all fmap JSON files for a specific subject and fixes the IntendedFor field.
    Removes 'bids::sub-<label>/' prefix.
    """
    subject_dir = Path(bids_root) / f"sub-{subject_label}"
    if not subject_dir.exists():
        print(f"Error: Subject directory {subject_dir} not found.")
        return

    json_files = list(subject_dir.glob("**/fmap/*.json"))
    if not json_files:
        print(f"No fmap JSON files found for sub-{subject_label}.")
        return

    for json_file in json_files:
        print(f"Checking {json_file}...")
        try:
            with open(json_file, 'r') as f:
                data = json.load(f)

            if "IntendedFor" in data:
                intended_for = data["IntendedFor"]
                if isinstance(intended_for, str):
                    intended_for = [intended_for]
                
                new_intended_for = []
                modified = False
                
                for path in intended_for:
                    if path.startswith("bids::"):
                        # Remove bids:: prefix
                        new_path = path.replace("bids::", "")
                        # Remove sub-XXXXX/ if it exists at the start
                        if new_path.startswith(f"sub-{subject_label}/"):
                            new_path = new_path.replace(f"sub-{subject_label}/", "")
                        
                        new_intended_for.append(new_path)
                        modified = True
                        print(f"  Fixed: {path} -> {new_path}")
                    else:
                        new_intended_for.append(path)
                
                if modified:
                    data["IntendedFor"] = new_intended_for
                    with open(json_file, 'w') as f:
                        json.dump(data, f, indent=4)
                    print(f"  Updated {json_file}")
                else:
                    print(f"  No 'bids::' prefix found in {json_file}")
            else:
                print(f"  No 'IntendedFor' field in {json_file}")
        except Exception as e:
            print(f"  Error processing {json_file}: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fix BIDS IntendedFor field by removing bids:: prefix.")
    parser.add_argument("bids_root", help="Root of the BIDS dataset")
    parser.add_argument("subject", help="Subject label (without sub-)")
    
    args = parser.parse_args()
    fix_intended_for(args.bids_root, args.subject)
