import re
import os
import sys
import json
import glob
import subprocess
import requests
from flask import Flask, render_template, request, jsonify
from waitress import serve
from pathlib import Path

app = Flask(__name__)
app.secret_key = "bids-app-runner-secret-key"
app.config['TEMPLATES_AUTO_RELOAD'] = True

# Base directory for the project
BASE_DIR = Path(__file__).resolve().parent

@app.before_request
def log_request_info():
    print(f"[GUI] {request.method} {request.path} from {request.remote_addr}", flush=True)

# Common BIDS Apps mapping to Docker Hub repos
APP_REPO_MAPPING = {
    'mriqc': 'nipreps/mriqc',
    'fmriprep': 'nipreps/fmriprep',
    'qsiprep': 'pennlinc/qsiprep',
    'nibabies': 'nipreps/nibabies',
    'mritools': 'bids/mritools',
    'freesurfer': 'freesurfer/freesurfer',
    'synthseg': 'freesurfer/synthseg'
}

def get_latest_version_from_dockerhub(repo):
    """Fetch the latest tag from Docker Hub for a given repo."""
    try:
        url = f"https://registry.hub.docker.com/v2/repositories/{repo}/tags?page_size=10&ordering=last_updated"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            # Filter out 'latest' and other non-version tags if possible, 
            # but usually the first one that looks like a version is what we want.
            tags = [t['name'] for t in data.get('results', [])]
            for tag in tags:
                # Basic check to avoid 'latest', 'stable', 'master', etc.
                if re.search(r'\d+\.\d+', tag):
                    return tag
        return None
    except Exception as e:
        print(f"[DEBUG] Error checking Docker Hub for {repo}: {e}")
        return None

@app.route('/check_container_version', methods=['POST'])
def check_container_version():
    container_path = request.json.get('container')
    if not container_path:
        return jsonify({'error': 'No container path provided'}), 400
    
    filename = os.path.basename(container_path)
    # Remove extension first
    filename_no_ext = os.path.splitext(filename)[0]
    
    # Common pattern: appname_version or appname-version
    match = re.search(r'^([a-zA-Z0-9-]+)[_-](v?\d+\.[\w\.-]+)', filename_no_ext)
    
    if not match:
        # Fallback: try to just guess app name from string
        app_name = None
        for key in APP_REPO_MAPPING.keys():
            if key in filename.lower():
                app_name = key
                break
        
        if not app_name:
            return jsonify({'info': 'Could not parse app name from filename'}), 200
        
        current_version = "unknown"
    else:
        app_name = match.group(1).lower()
        current_version = match.group(2).lstrip('v')
    
    repo = APP_REPO_MAPPING.get(app_name)
    if not repo:
        # Try a guess: bids/app_name
        repo = f"bids/{app_name}"
        
    latest_version = get_latest_version_from_dockerhub(repo)
    if not latest_version:
        # One more try if it's a known nipreps one
        if app_name in ['mriqc', 'fmriprep', 'nibabies']:
            repo = f"nipreps/{app_name}"
            latest_version = get_latest_version_from_dockerhub(repo)

    if latest_version:
        # Normalize versions for comparison
        clean_current = current_version.lower().replace(".sif", "").replace(".simg", "")
        clean_latest = latest_version.lower().lstrip('v')
        
        is_newer = clean_latest != clean_current
        return jsonify({
            'app': app_name,
            'current': current_version,
            'latest': latest_version,
            'is_newer': is_newer,
            'repo': repo,
            'changelog_url': f"https://github.com/{repo}/releases/tag/{latest_version if latest_version.startswith('v') else latest_version}"
        })
    
    return jsonify({'info': 'No newer version found or repo not identified'}), 200

# Global state to track if a job was started in this GUI session
GUI_SESSION_STARTED = False

@app.route('/get_log', methods=['GET'])
def get_log():
    try:
        # Find the most recent nohup log file
        log_files = glob.glob(str(BASE_DIR / "nohup_bids_runner_*.log"))
        if not log_files:
            return jsonify({'content': '', 'filename': 'none'}), 200
        
        latest_log = max(log_files, key=os.path.getctime)
        
        # Use tail to get last 150 lines efficiently
        result = subprocess.run(["tail", "-n", "150", latest_log], capture_output=True, text=True)
        content = result.stdout
        
        # Strip ANSI escape sequences (colors)
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        content = ansi_escape.sub('', content)
            
        return jsonify({
            'filename': os.path.basename(latest_log),
            'content': content
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/health')
def health():
    return "Flask is running and responding!", 200

@app.route('/get_app_help', methods=['POST'])
def get_app_help():
    container = request.json.get('container')
    if not container or not os.path.exists(container):
        return jsonify({'error': 'Valid container path required'}), 400
    
    try:
        # Run container help
        print(f"[GUI] Fetching help for {container}...", flush=True)
        cmd = ['apptainer', 'run', '--containall', container, '--help']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
        output = result.stdout + result.stderr
        
        if result.returncode != 0:
            print(f"[GUI] Apptainer help returned exit code {result.returncode}", flush=True)
        
        # IMPROVED: Clean up output (remove usage summary from the top to prevent it from confusing the parser)
        # Usually headers start with a capitalized word followed by colon, e.g., "Options:"
        parts = re.split(r'\n(?=[A-Z][a-z ]+:)', output)
        
        sections = []
        # Standard BIDS args to exclude (already handled by the GUI common section)
        exclude = {'--help', '--version', '--participant-label', '--space', '--bids-filter-file'}
        
        for part in parts:
            lines = part.strip().split('\n')
            if not lines: continue
            
            header = lines[0].strip().rstrip(':')
            
            # Skip usage/help summary sections
            if any(x in header.lower() for x in ['usage', 'synopsis', 'description']):
                continue
                
            content = '\n'.join(lines[1:])
            
            # Only process sections that look like they have definitions
            if '--' not in part: continue

            options = []
            # Split by flags that are at the beginning of a line (with 2+ spaces or start of block)
            arg_blocks = re.split(r'\n\s*(?=--)', "\n" + content)
            
            for block in arg_blocks:
                block = block.strip()
                if not block.startswith('--'): 
                    # Try to find a flag anyway if it's not at the start
                    flag_match = re.search(r'(--[a-zA-Z0-9-]+)', block)
                    if not flag_match: continue
                
                # Extract first flag found in the definition line
                flag_match = re.search(r'(--[a-zA-Z0-9-]+)', block)
                if not flag_match: continue
                flag = flag_match.group(1)
                if flag in exclude: continue

                # Detect if it's already in options (sometimes flags ARE repeated in help)
                if any(o['flag'] == flag for o in options): continue

                # Choices / Type detection
                choices = []
                choice_match = re.search(r'\{([^}]+)\}', block)
                if choice_match:
                    choices = [c.strip() for c in choice_match.group(1).split(',')]
                else:
                    choice_text_match = re.search(r'Possible choices:\s*([^\n]+)', block)
                    if choice_text_match:
                        # Split by comma or space and clean up
                        choices = [c.strip().strip(',') for c in re.split(r'[,\s]+', choice_text_match.group(1))]
                        choices = [c for c in choices if c and not c.startswith('-')]

                # Description: take everything AFTER the flag/metavar definition line
                block_lines = block.strip().split('\n')
                description = ""
                if len(block_lines) > 1:
                    # Often the first line contains the flag and maybe the metavar
                    # Everything from the second line onwards is description
                    # OR if there's only one line, the description might be after many spaces
                    description = " ".join([l.strip() for l in block_lines[1:]])
                elif '  ' in block:
                    # Handle single line case: --flag METAVAR  description
                    parts_of_line = re.split(r'\s{2,}', block.strip())
                    if len(parts_of_line) > 1:
                        description = " ".join(parts_of_line[1:])

                description = re.sub(r'\s+', ' ', description)
                # Cleanup common artifacts
                description = re.sub(r'\(default:.*?\)', '', description).strip()
                
                has_value = len(choices) > 0 or bool(re.search(flag + r'\s+[A-Z_]{2,}', block))
                is_multiple = bool(re.search(r'\(s\)|\[...\]|modes|choices', block, re.IGNORECASE))

                options.append({
                    'flag': flag,
                    'name': flag.lstrip('-').replace('-', ' ').title(),
                    'choices': choices,
                    'description': description,
                    'has_value': has_value,
                    'is_multiple': is_multiple
                })
            
            if options:
                sections.append({
                    'title': header,
                    'options': sorted(options, key=lambda x: x['name'])
                })

        # Identify app for Doc link
        app_name = "BIDS App"
        doc_url = "https://bids-apps.neuroimaging.io/"
        container_lower = os.path.basename(container).lower()
        if 'qsiprep' in container_lower:
            app_name = "QSIPrep"; doc_url = "https://qsiprep.readthedocs.io/"
        elif 'fmriprep' in container_lower:
            app_name = "fMRIPrep"; doc_url = "https://fmriprep.org/"
        elif 'mriqc' in container_lower:
            app_name = "MRIQC"; doc_url = "https://mriqc.readthedocs.io/"

        return jsonify({
            'sections': sections,
            'app_info': {'name': app_name, 'url': doc_url},
            'raw_help': output if not sections else None
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/list_dirs', methods=['POST'])
def list_dirs():
    path = request.json.get('path', '/')
    if not path:
        path = '/'
    
    try:
        p = Path(path)
        if not p.exists() or not p.is_dir():
            # Try to go to parent or root if path is invalid
            p = Path('/')
        
        items = []
        # Add parent directory entry
        if p.parent != p:
            items.append({'name': '..', 'path': str(p.parent), 'is_dir': True})
            
        for child in sorted(p.iterdir()):
            if child.is_dir() and not child.name.startswith('.'):
                items.append({
                    'name': child.name,
                    'path': str(child.absolute()),
                    'is_dir': True
                })
        return jsonify({'current_path': str(p.absolute()), 'items': items})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/')
def index():
    try:
        return render_template('index.html')
    except Exception as e:
        print(f"[DEBUG] Template error: {e}", flush=True)
        return str(e), 500

@app.route('/list_containers', methods=['POST'])
def list_containers():
    folder = request.json.get('folder')
    if not folder:
        return jsonify({'error': 'No folder provided'}), 400
    
    try:
        # Expand user path if needed
        folder_path = os.path.expanduser(folder)
        # Search for .sif and .simg files
        containers = glob.glob(os.path.join(folder_path, "*.sif")) + glob.glob(os.path.join(folder_path, "*.simg"))
        containers = [os.path.basename(c) for c in containers]
        return jsonify({'containers': sorted(containers)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/list_configs', methods=['GET'])
def list_configs():
    try:
        config_dir = BASE_DIR / "configs"
        configs = [f for f in os.listdir(config_dir) if f.endswith('.json')]
        return jsonify({'configs': sorted(configs)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/get_config', methods=['GET'])
def get_config():
    name = request.args.get('name')
    if not name: return jsonify({'error': 'No name provided'}), 400
    try:
        config_path = BASE_DIR / "configs" / name
        with open(config_path, 'r') as f:
            data = json.load(f)
        return jsonify({'config': data})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/save_config', methods=['POST'])
def save_config():
    data = request.json
    filename = data.get('filename', 'config.json')
    config_data = data.get('config')
    
    if not config_data:
        return jsonify({'error': 'No config data provided'}), 400
    
    try:
        # Ensure filename ends with .json
        if not filename.endswith('.json'):
            filename += '.json'
            
        config_path = BASE_DIR / "configs" / filename
        os.makedirs(config_path.parent, exist_ok=True)
        
        with open(config_path, 'w') as f:
            json.dump(config_data, f, indent=2)
            
        return jsonify({'message': f'Config saved successfully to {config_path}', 'path': str(config_path)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/run_app', methods=['POST'])
def run_app():
    global GUI_SESSION_STARTED
    config_path = request.json.get('config_path')
    runner_args = request.json.get('runner_args', [])
    if not config_path:
        return jsonify({'error': 'No config path provided'}), 400
    
    try:
        # 1. Path Validation
        with open(config_path, 'r') as f:
            cfg = json.load(f)
        
        common = cfg.get('common', {})
        paths_to_check = {
            'BIDS Folder': common.get('bids_folder'),
            'Temp Folder': common.get('tmp_folder'),
            'Container Image': common.get('container')
        }
        
        missing = []
        for name, path in paths_to_check.items():
            if path and not os.path.exists(path):
                missing.append(f"{name}: {path}")
        
        if missing:
            return jsonify({
                'error': 'Validation Failed',
                'details': 'The following paths do not exist:\n' + '\n'.join(missing)
            }), 400

        # 2. Launch run_bids_apps.py in background
        script_path = BASE_DIR / "run_bids_apps.py"
        
        # Build command
        cmd = [
            "python3", str(script_path),
            "-c", str(config_path),
        ]
        
        # Append runner arguments from UI
        if runner_args:
            cmd.extend(runner_args)
            
        # Ensure --nohup is present
        if "--nohup" not in cmd:
            cmd.append("--nohup")
        
        print(f"[GUI] Executing: {' '.join(cmd)}")
        subprocess.Popen(cmd, cwd=BASE_DIR)
        
        GUI_SESSION_STARTED = True
        
        return jsonify({'message': f'BIDS App Runner started in background. Command: {" ".join(cmd)}'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/kill_job', methods=['POST'])
def kill_job():
    # Attempt to kill run_bids_apps.py and associated apptainer processes
    try:
        # We look for the main runner script first
        cmd_find = ["pgrep", "-f", "run_bids_apps.py"]
        result = subprocess.run(cmd_find, capture_output=True, text=True)
        pids = result.stdout.strip().split('\n')
        
        if not pids or not pids[0]:
            return jsonify({'message': 'No active BIDS App Runner processes found.'}), 200

        # Kill the runner processes
        for pid in pids:
            if pid:
                subprocess.run(["kill", pid])
        
        # Also clean up any lingering container processes (like what kill_app.sh does)
        # We use a broad search for 'apptainer' or 'qsirecon' or 'fmriprep' 
        # but specifically looking for those that might have been spawned
        subprocess.run(["pkill", "-f", "apptainer"])
        subprocess.run(["pkill", "-f", "appinit"]) # Specific to some BIDS implementations
        
        return jsonify({'message': f'Termination signal sent to {len(pids)} runner process(es) and containers.'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    import socket
    port = 8080
    max_tries = 20
    
    # Simple loop to find an available port
    for _ in range(max_tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('0.0.0.0', port)) != 0:
                # Port is available
                break
            else:
                port += 1
    
    print("--------------------------------------------------------")
    print(f"  BIDS App Runner GUI starting on http://localhost:{port}")
    print(f"  Note: If local, open http://localhost:{port}")
    print(f"  Note: If remote, ensure port {port} is forwarded")
    print("--------------------------------------------------------")
    serve(app, host='0.0.0.0', port=port, threads=4)
