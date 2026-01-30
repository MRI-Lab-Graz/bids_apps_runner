#!/usr/bin/env python3
"""
BIDS App Output Validation Integration

Integration module for check_app_output.py functionality into BIDS App Runner.
Provides automatic validation and reprocessing capabilities.
"""

import json
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set
from datetime import datetime

# Import the existing checker classes
try:
    from check_app_output import BIDSOutputValidator, FMRIPrepChecker, QSIPrepChecker, FreeSurferChecker, QSIReconChecker
except ImportError:
    logging.warning("check_app_output.py not found - validation features disabled")
    BIDSOutputValidator = None


class BIDSAppIntegratedValidator:
    """Integrated validator for BIDS App Runner workflows."""
    
    def __init__(self, common_config: Dict, app_config: Dict):
        self.common_config = common_config
        self.app_config = app_config
        self.bids_dir = Path(common_config["bids_folder"])
        self.output_dir = Path(common_config["output_folder"])
        self.logger = logging.getLogger(__name__)
        
    def detect_pipeline_type(self) -> Optional[str]:
        """Auto-detect the pipeline type from container or configuration."""
        container_path = self.common_config.get("container", "")
        container_name = os.path.basename(container_path).lower()
        
        # Pipeline detection logic
        if "fmriprep" in container_name:
            return "fmriprep"
        elif "qsiprep" in container_name:
            return "qsiprep"
        elif "freesurfer" in container_name:
            return "freesurfer"
        elif any("qsirecon" in opt for opt in self.app_config.get("options", [])):
            return "qsirecon"
        
        # Check output directory structure
        if (self.output_dir / "fmriprep").exists():
            return "fmriprep"
        elif (self.output_dir / "qsiprep").exists():
            return "qsiprep"
        elif (self.output_dir / "freesurfer").exists():
            return "freesurfer"
        elif any((self.output_dir / "derivatives").glob("qsirecon*")):
            return "qsirecon"
            
        return None
    
    def validate_outputs(self, pipeline_type: Optional[str] = None) -> Dict:
        """Validate pipeline outputs and return detailed results."""
        if BIDSOutputValidator is None:
            self.logger.error("Output validation not available - check_app_output.py not found")
            return {"error": "Validation module not available"}
        
        if pipeline_type is None:
            pipeline_type = self.detect_pipeline_type()
            
        if pipeline_type is None:
            self.logger.warning("Could not auto-detect pipeline type")
            return {"error": "Unknown pipeline type"}
        
        self.logger.info(f"Validating {pipeline_type} outputs...")
        
        # Determine derivatives directory based on pipeline type
        if pipeline_type == "qsirecon":
            derivatives_dir = self.output_dir / "derivatives"
        else:
            derivatives_dir = self.output_dir
            
        try:
            validator = BIDSOutputValidator(
                self.bids_dir,
                derivatives_dir,
                verbose=False,
                quiet=True
            )
            
            results = validator.validate_all(pipeline_type)
            return results
            
        except Exception as e:
            self.logger.error(f"Validation failed: {e}")
            return {"error": str(e)}
    
    def get_missing_subjects(self, validation_results: Dict) -> List[str]:
        """Extract list of subjects that need reprocessing."""
        missing_subjects = set()
        
        if "pipelines" in validation_results:
            for pipeline_name, pipeline_data in validation_results["pipelines"].items():
                if "missing_items" in pipeline_data:
                    for item in pipeline_data["missing_items"]:
                        # Extract subject ID from missing item description
                        if "sub-" in item:
                            import re
                            match = re.search(r'sub-\d+', item)
                            if match:
                                missing_subjects.add(match.group())
        
        return sorted(list(missing_subjects))
    
    def get_missing_subject_sessions(self, validation_results: Dict) -> Dict[str, List[str]]:
        """Extract subjects and their missing sessions for session-aware reprocessing."""
        missing_subject_sessions = {}
        
        if "pipelines" in validation_results:
            for pipeline_name, pipeline_data in validation_results["pipelines"].items():
                if "missing_items" in pipeline_data:
                    for item in pipeline_data["missing_items"]:
                        # Extract subject and session from missing item
                        if "sub-" in item:
                            import re
                            # Look for pattern like "sub-123/ses-01" or "sub-123_ses-01"
                            subject_match = re.search(r'sub-\d+', item)
                            session_match = re.search(r'ses-[^/\s]+', item)
                            
                            if subject_match:
                                subject = subject_match.group()
                                session = session_match.group() if session_match else None
                                
                                if subject not in missing_subject_sessions:
                                    missing_subject_sessions[subject] = []
                                
                                if session and session not in missing_subject_sessions[subject]:
                                    missing_subject_sessions[subject].append(session)
                                elif session is None:
                                    # Single-session dataset
                                    missing_subject_sessions[subject] = [None]
        
        # Sort sessions for each subject
        for subject in missing_subject_sessions:
            if missing_subject_sessions[subject] and missing_subject_sessions[subject][0] is not None:
                missing_subject_sessions[subject].sort()
        
        return missing_subject_sessions
    
    def generate_reprocess_config(self, missing_subjects: List[str], output_file: Optional[str] = None) -> str:
        """Generate a new configuration file for reprocessing missing subjects."""
        if not missing_subjects:
            self.logger.info("No missing subjects found - no reprocessing needed")
            return ""
        
        # Create new config based on original
        reprocess_config = {
            "common": self.common_config.copy(),
            "app": self.app_config.copy()
        }
        
        # Override participant_labels with missing subjects
        reprocess_config["app"]["participant_labels"] = missing_subjects
        
        # Add metadata
        reprocess_config["_metadata"] = {
            "generated_by": "BIDS App Runner Integrated Validator",
            "timestamp": datetime.now().isoformat(),
            "original_subjects": len(missing_subjects),
            "reprocess_reason": "Missing or incomplete outputs detected"
        }
        
        # Generate output filename
        if output_file is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            pipeline_type = self.detect_pipeline_type() or "unknown"
            output_file = f"reprocess_{pipeline_type}_{timestamp}.json"
        
        # Write configuration
        try:
            with open(output_file, 'w') as f:
                json.dump(reprocess_config, f, indent=2)
            
            self.logger.info(f"Generated reprocessing config: {output_file}")
            self.logger.info(f"Missing subjects ({len(missing_subjects)}): {', '.join(missing_subjects)}")
            
            return output_file
            
        except Exception as e:
            self.logger.error(f"Failed to write reprocessing config: {e}")
            return ""
    
    def generate_session_aware_reprocess_config(self, missing_subject_sessions: Dict[str, List[str]], 
                                               output_file: Optional[str] = None) -> str:
        """Generate session-aware reprocessing config for apps that support --session-id."""
        if not missing_subject_sessions:
            self.logger.info("No missing sessions found - no reprocessing needed")
            return ""
        
        # Detect pipeline type to determine session support
        pipeline_type = self.detect_pipeline_type()
        supports_session_id = pipeline_type in ["qsiprep", "qsirecon"]  # Known apps with --session-id support
        
        if not supports_session_id:
            self.logger.info(f"Pipeline '{pipeline_type}' does not support --session-id, using subject-level reprocessing")
            # Fall back to subject-level reprocessing
            missing_subjects = list(missing_subject_sessions.keys())
            return self.generate_reprocess_config(missing_subjects, output_file)
        
        # Generate session-aware configs for apps that support it
        configs = []
        
        for subject, sessions in missing_subject_sessions.items():
            if sessions == [None]:
                # Single-session dataset
                config = {
                    "common": self.common_config.copy(),
                    "app": self.app_config.copy()
                }
                config["app"]["participant_labels"] = [subject]
                configs.append((config, f"{subject}_single-session"))
            else:
                # Multi-session dataset - create configs per session
                for session in sessions:
                    config = {
                        "common": self.common_config.copy(),
                        "app": self.app_config.copy()
                    }
                    config["app"]["participant_labels"] = [subject]
                    
                    # Add session-id option for supporting apps
                    if "options" not in config["app"]:
                        config["app"]["options"] = []
                    
                    # Add session-id parameter (remove ses- prefix as per QSIPrep docs)
                    session_id = session.replace("ses-", "") if session.startswith("ses-") else session
                    config["app"]["options"].extend(["--session-id", session_id])
                    
                    configs.append((config, f"{subject}_{session}"))
        
        # Save all configs
        saved_configs = []
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        for i, (config, identifier) in enumerate(configs):
            if output_file:
                base_name = output_file.replace(".json", f"_{i:03d}_{identifier}.json")
            else:
                base_name = f"reprocess_{pipeline_type}_{timestamp}_{i:03d}_{identifier}.json"
            
            # Add metadata
            config["_metadata"] = {
                "generated_by": "BIDS App Runner Integrated Validator (Session-Aware)",
                "timestamp": datetime.now().isoformat(),
                "session_config": True,
                "target_subject": identifier.split("_")[0],
                "target_session": identifier.split("_", 1)[1] if "_" in identifier else "single-session",
                "reprocess_reason": "Missing session outputs detected"
            }
            
            try:
                with open(base_name, 'w') as f:
                    json.dump(config, f, indent=2)
                saved_configs.append(base_name)
                self.logger.info(f"Generated session-aware config: {base_name}")
            except Exception as e:
                self.logger.error(f"Failed to write session config {base_name}: {e}")
        
        # Create master config list
        if saved_configs:
            master_config_file = output_file or f"reprocess_{pipeline_type}_{timestamp}_master.json"
            master_config = {
                "session_aware_reprocessing": True,
                "pipeline_type": pipeline_type,
                "generated_configs": saved_configs,
                "total_configs": len(saved_configs),
                "missing_sessions_summary": {
                    subject: sessions for subject, sessions in missing_subject_sessions.items()
                },
                "_metadata": {
                    "generated_by": "BIDS App Runner Integrated Validator (Session-Aware Master)",
                    "timestamp": datetime.now().isoformat(),
                    "reprocess_reason": "Session-aware reprocessing for missing outputs"
                }
            }
            
            try:
                with open(master_config_file, 'w') as f:
                    json.dump(master_config, f, indent=2)
                self.logger.info(f"Generated master session config: {master_config_file}")
                self.logger.info(f"Total session configs created: {len(saved_configs)}")
                return master_config_file
            except Exception as e:
                self.logger.error(f"Failed to write master config: {e}")
                return saved_configs[0] if saved_configs else ""
        
        return ""
    
    def create_validation_report(self, validation_results: Dict, output_file: Optional[str] = None) -> str:
        """Create a detailed validation report."""
        if output_file is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            pipeline_type = self.detect_pipeline_type() or "unknown"
            output_file = f"validation_report_{pipeline_type}_{timestamp}.json"
        
        # Enhanced report with metadata
        report = {
            "metadata": {
                "generated_by": "BIDS App Runner Integrated Validator",
                "timestamp": datetime.now().isoformat(),
                "bids_directory": str(self.bids_dir),
                "output_directory": str(self.output_dir),
                "pipeline_type": self.detect_pipeline_type()
            },
            "validation_results": validation_results,
            "missing_subjects": self.get_missing_subjects(validation_results)
        }
        
        try:
            with open(output_file, 'w') as f:
                json.dump(report, f, indent=2)
            
            self.logger.info(f"Validation report saved: {output_file}")
            return output_file
            
        except Exception as e:
            self.logger.error(f"Failed to write validation report: {e}")
            return ""


def validate_and_generate_reprocess_config(common_config: Dict, app_config: Dict, 
                                           output_dir: str = ".", 
                                           pipeline_type: Optional[str] = None) -> Dict:
    """
    Convenience function for validation and reprocessing config generation.
    
    Returns:
        Dict with keys: 'validation_report', 'reprocess_config', 'missing_subjects'
    """
    validator = BIDSAppIntegratedValidator(common_config, app_config)
    
    # Validate outputs
    validation_results = validator.validate_outputs(pipeline_type)
    
    if "error" in validation_results:
        return {"error": validation_results["error"]}
    
    # Get missing subjects
    missing_subjects = validator.get_missing_subjects(validation_results)
    
    # Generate reports
    report_file = validator.create_validation_report(
        validation_results, 
        os.path.join(output_dir, f"validation_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    )
    
    reprocess_config_file = ""
    if missing_subjects:
        reprocess_config_file = validator.generate_reprocess_config(
            missing_subjects,
            os.path.join(output_dir, f"reprocess_config_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        )
    
    return {
        "validation_report": report_file,
        "reprocess_config": reprocess_config_file,
        "missing_subjects": missing_subjects,
        "total_missing": len(missing_subjects)
    }


def print_validation_summary(results: Dict):
    """Print a user-friendly validation summary."""
    if "error" in results:
        print(f"❌ Validation Error: {results['error']}")
        return
    
    missing_count = results.get("total_missing", 0)
    
    if missing_count == 0:
        print("✅ All subjects processed successfully!")
        print(f"📊 Validation report: {results.get('validation_report', 'N/A')}")
    else:
        print(f"⚠️  Found {missing_count} subjects requiring reprocessing")
        print(f"📋 Missing subjects: {', '.join(results.get('missing_subjects', []))}")
        print(f"📊 Validation report: {results.get('validation_report', 'N/A')}")
        print(f"🔄 Reprocess config: {results.get('reprocess_config', 'N/A')}")
        print("\n💡 To reprocess missing subjects:")
        print(f"   python run_bids_apps.py -x {results.get('reprocess_config', 'reprocess_config.json')}")
