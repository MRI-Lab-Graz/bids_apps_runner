#!/bin/bash

# BIDS DataLad Repository Management Script
# This script helps manage BIDS datasets and output repositories with DataLad

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
    echo "Usage: $0 [COMMAND] [OPTIONS]"
    echo ""
    echo "Commands:"
    echo "  init-input      Initialize a new BIDS input repository"
    echo "  init-output     Initialize a new output repository" 
    echo "  setup-sibling   Setup a sibling repository (for backup/sharing)"
    echo "  merge-results   Merge results from different processing branches"
    echo "  cleanup         Clean up temporary branches and data"
    echo "  status          Show repository status and data usage"
    echo ""
    echo "Options:"
    echo "  -r, --repo PATH     Repository path"
    echo "  -s, --sibling URL   Sibling repository URL"
    echo "  -b, --branch NAME   Branch name"
    echo "  -h, --help          Show this help"
    echo ""
    echo "Examples:"
    echo "  $0 init-input -r /data/bids/my_study"
    echo "  $0 init-output -r /data/outputs/qsirecon_results"
    echo "  $0 merge-results -r /data/outputs/qsirecon_results"
    echo "  $0 setup-sibling -r /data/bids/my_study -s git@github.com:lab/study.git"
}

init_input_repo() {
    local repo_path="$1"
    
    echo "Initializing BIDS input repository at: $repo_path"
    
    if [ ! -d "$repo_path" ]; then
        mkdir -p "$repo_path"
    fi
    
    cd "$repo_path"
    
    # Initialize DataLad dataset
    datalad create -c yoda .
    
    # Create basic BIDS structure
    mkdir -p derivatives
    
    # Create .bidsignore
    cat > .bidsignore << EOF
# BIDS ignore file
derivatives/
sourcedata/
*.log
.git/
EOF
    
    # Create dataset_description.json template
    cat > dataset_description.json << EOF
{
    "Name": "BIDS Dataset",
    "BIDSVersion": "1.8.0",
    "DatasetType": "raw",
    "Authors": [""],
    "License": "",
    "Acknowledgements": "",
    "HowToAcknowledge": "",
    "DatasetDOI": "",
    "Funding": [""],
    "ReferencesAndLinks": [""],
    "GeneratedBy": [
        {
            "Name": "Manual",
            "Version": "",
            "Description": ""
        }
    ]
}
EOF
    
    # Create README
    cat > README.md << EOF
# BIDS Dataset

This is a BIDS (Brain Imaging Data Structure) dataset managed with DataLad.

## Dataset Organization

This dataset follows the BIDS specification version 1.8.0.

## DataLad Usage

To get all data:
\`\`\`bash
datalad get .
\`\`\`

To get data for a specific subject:
\`\`\`bash
datalad get sub-<subject_id>
\`\`\`

## Processing

This dataset can be processed using the BIDS Apps Runner scripts in this repository.
EOF
    
    # Save initial state
    datalad save -m "Initialize BIDS dataset structure"
    
    echo "BIDS input repository initialized successfully"
}

init_output_repo() {
    local repo_path="$1"
    
    echo "Initializing output repository at: $repo_path"
    
    if [ ! -d "$repo_path" ]; then
        mkdir -p "$repo_path"
    fi
    
    cd "$repo_path"
    
    # Initialize DataLad dataset
    datalad create -c yoda .
    
    # Create derivatives structure
    mkdir -p derivatives
    
    # Create README
    cat > README.md << EOF
# BIDS Processing Outputs

This repository contains processed outputs from BIDS Apps.

## Structure

- \`derivatives/\`: Processed outputs organized by pipeline
- \`logs/\`: Processing logs and job information
- \`quality_control/\`: QC reports and metrics

## Branches

- \`main\`: Stable, verified results
- \`results\`: Latest processing outputs
- \`processing-sub-*\`: Subject-specific processing branches

## DataLad Usage

To get all results:
\`\`\`bash
datalad get .
\`\`\`

To get results for a specific pipeline:
\`\`\`bash
datalad get derivatives/<pipeline_name>
\`\`\`
EOF
    
    # Create logs directory
    mkdir -p logs
    
    # Save initial state
    datalad save -m "Initialize output repository structure"
    
    echo "Output repository initialized successfully"
}

setup_sibling() {
    local repo_path="$1"
    local sibling_url="$2"
    local sibling_name="${3:-origin}"
    
    echo "Setting up sibling repository: $sibling_url"
    
    cd "$repo_path"
    
    # Add sibling
    datalad siblings add -s "$sibling_name" --url "$sibling_url"
    
    # Configure git-annex
    git annex enableremote "$sibling_name"
    
    echo "Sibling repository configured successfully"
}

merge_results() {
    local repo_path="$1"
    local target_branch="${2:-main}"
    
    echo "Merging results into $target_branch branch"
    
    cd "$repo_path"
    
    # Switch to target branch
    git checkout "$target_branch" || git checkout -b "$target_branch"
    
    # Find all processing branches
    processing_branches=$(git branch -a | grep "processing-sub-" | sed 's/^[ *]*//' | sed 's/remotes\/origin\///')
    
    if [ -z "$processing_branches" ]; then
        echo "No processing branches found"
        return
    fi
    
    echo "Found processing branches:"
    echo "$processing_branches"
    
    # Merge each processing branch
    for branch in $processing_branches; do
        echo "Merging branch: $branch"
        
        # Check if branch exists locally
        if git show-ref --verify --quiet "refs/heads/$branch"; then
            git merge "$branch" -m "Merge results from $branch"
        else
            echo "Branch $branch not found locally, skipping"
        fi
    done
    
    # Save merged results
    datalad save -m "Merge processing results into $target_branch"
    
    echo "Results merged successfully"
}

cleanup_branches() {
    local repo_path="$1"
    local keep_main="${2:-true}"
    
    echo "Cleaning up processing branches"
    
    cd "$repo_path"
    
    # Switch to main branch
    git checkout main 2>/dev/null || git checkout master 2>/dev/null || {
        echo "No main/master branch found"
        return 1
    }
    
    # Find processing branches
    processing_branches=$(git branch | grep "processing-sub-" | sed 's/^[ *]*//')
    
    if [ -z "$processing_branches" ]; then
        echo "No processing branches to clean up"
        return
    fi
    
    echo "Cleaning up branches:"
    echo "$processing_branches"
    
    # Delete processing branches
    for branch in $processing_branches; do
        echo "Deleting branch: $branch"
        git branch -D "$branch"
    done
    
    echo "Branch cleanup completed"
}

show_status() {
    local repo_path="$1"
    
    cd "$repo_path"
    
    echo "=== Repository Status ==="
    echo "Path: $(pwd)"
    echo "DataLad version: $(datalad --version | head -1)"
    echo ""
    
    echo "=== Git Status ==="
    git status --short
    echo ""
    
    echo "=== Branches ==="
    git branch -a
    echo ""
    
    echo "=== DataLad Status ==="
    datalad status
    echo ""
    
    echo "=== Disk Usage ==="
    du -sh .
    
    echo "=== Git-Annex Info ==="
    git annex info . 2>/dev/null || echo "No git-annex information available"
}

# Parse command line arguments
COMMAND=""
REPO_PATH=""
SIBLING_URL=""
BRANCH_NAME=""

while [[ $# -gt 0 ]]; do
    case $1 in
        init-input|init-output|setup-sibling|merge-results|cleanup|status)
            COMMAND="$1"
            shift
            ;;
        -r|--repo)
            REPO_PATH="$2"
            shift 2
            ;;
        -s|--sibling)
            SIBLING_URL="$2"
            shift 2
            ;;
        -b|--branch)
            BRANCH_NAME="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

# Execute command
case $COMMAND in
    init-input)
        if [ -z "$REPO_PATH" ]; then
            echo "Error: Repository path required"
            usage
            exit 1
        fi
        init_input_repo "$REPO_PATH"
        ;;
    init-output)
        if [ -z "$REPO_PATH" ]; then
            echo "Error: Repository path required"
            usage
            exit 1
        fi
        init_output_repo "$REPO_PATH"
        ;;
    setup-sibling)
        if [ -z "$REPO_PATH" ] || [ -z "$SIBLING_URL" ]; then
            echo "Error: Repository path and sibling URL required"
            usage
            exit 1
        fi
        setup_sibling "$REPO_PATH" "$SIBLING_URL"
        ;;
    merge-results)
        if [ -z "$REPO_PATH" ]; then
            echo "Error: Repository path required"
            usage
            exit 1
        fi
        merge_results "$REPO_PATH" "$BRANCH_NAME"
        ;;
    cleanup)
        if [ -z "$REPO_PATH" ]; then
            echo "Error: Repository path required"
            usage
            exit 1
        fi
        cleanup_branches "$REPO_PATH"
        ;;
    status)
        if [ -z "$REPO_PATH" ]; then
            REPO_PATH="."
        fi
        show_status "$REPO_PATH"
        ;;
    "")
        echo "Error: No command specified"
        usage
        exit 1
        ;;
    *)
        echo "Error: Unknown command: $COMMAND"
        usage
        exit 1
        ;;
esac
