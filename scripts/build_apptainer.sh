#!/bin/bash

# User-supplied directories (mandatory)
OUTPUT_DIR=""
APPTAINER_TMPDIR=""
DOCKERFILE=""
DOCKER_REPO_OVERRIDE=""
DOCKER_TAG_OVERRIDE=""

NO_TEMP_DEL=false
APPTAINER_BASE_TMPDIR=""
APPTAINER_RUN_TMPDIR=""

OUTPUT_SET=false
TMP_SET=false

cleanup_run_tmpdir_on_success() {
    local run_tmpdir="$1"

    if [ "$NO_TEMP_DEL" = true ]; then
        echo "🧷 Keeping per-build temp dir (requested): $run_tmpdir"
        return 0
    fi
    if [ -z "$run_tmpdir" ] || [ ! -d "$run_tmpdir" ]; then
        return 0
    fi

    local run_real
    run_real=$(realpath "$run_tmpdir" 2>/dev/null) || return 0

    # Safety guards: never delete dangerous paths.
    if [ -z "$run_real" ] || [ "$run_real" = "/" ]; then
        echo "⚠️  Refusing to delete per-build temp dir: '$run_tmpdir' (resolved to '$run_real')."
        return 0
    fi
    if [ -n "$APPTAINER_BASE_TMPDIR" ] && [ "$run_real" = "$APPTAINER_BASE_TMPDIR" ]; then
        echo "⚠️  Refusing to delete base temp dir: '$run_real'."
        return 0
    fi

    echo "🧹 Deleting per-build temp dir: $run_real"
    rm -rf -- "${run_real:?}" 2>/dev/null || true
}

# Function to display usage information
usage() {
    echo
    echo "====================================================================="
    echo "  MRI-Lab Graz (Karl Koschutnig) - BIDS Apptainer Builder 🧠 🏗️"
    echo "====================================================================="
    echo
    echo "Usage: $0 -o OUTPUT_DIR -t TEMP_DIR [--no-temp-del] [-d DOCKERFILE] [-h]"
    echo
    echo "Options ⚙️ :"
    echo "  -o OUTPUT_DIR   📂 (required) Output directory for the Apptainer image (.sif)."
    echo "  -t TEMP_DIR     🗑️  (required) Temporary directory for Apptainer build files."
    echo "  --no-temp-del   🧷 Keep the per-build temp folder after a successful build."
    echo "  -d DOCKERFILE   🐳 Provide a Dockerfile to build the container."
    echo "                  When specified, the script will use Docker to build an image"
    echo "                  from this Dockerfile and then convert it to an Apptainer image."
    echo "  --docker-repo   📦 (non-interactive) Docker repository to convert (e.g., nipreps/fmriprep)"
    echo "  --docker-tag    🏷️  Tag to convert (when repo provided, default selection is skipped)"
    echo "  -h              ℹ️  Display this help message and exit."
    echo
    echo "Examples 💡:"
    echo "  Build from Docker Hub (interactive app + tag selection):"
    echo "    $0 -o /data/local/container/qsiprep -t /data/local/container/apptainer_tmp"
    echo
    echo "  Build from a Dockerfile (no interactive tag selection):"
    echo "    $0 -d /path/to/Dockerfile -o /data/local/container/custom -t /tmp/apptainer"
    exit 1
}

# Function to display a spinner
spinner() {
    local pid=$1
    local delay=0.1
    local spinstr='|/-\'
    echo -n " "
    while kill -0 $pid 2>/dev/null; do
        local temp=${spinstr#?}
        printf " [%c]  " "$spinstr"
        local spinstr=$temp${spinstr%"$temp"}
        sleep $delay
        printf "\b\b\b\b\b\b"
    done
    printf "    \b\b\b\b"
}

# Check if no arguments provided
if [ $# -eq 0 ]; then
    usage
fi

# Pre-parse long options (getopts doesn't support them)
ARGS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --no-temp-del)
            NO_TEMP_DEL=true
            shift
            ;;
        --docker-repo=*)
            DOCKER_REPO_OVERRIDE="${1#--docker-repo=}"
            shift
            ;;
        --docker-repo)
            shift
            DOCKER_REPO_OVERRIDE="$1"
            shift
            ;;
        --docker-tag=*)
            DOCKER_TAG_OVERRIDE="${1#--docker-tag=}"
            shift
            ;;
        --docker-tag)
            shift
            DOCKER_TAG_OVERRIDE="$1"
            shift
            ;;
        *)
            ARGS+=("$1")
            shift
            ;;
    esac
done
set -- "${ARGS[@]}"

# Parse command-line options
while getopts ":o:t:d:h" opt; do
  case $opt in
    o)
      OUTPUT_DIR="$OPTARG"
    OUTPUT_SET=true
      ;;
    t)
      APPTAINER_TMPDIR="$OPTARG"
    TMP_SET=true
      ;;
    d)
      DOCKERFILE="$OPTARG"
      ;;
    h)
      usage
      ;;
    \?)
      echo "Invalid option: -$OPTARG" >&2
      usage
      ;;
  esac
done

# Require mandatory options
if [ "$OUTPUT_SET" != true ] || [ "$TMP_SET" != true ]; then
    echo "Error: Both -o (output dir) and -t (temporary dir) are required."
    usage
fi

echo
echo "====================================================================="
echo "  MRI-Lab Graz (Karl Koschutnig) - BIDS Apptainer Builder 🧠 🏗️"
echo "====================================================================="
echo
echo "You will select the Docker image (BIDS App) and tag to convert into an Apptainer image."
echo "Using output dir: $OUTPUT_DIR"
echo "Using temp dir:   $APPTAINER_TMPDIR"
echo

# Check if Apptainer is installed
if ! command -v apptainer &> /dev/null; then
    echo "Error: Apptainer is not installed. Please install Apptainer before running this script."
    exit 1
fi

# Check if curl is installed (needed for Docker Hub branch)
if ! command -v curl &> /dev/null; then
    echo "Error: curl is not installed. Please install curl before running this script."
    exit 1
fi

# Check if jq is installed (needed for Docker Hub branch)
if ! command -v jq &> /dev/null; then
    echo "Error: jq is not installed. Please install jq before running this script."
    exit 1
fi

# Ensure OUTPUT_DIR exists and is a directory
if [ -e "$OUTPUT_DIR" ] && [ ! -d "$OUTPUT_DIR" ]; then
    echo "Error: Output path '$OUTPUT_DIR' exists and is not a directory."
    exit 1
fi

if [ ! -d "$OUTPUT_DIR" ]; then
    mkdir -p "$OUTPUT_DIR" || { echo "Error: Failed to create output directory '$OUTPUT_DIR'."; exit 1; }
fi

# Ensure APPTAINER_TMPDIR exists and is a directory
if [ -e "$APPTAINER_TMPDIR" ] && [ ! -d "$APPTAINER_TMPDIR" ]; then
    echo "Error: Temporary path '$APPTAINER_TMPDIR' exists and is not a directory."
    exit 1
fi

if [ ! -d "$APPTAINER_TMPDIR" ]; then
    mkdir -p "$APPTAINER_TMPDIR" || { echo "Error: Failed to create temporary directory '$APPTAINER_TMPDIR'."; exit 1; }
fi

# Validate that OUTPUT_DIR is writable
if [ ! -w "$OUTPUT_DIR" ]; then
    echo "Error: Output directory '$OUTPUT_DIR' is not writable."
    exit 1
fi

# Validate that APPTAINER_TMPDIR is writable
if [ ! -w "$APPTAINER_TMPDIR" ]; then
    echo "Error: Temporary directory '$APPTAINER_TMPDIR' is not writable."
    exit 1
fi

# Create a per-build temp directory under the provided base temp dir (-t)
APPTAINER_BASE_TMPDIR=$(realpath "$APPTAINER_TMPDIR" 2>/dev/null)
if [ -z "$APPTAINER_BASE_TMPDIR" ] || [ "$APPTAINER_BASE_TMPDIR" = "/" ]; then
    echo "Error: Refusing to use unsafe temp directory: '$APPTAINER_TMPDIR'."
    exit 1
fi

APPTAINER_RUN_TMPDIR=$(mktemp -d -p "$APPTAINER_BASE_TMPDIR" "apptainer_build.XXXXXX")
if [ -z "$APPTAINER_RUN_TMPDIR" ] || [ ! -d "$APPTAINER_RUN_TMPDIR" ]; then
    echo "Error: Failed to create per-build temporary directory under '$APPTAINER_BASE_TMPDIR'."
    exit 1
fi

echo "Using per-build temp dir: $APPTAINER_RUN_TMPDIR"

# Set the temporary/cache directory for Apptainer to the per-build folder
export APPTAINER_CACHEDIR="$APPTAINER_RUN_TMPDIR"

# --- Dockerfile Branch ---
if [ -n "$DOCKERFILE" ]; then
    if [ ! -f "$DOCKERFILE" ]; then
         echo "Error: Dockerfile '$DOCKERFILE' does not exist."
         exit 1
    fi

    # Check if Docker is installed
    if ! command -v docker &> /dev/null; then
        echo "Error: Docker is not installed. Please install Docker to build from a Dockerfile."
        exit 1
    fi

    # Derive an image name from the Dockerfile filename and convert it to lowercase
    IMAGE_NAME=$(basename "$DOCKERFILE")
    IMAGE_NAME="${IMAGE_NAME%.*}"
    IMAGE_NAME=$(echo "$IMAGE_NAME" | tr '[:upper:]' '[:lower:]')
    
    # Determine the Docker build context (the directory containing the Dockerfile)
    DOCKER_CONTEXT=$(dirname "$DOCKERFILE")
    
    echo "Building Docker image from Dockerfile '$DOCKERFILE' using context '$DOCKER_CONTEXT'..."
    if docker build -f "$DOCKERFILE" -t "${IMAGE_NAME}:latest" "$DOCKER_CONTEXT"; then
         echo "Docker image '${IMAGE_NAME}:latest' built successfully."
    else
         echo "Docker build failed. Please check the output above."
         exit 1
    fi

    # Define the output path for the Apptainer image
    OUTPUT_PATH="${OUTPUT_DIR}/${IMAGE_NAME}.sif"

    # Create the output directory if it doesn't exist
    mkdir -p "$OUTPUT_DIR"

    if [ -n "$DOCKER_REPO_OVERRIDE" ]; then
        DOCKER_REPO="$DOCKER_REPO_OVERRIDE"
        echo "Using provided Docker repository: $DOCKER_REPO"
    else
        echo "Select a BIDS App (Docker Hub repo) to build 📦:"
        PS3="Please enter your choice (number): "
        select APP in "${APPS[@]}"; do
            if [[ "$APP" == "Custom" ]]; then
                read -p "Enter Docker image repository (e.g., 'pennbbl/qsiprep'): " DOCKER_REPO
                break
            elif [[ -n "$APP" ]]; then
                DOCKER_REPO="$APP"
                break
            else
                echo "❌ Invalid selection. Please try again."
            fi
        done
    fi
    # Log file for build output
    LOG_FILE="${OUTPUT_PATH%.sif}.log"

    echo "Converting Docker image '${IMAGE_NAME}:latest' to Apptainer image... 🔄"
    echo "   This may take a while. Please wait..."

    # --force allows rebuilding if a prior image file already exists
    apptainer build --force --tmpdir="$APPTAINER_RUN_TMPDIR" "$OUTPUT_PATH" "docker-daemon://${IMAGE_NAME}:latest" &> "$LOG_FILE" &
    BUILD_PID=$!
    spinner $BUILD_PID

    wait $BUILD_PID
    EXIT_CODE=$?

    if [ $EXIT_CODE -eq 0 ]; then
         echo "✅ Apptainer image built successfully at: $OUTPUT_PATH"
            cleanup_run_tmpdir_on_success "$APPTAINER_RUN_TMPDIR"
         exit 0
    else
         echo "❌ Failed to build Apptainer image. Check log file: $LOG_FILE"
         exit 1
    fi
fi

# --- Docker Hub Branch ---

# Predefined BIDS Apps
APPS=("nipreps/fmriprep" "pennbbl/qsiprep" "nipreps/mriqc" "freesurfer/freesurfer" "Custom")

echo "Select a BIDS App (Docker Hub repo) to build 📦:"
PS3="Please enter your choice (number): "
select APP in "${APPS[@]}"; do
    if [[ "$APP" == "Custom" ]]; then
        read -p "Enter Docker image repository (e.g., 'pennbbl/qsiprep'): " DOCKER_REPO
        break
    elif [[ -n "$APP" ]]; then
        DOCKER_REPO="$APP"
        break
    else
        echo "❌ Invalid selection. Please try again."
    fi
done

echo "Chosen Docker repository: ${DOCKER_REPO}"

# Extract the image name (e.g., 'qsiprep' from 'pennbbl/qsiprep')
IMAGE_NAME="${DOCKER_REPO##*/}"

# Initialize variables for tag fetching
TAGS=()
PAGE=1

# Fetch all tags, handling pagination
while true; do
    RESPONSE=$(curl -s "https://registry.hub.docker.com/v2/repositories/${DOCKER_REPO}/tags?page=${PAGE}&page_size=100")
    PAGE_TAGS=$(echo "$RESPONSE" | jq -r '.results[].name')
    TAGS+=($PAGE_TAGS)

    # Check if there are more pages
    NEXT=$(echo "$RESPONSE" | jq -r '.next')
    if [ "$NEXT" == "null" ]; then
        break
    else
        PAGE=$((PAGE + 1))
    fi
done

# Check if tags were retrieved successfully
if [ ${#TAGS[@]} -eq 0 ]; then
    echo "No tags found or failed to retrieve tags for repository '${DOCKER_REPO}'."
    exit 1
fi

# Present the list of tags to the user for selection
if [ -n "$DOCKER_TAG_OVERRIDE" ]; then
    TAG="$DOCKER_TAG_OVERRIDE"
    if ! printf '%s\n' "${TAGS[@]}" | grep -Fxq "$TAG"; then
        echo "Error: Provided tag '$TAG' not found for repository '${DOCKER_REPO}'."
        exit 1
    fi
    echo "Using provided tag: $TAG"
else
    echo "Available tags for '${DOCKER_REPO}':"
    select TAG in "${TAGS[@]}"; do
        if [[ -n "$TAG" ]]; then
            echo "You selected tag: $TAG"
            break
        else
            echo "Invalid selection. Please try again."
        fi
    done
fi

# Define the output path for the Apptainer image
OUTPUT_PATH="${OUTPUT_DIR}/${IMAGE_NAME}_${TAG}.sif"

# Create the output directory if it doesn't exist
mkdir -p "$OUTPUT_DIR"

# Build the Apptainer image and log output
LOG_FILE="${OUTPUT_PATH%.sif}.log"
echo "🚀 Starting Apptainer build for ${DOCKER_REPO}:${TAG}..."
echo "   This may take a while. Please wait..."

apptainer build --force --tmpdir="$APPTAINER_RUN_TMPDIR" "$OUTPUT_PATH" "docker://${DOCKER_REPO}:${TAG}" &> "$LOG_FILE" &
BUILD_PID=$!
spinner $BUILD_PID

wait $BUILD_PID
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "✅ Apptainer image built successfully at: $OUTPUT_PATH"
    cleanup_run_tmpdir_on_success "$APPTAINER_RUN_TMPDIR"
    exit 0
else
    echo "❌ Failed to build Apptainer image. Check log file: $LOG_FILE"
    exit 1
fi
