#!/bin/bash

# Default directories
OUTPUT_DIR="$(pwd)"
APPTAINER_TMPDIR="$(pwd)"
DOCKERFILE=""

# Function to display usage information
usage() {
    echo "Usage: $0 [-o OUTPUT_DIR] [-t TEMP_DIR] [-d DOCKERFILE] [-h]"
    echo
    echo "Options:"
    echo "  -o OUTPUT_DIR   Specify the output directory for the Apptainer image."
    echo "                  Default is the current directory."
    echo "  -t TEMP_DIR     Specify the temporary directory for Apptainer build files."
    echo "                  Default is the current directory."
    echo "  -d DOCKERFILE   Provide a Dockerfile to build the container."
    echo "                  When specified, the script will use Docker to build an image"
    echo "                  from this Dockerfile and then convert it to an Apptainer image."
    echo "  -h              Display this help message and exit."
    echo
    echo "Examples:"
    echo "  Build from Docker Hub:"
    echo "    $0 -o /data/local/container/qsiprep -t /data/local/container/apptainer_tmp"
    echo
    echo "  Build from a Dockerfile:"
    echo "    $0 -d /path/to/Dockerfile -o /data/local/container/custom"
    exit 1
}

# Parse command-line options
while getopts ":o:t:d:h" opt; do
  case $opt in
    o)
      OUTPUT_DIR="$OPTARG"
      ;;
    t)
      APPTAINER_TMPDIR="$OPTARG"
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

# Set the temporary directory for Apptainer
export APPTAINER_CACHEDIR="$APPTAINER_TMPDIR"

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

    # Convert temporary directory to an absolute path
    APPTAINER_TMPDIR=$(realpath "$APPTAINER_TMPDIR")
    
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

    # Log file for build output
    LOG_FILE="${OUTPUT_PATH%.sif}.log"

    echo "Converting Docker image '${IMAGE_NAME}:latest' to Apptainer image..."
    if apptainer build --tmpdir="$APPTAINER_TMPDIR" "$OUTPUT_PATH" "docker-daemon://${IMAGE_NAME}:latest" &> "$LOG_FILE"; then
         echo "Apptainer image built successfully at: $OUTPUT_PATH"
         exit 0
    else
         echo "Failed to build Apptainer image. Check log file: $LOG_FILE"
         exit 1
    fi
fi

# --- Docker Hub Branch ---

# Prompt user for Docker image repository (e.g., 'pennbbl/qsiprep')
read -p "Enter Docker image repository (e.g., 'pennbbl/qsiprep'): " DOCKER_REPO

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
echo "Available tags for '${DOCKER_REPO}':"
select TAG in "${TAGS[@]}"; do
    if [[ -n "$TAG" ]]; then
        echo "You selected tag: $TAG"
        break
    else
        echo "Invalid selection. Please try again."
    fi
done

# Define the output path for the Apptainer image
OUTPUT_PATH="${OUTPUT_DIR}/${IMAGE_NAME}_${TAG}.sif"

# Create the output directory if it doesn't exist
mkdir -p "$OUTPUT_DIR"

# Build the Apptainer image and log output
LOG_FILE="${OUTPUT_PATH%.sif}.log"
if apptainer build --tmpdir="$APPTAINER_TMPDIR" "$OUTPUT_PATH" "docker://${DOCKER_REPO}:${TAG}" &> "$LOG_FILE"; then
    echo "Apptainer image built successfully at: $OUTPUT_PATH"
else
    echo "Failed to build Apptainer image. Check log file: $LOG_FILE"
    exit 1
fi
