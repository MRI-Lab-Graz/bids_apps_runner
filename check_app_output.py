#!/usr/bin/env python3
"""
BIDS App Output Checker

This script compares BIDS source data to pipeline outputs in a derivatives folder.
It automatically detects available pipelines and validates their outputs.

Usage:
    python check_bids_outputs.py /path/to/bids/source /path/to/derivatives
    python check_bids_outputs.py /path/to/bids/source /path/to/derivatives -p fmriprep
    python check_bids_outputs.py /path/to/bids/source /path/to/derivatives --verbose
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple, Optional
from collections import defaultdict
import re


class BIDSChecker:
    """Base class for BIDS pipeline output validation."""
    
    def __init__(self, bids_dir: Path, derivatives_dir: Path):
        self.bids_dir = bids_dir
        self.derivatives_dir = derivatives_dir
        self.missing_items = []
        self.logger = logging.getLogger(__name__)
        self.stats = {}  # For pipeline-specific statistics
    
    def get_subjects(self) -> List[Path]:
        """Get all subject directories from BIDS source."""
        return sorted([d for d in self.bids_dir.glob("sub-*") if d.is_dir()])
    
    def get_sessions(self, subject_dir: Path) -> List[Path]:
        """Get sessions for a subject, or return subject dir if no sessions."""
        session_dirs = list(subject_dir.glob("ses-*"))
        return sorted(session_dirs) if session_dirs else [subject_dir]
    
    def add_missing_item(self, item: str, severity: str = "ERROR"):
        """Add a missing item to the list with severity level."""
        formatted_item = f"[{severity}] {item}"
        self.missing_items.append(formatted_item)
        
        if severity == "ERROR":
            self.logger.error(f"MISSING: {item}")
        elif severity == "WARNING":
            self.logger.warning(f"WARNING: {item}")
        else:
            self.logger.info(f"INFO: {item}")
    
    def add_found_item(self, item: str):
        """Log found items for debugging."""
        self.logger.debug(f"FOUND: {item}")
    
    def check_pipeline(self, pipeline_dir: Path) -> bool:
        """Check a specific pipeline. To be implemented by subclasses."""
        raise NotImplementedError


class FMRIPrepChecker(BIDSChecker):
    """Checker for fMRIPrep pipeline outputs."""
    
    def check_pipeline(self, pipeline_dir: Path) -> bool:
        """Check fMRIPrep outputs."""
        self.logger.info("Checking fMRIPrep pipeline...")
        
        has_surface_output = {}
        all_subjects = []
        surface_found_global = False
        
        for subj_dir in self.get_subjects():
            subj = subj_dir.name
            self.logger.info(f"Checking subject: {subj}")
            all_subjects.append(subj)
            surface_found_for_subject = False
            
            for sess_dir in self.get_sessions(subj_dir):
                func_dir = sess_dir / "func"
                if not func_dir.exists():
                    self.logger.warning(f"No func directory in {sess_dir.name}")
                    continue
                
                # Check volumetric preprocessed data
                for bids_func in func_dir.glob("*_bold.nii*"):
                    prefix = bids_func.stem.split("_bold")[0]
                    if bids_func.suffix == ".gz":
                        prefix = prefix.split(".nii")[0]
                    
                    # Build expected fMRIPrep path
                    fmriprep_subj_dir = pipeline_dir / subj
                    sess_basename = sess_dir.name
                    if sess_basename.startswith("ses-"):
                        fmriprep_subj_dir = fmriprep_subj_dir / sess_basename
                    
                    fmriprep_func_dir = fmriprep_subj_dir / "func"
                    
                    # Look for preprocessed files
                    pattern = f"{prefix}*desc-preproc_bold.nii*"
                    
                    if not fmriprep_func_dir.exists():
                        self.add_missing_item(
                            f"fMRIPrep func directory missing:\n"
                            f"    Expected: {fmriprep_func_dir}\n"
                            f"    Subject:  {subj}\n"
                            f"    Session:  {sess_basename if sess_basename.startswith('ses-') else 'single-session'}"
                        )
                        continue
                    
                    matches = list(fmriprep_func_dir.glob(pattern))
                    
                    if not matches:
                        # List what files are actually in the directory
                        actual_files = list(fmriprep_func_dir.glob("*.nii*"))
                        self.add_missing_item(
                            f"fMRIPrep preprocessed BOLD missing:\n"
                            f"    Input:     {bids_func}\n"
                            f"    Expected:  {fmriprep_func_dir}/{pattern}\n"
                            f"    Found:     {len(actual_files)} files in directory\n"
                            f"    Examples:  {[f.name for f in actual_files[:3]]}"
                        )
                    else:
                        self.add_found_item(f"fMRIPrep preprocessed file for: {bids_func}")
                        # Check if we have multiple matches (might indicate issue)
                        if len(matches) > 1:
                            self.add_missing_item(
                                f"Multiple fMRIPrep matches for {bids_func.name}:\n"
                                f"    Found: {[m.name for m in matches]}", 
                                "WARNING"
                            )
                
                # Check surface-based outputs
                if fmriprep_func_dir.exists():
                    surface_files = list(fmriprep_func_dir.glob("*_hemi-*_bold.func.gii"))
                    if surface_files:
                        surface_found_for_subject = True
                        surface_found_global = True
                        
                        # Check hemisphere pairs
                        for surface_file in surface_files:
                            if "hemi-L" in surface_file.name:
                                expected_r = Path(str(surface_file).replace("hemi-L", "hemi-R"))
                                if not expected_r.exists():
                                    self.add_missing_item(
                                        f"Missing hemi-R pair for: {surface_file}"
                                    )
                            elif "hemi-R" in surface_file.name:
                                expected_l = Path(str(surface_file).replace("hemi-R", "hemi-L"))
                                if not expected_l.exists():
                                    self.add_missing_item(
                                        f"Missing hemi-L pair for: {surface_file}"
                                    )
            
            has_surface_output[subj] = surface_found_for_subject
        
        # Global surface output consistency check
        if surface_found_global:
            for subj in all_subjects:
                if not has_surface_output[subj]:
                    self.add_missing_item(
                        f"Subject {subj} missing surface outputs (present in others)"
                    )
        
        return len(self.missing_items) == 0


class FreeSurferChecker(BIDSChecker):
    """Checker for FreeSurfer pipeline outputs."""
    
    def check_pipeline(self, pipeline_dir: Path) -> bool:
        """Check FreeSurfer outputs."""
        self.logger.info("Checking FreeSurfer pipeline...")
        
        subject_tracking = defaultdict(lambda: {
            'cross_hippoSf': False, 'cross_amyg': False,
            'long_hippoSf': False, 'long_amyg': False,
            'is_multisession': False,
            'has_longitudinal_processing': False
        })
        
        for subj_dir in self.get_subjects():
            subj = subj_dir.name
            self.logger.info(f"Checking FreeSurfer outputs for: {subj}")
            
            # Count sessions with anatomical data
            anat_sessions = 0
            for sess_dir in self.get_sessions(subj_dir):
                anat_dir = sess_dir / "anat"
                if anat_dir.exists() and list(anat_dir.glob("*_T1w.nii*")):
                    anat_sessions += 1
            
            if anat_sessions == 0:
                self.logger.info(f"No T1w files for {subj}, skipping FreeSurfer check")
                continue
            
            # Mark multi-session subjects
            subject_tracking[subj]['is_multisession'] = anat_sessions > 1
            
            # Find FreeSurfer directories first to determine processing type
            # Look for both subject-level folders and session-specific folders
            fs_dirs = [d for d in pipeline_dir.glob(f"{subj}*") if d.is_dir() and not d.name.startswith(('fsaverage', 'local'))]
            
            self.logger.debug(f"Subject {subj}: Found {len(fs_dirs)} FreeSurfer directories: {[d.name for d in fs_dirs]}")
            
            # If no FreeSurfer folders found, report missing processing
            if not fs_dirs:
                self.add_missing_item(
                    f"FreeSurfer processing missing for subject:\n"
                    f"    Subject:      {subj}\n"
                    f"    T1w sessions: {anat_sessions}\n"
                    f"    Expected:     At least one FreeSurfer folder\n"
                    f"    Found:        No folders matching '{subj}*'\n"
                    f"    Location:     {pipeline_dir}"
                )
                continue
            
            # Check if longitudinal processing was performed by looking for .long folders
            has_longitudinal = any(".long" in fs_dir.name for fs_dir in fs_dirs)
            subject_tracking[subj]['has_longitudinal_processing'] = has_longitudinal
            
            # Determine expected folder count based on actual processing type
            if anat_sessions == 1:
                expected_count = 1
                processing_type = "single-session"
            else:
                if has_longitudinal:
                    # Longitudinal processing: N cross + 1 base + N long
                    expected_count = 2 * anat_sessions + 1
                    processing_type = "longitudinal"
                else:
                    # Cross-sectional processing: N cross-sectional folders
                    expected_count = anat_sessions
                    processing_type = "cross-sectional"
            
            if len(fs_dirs) != expected_count:
                actual_dirs = [d.name for d in fs_dirs]
                self.add_missing_item(
                    f"FreeSurfer folder count mismatch:\n"
                    f"    Subject:     {subj}\n"
                    f"    Sessions:    {anat_sessions} T1w sessions\n"
                    f"    Processing:  {processing_type}\n"
                    f"    Expected:    {expected_count} folders\n"
                    f"    Found:       {len(fs_dirs)} folders\n"
                    f"    Actual:      {actual_dirs}\n"
                    f"    Location:    {pipeline_dir}"
                )
            
            # Check each FreeSurfer directory
            for fs_dir in fs_dirs:
                recon_done = fs_dir / "scripts" / "recon-all.done"
                if not recon_done.exists():
                    scripts_dir = fs_dir / "scripts"
                    if scripts_dir.exists():
                        script_files = list(scripts_dir.glob("*"))
                        self.add_missing_item(
                            f"FreeSurfer recon-all.done missing:\n"
                            f"    Expected:   {recon_done}\n"
                            f"    Directory:  {fs_dir.name}\n"
                            f"    Scripts:    {len(script_files)} files found\n"
                            f"    Status:     Processing incomplete or failed"
                        )
                    else:
                        self.add_missing_item(
                            f"FreeSurfer scripts directory missing:\n"
                            f"    Expected: {scripts_dir}\n"
                            f"    Folder:   {fs_dir.name}"
                        )
                else:
                    self.add_found_item(f"FreeSurfer recon-all.done in: {fs_dir.name}")
                
                # Check hippocampal/amygdala segmentation files
                self._check_segmentation_files(fs_dir, subj, subject_tracking)
        
        # Check multi-session consistency only for subjects with longitudinal processing
        for subj, tracking in subject_tracking.items():
            if tracking['is_multisession'] and tracking['has_longitudinal_processing']:
                if not tracking['long_hippoSf']:
                    self.add_missing_item(
                        f"Subject {subj} missing longitudinal hippocampal subfield volumes\n"
                        f"    Note: Subject has longitudinal processing but missing hippocampal files"
                    )
                if not tracking['long_amyg']:
                    self.add_missing_item(
                        f"Subject {subj} missing longitudinal hippocampal/amygdala files\n"
                        f"    Note: Subject has longitudinal processing but missing amygdala files"
                    )
        
        return len(self.missing_items) == 0
    
    def _check_segmentation_files(self, fs_dir: Path, subj: str, tracking: dict):
        """Check hippocampal and amygdala segmentation files."""
        mri_dir = fs_dir / "mri"
        if not mri_dir.exists():
            self.logger.warning(f"No mri directory in {fs_dir}")
            return
        
        is_longitudinal = ".long" in fs_dir.name
        
        if is_longitudinal:
            # Check longitudinal files
            hippo_files = list(mri_dir.glob("*hippoSfVolumes*.long*.txt"))
            amyg_files = list(mri_dir.glob("*hippoAmygLabels*.long*.txt")) + \
                        list(mri_dir.glob("*amygNucVolumes*.long*.txt"))
            
            if hippo_files:
                tracking[subj]['long_hippoSf'] = True
            if amyg_files:
                tracking[subj]['long_amyg'] = True
        else:
            # Check cross-sectional files (shouldn't have .long in name)
            long_hippo = list(mri_dir.glob("*hippoSfVolumes*.long*.txt"))
            long_amyg = list(mri_dir.glob("*hippoAmygLabels*.long*.txt")) + \
                       list(mri_dir.glob("*amygNucVolumes*.long*.txt"))
            
            if long_hippo:
                self.add_missing_item(
                    f"Found longitudinal hippocampal file in cross-sectional folder: {fs_dir}"
                )
            if long_amyg:
                self.add_missing_item(
                    f"Found longitudinal amygdala file in cross-sectional folder: {fs_dir}"
                )
            
            # Check for cross-sectional files
            cross_hippo = [f for f in mri_dir.glob("*hippoSfVolumes*.txt") 
                          if ".long" not in f.name]
            cross_amyg = [f for f in (list(mri_dir.glob("*hippoAmygLabels*.txt")) + 
                                     list(mri_dir.glob("*amygNucVolumes*.txt"))) 
                         if ".long" not in f.name]
            
            if cross_hippo:
                tracking[subj]['cross_hippoSf'] = True
            if cross_amyg:
                tracking[subj]['cross_amyg'] = True


class QSIPrepChecker(BIDSChecker):
    """Checker for QSIPrep pipeline outputs."""
    
    def check_pipeline(self, pipeline_dir: Path) -> bool:
        """Check QSIPrep outputs."""
        self.logger.info("Checking QSIPrep pipeline...")
        
        # Initialize statistics tracking
        self.stats = {
            'total_subjects': 0,
            'subjects_with_dwi': 0,
            'subjects_with_missing_sessions': [],
            'subjects_with_no_dwi': [],
            'missing_sessions_by_subject': {},
            'session_statistics': {}
        }
        
        for subj_dir in self.get_subjects():
            subj = subj_dir.name
            self.logger.info(f"Checking QSIPrep outputs for: {subj}")
            
            # Update statistics
            self.stats['total_subjects'] += 1
            
            # Check subject folder exists
            qsiprep_subj_dir = pipeline_dir / subj
            if not qsiprep_subj_dir.exists():
                self.add_missing_item(
                    f"QSIPrep subject directory missing:\n"
                    f"    Expected: {qsiprep_subj_dir}\n"
                    f"    Subject:  {subj}"
                )
                self.stats['subjects_with_no_dwi'].append(subj)
                continue
            
            # Check HTML report
            html_report = pipeline_dir / f"{subj}.html"
            if not html_report.exists():
                # Look for other HTML files in the directory
                html_files = list(pipeline_dir.glob("*.html"))
                self.add_missing_item(
                    f"QSIPrep HTML report missing:\n"
                    f"    Expected:    {html_report}\n"
                    f"    Subject:     {subj}\n"
                    f"    Found HTML:  {len(html_files)} files\n"
                    f"    Examples:    {[f.name for f in html_files[:3]]}"
                )
            else:
                self.add_found_item(f"QSIPrep HTML report for: {subj}")
            
            # First, check if this subject has any DWI data at all
            subject_has_dwi = False
            session_dwi_status = {}
            missing_sessions = []
            
            for sess_dir in self.get_sessions(subj_dir):
                dwi_dir = sess_dir / "dwi"
                session_dwi_status[sess_dir.name] = dwi_dir.exists()
                if dwi_dir.exists() and list(dwi_dir.glob("*_dwi.nii*")):
                    subject_has_dwi = True
                    # Track session statistics
                    if sess_dir.name not in self.stats['session_statistics']:
                        self.stats['session_statistics'][sess_dir.name] = {
                            'total_subjects': 0, 'missing_subjects': []
                        }
                    self.stats['session_statistics'][sess_dir.name]['total_subjects'] += 1
                else:
                    missing_sessions.append(sess_dir.name)
            
            # Update subject-level statistics
            if subject_has_dwi:
                self.stats['subjects_with_dwi'] += 1
                if missing_sessions:
                    self.stats['subjects_with_missing_sessions'].append(subj)
                    self.stats['missing_sessions_by_subject'][subj] = missing_sessions
                    # Update session-specific missing counts
                    for missing_sess in missing_sessions:
                        if missing_sess not in self.stats['session_statistics']:
                            self.stats['session_statistics'][missing_sess] = {
                                'total_subjects': 0, 'missing_subjects': []
                            }
                        self.stats['session_statistics'][missing_sess]['missing_subjects'].append(subj)
            else:
                self.stats['subjects_with_no_dwi'].append(subj)
            
            # Check DWI outputs for each session
            for sess_dir in self.get_sessions(subj_dir):
                dwi_dir = sess_dir / "dwi"
                if not dwi_dir.exists():
                    if subject_has_dwi:
                        # If subject has DWI data in other sessions, missing DWI in this session is an error
                        self.add_missing_item(
                            f"DWI directory missing for session with DWI data in other sessions:\n"
                            f"    Subject:  {subj}\n"
                            f"    Session:  {sess_dir.name}\n"
                            f"    Expected: {dwi_dir}\n"
                            f"    Note:     Other sessions have DWI data, QSIPrep output expected"
                        )
                    else:
                        # If no sessions have DWI data, just log as info
                        self.logger.info(f"No DWI directory in {sess_dir.name} (subject has no DWI data)")
                    continue
                
                sess = sess_dir.name
                for bids_dwi in dwi_dir.glob("*_dwi.nii*"):
                    # Extract subject and session from filename for pattern matching
                    base_name = bids_dwi.name
                    
                    # Simple approach: extract subject and session directly
                    # Pattern: sub-XXXXX_ses-Y_acq-multishell_dwi.nii.gz -> sub-XXXXX_ses-Y
                    if '_ses-' in base_name:
                        # Multi-session: sub-XXXXX_ses-Y_...
                        parts = base_name.split('_')
                        subj_part = parts[0]  # sub-XXXXX
                        sess_part = None
                        for part in parts[1:]:
                            if part.startswith('ses-'):
                                sess_part = part
                                break
                        base_prefix = f"{subj_part}_{sess_part}" if sess_part else subj_part
                    else:
                        # Single session: sub-XXXXX_...
                        parts = base_name.split('_')
                        base_prefix = parts[0]  # sub-XXXXX
                    
                    # Check for preprocessed DWI file with flexible pattern
                    qsiprep_dwi_dir = qsiprep_subj_dir / sess / "dwi"
                    # Use a flexible pattern that accounts for QSIPrep's additional parameters like space-ACPC
                    pattern = f"{base_prefix}_*desc-preproc_dwi.nii*"
                    
                    if not qsiprep_dwi_dir.exists():
                        self.add_missing_item(
                            f"QSIPrep DWI directory missing: {qsiprep_dwi_dir}"
                        )
                        continue
                    
                    matches = list(qsiprep_dwi_dir.glob(pattern))
                    
                    if not matches:
                        # Try alternative patterns to catch different QSIPrep output formats
                        alternative_patterns = [
                            f"{subj}_*_desc-preproc_dwi.nii*"  # Most flexible: just match subject
                        ]
                        
                        for alt_pattern in alternative_patterns:
                            alt_matches = list(qsiprep_dwi_dir.glob(alt_pattern))
                            if alt_matches:
                                matches = alt_matches
                                self.logger.debug(f"Found matches with alternative pattern {alt_pattern}: {[m.name for m in matches]}")
                                break
                    
                    if not matches:
                        # List what files are actually in the directory
                        actual_files = list(qsiprep_dwi_dir.glob("*.nii*"))
                        desc_preproc_files = list(qsiprep_dwi_dir.glob("*desc-preproc_dwi.nii*"))
                        
                        self.add_missing_item(
                            f"QSIPrep preprocessed DWI missing:\n"
                            f"    Input:              {bids_dwi}\n"
                            f"    Expected pattern:   {qsiprep_dwi_dir}/{pattern}\n"
                            f"    Base prefix:        {base_prefix}\n"
                            f"    Found .nii files:   {len(actual_files)}\n"
                            f"    Found desc-preproc: {len(desc_preproc_files)}\n"
                            f"    Examples:           {[f.name for f in (desc_preproc_files or actual_files)[:3]]}"
                        )
                    elif len(matches) > 1:
                        self.add_missing_item(
                            f"Multiple QSIPrep DWI matches for {bids_dwi.name}:\n"
                            f"    Found: {[m.name for m in matches]}", 
                            "WARNING"
                        )
                    else:
                        # Check if we actually have the .nii.gz file (not just .json or other files)
                        nii_gz_files = [m for m in matches if m.suffix == '.gz' and '.nii' in m.name]
                        if not nii_gz_files:
                            # We have matches but no actual .nii.gz file
                            json_files = [m for m in matches if m.suffix == '.json']
                            other_files = [m for m in matches if m not in json_files]
                            self.add_missing_item(
                                f"QSIPrep preprocessed DWI .nii.gz file missing:\n"
                                f"    Input:       {bids_dwi}\n"
                                f"    Directory:   {qsiprep_dwi_dir}\n"
                                f"    Expected:    {base_prefix}*_desc-preproc_dwi.nii.gz\n"
                                f"    Found JSON:  {[f.name for f in json_files]}\n"
                                f"    Found other: {[f.name for f in other_files]}\n"
                                f"    Status:      Processing incomplete - sidecar files present but main data missing"
                            )
                        else:
                            self.add_found_item(f"QSIPrep DWI file for: {bids_dwi}")
                            # Also check for essential sidecar files using the actual found file as reference
                            main_file = nii_gz_files[0]  # Use the first found .nii.gz file
                            # Extract the actual prefix from the found file
                            actual_filename = main_file.stem
                            if actual_filename.endswith('.nii'):
                                actual_filename = actual_filename[:-4]  # Remove .nii
                            
                            # Replace _desc-preproc_dwi with empty to get base
                            actual_prefix = actual_filename.replace('_desc-preproc_dwi', '')
                            
                            expected_sidecars = ['.bval', '.bvec', '.json']
                            missing_sidecars = []
                            for sidecar_ext in expected_sidecars:
                                sidecar_filename = f"{actual_prefix}_desc-preproc_dwi{sidecar_ext}"
                                sidecar_path = qsiprep_dwi_dir / sidecar_filename
                                if not sidecar_path.exists():
                                    missing_sidecars.append(sidecar_ext)
                            
                            if missing_sidecars:
                                self.add_missing_item(
                                    f"QSIPrep essential sidecar files missing:\n"
                                    f"    Input:            {bids_dwi}\n"
                                    f"    Main file:        {main_file.name}\n"
                                    f"    Missing sidecars: {missing_sidecars}\n"
                                    f"    Expected prefix:  {actual_prefix}_desc-preproc_dwi", 
                                    "WARNING"
                                )
        
        return len(self.missing_items) == 0


class QSIReconChecker(BIDSChecker):
    """Checker for QSIRecon pipeline outputs."""
    
    def check_pipeline(self, pipeline_dir: Path) -> bool:
        """Check QSIRecon outputs."""
        self.logger.info("Checking QSIRecon pipeline...")
        
        # Initialize statistics tracking
        self.stats = {
            'total_subjects': 0,
            'subjects_with_dwi': 0,
            'recon_pipelines_found': [],
            'subjects_by_pipeline': {},
            'missing_subjects_by_pipeline': {}
        }
        
        # Find all qsirecon pipelines in derivatives
        qsirecon_pipelines = list(pipeline_dir.glob("qsirecon*"))
        
        if not qsirecon_pipelines:
            self.logger.warning("No QSIRecon pipelines found")
            return True
        
        for qsi_pipeline in qsirecon_pipelines:
            if not qsi_pipeline.is_dir():
                continue
                
            pipeline_name = qsi_pipeline.name
            self.logger.info(f"Checking QSIRecon pipeline: {pipeline_name}")
            
            # Check for derivatives subdirectory structure
            derivatives_dir = qsi_pipeline / "derivatives"
            if derivatives_dir.exists():
                self.logger.info(f"Found derivatives subdirectory in {pipeline_name}")
                self._check_derivatives_structure(derivatives_dir, pipeline_name)
            else:
                # Fallback to old structure check
                self.logger.info(f"No derivatives subdirectory found, checking direct structure")
                self._check_direct_structure(qsi_pipeline, pipeline_name)
        
        return len(self.missing_items) == 0
    
    def _check_derivatives_structure(self, derivatives_dir: Path, parent_pipeline: str):
        """Check QSIRecon outputs in derivatives subdirectory structure."""
        # Find all recon pipelines in derivatives (e.g., qsirecon-NODDI, qsirecon-DSIStudio)
        recon_pipelines = [d for d in derivatives_dir.glob("qsirecon-*") if d.is_dir()]
        
        if not recon_pipelines:
            self.add_missing_item(
                f"No QSIRecon reconstruction pipelines found in derivatives:\n"
                f"    Expected: {derivatives_dir}/qsirecon-*\n"
                f"    Parent:   {parent_pipeline}"
            )
            return
        
        # Get all subjects with DWI data from BIDS source
        subjects_with_dwi = []
        for subj_dir in self.get_subjects():
            subj = subj_dir.name
            has_dwi = False
            for sess_dir in self.get_sessions(subj_dir):
                dwi_dir = sess_dir / "dwi"
                if dwi_dir.exists() and list(dwi_dir.glob("*_dwi.nii*")):
                    has_dwi = True
                    break
            if has_dwi:
                subjects_with_dwi.append(subj)
        
        self.stats['total_subjects'] = len(self.get_subjects())
        self.stats['subjects_with_dwi'] = len(subjects_with_dwi)
        
        for recon_pipeline in recon_pipelines:
            recon_name = recon_pipeline.name
            self.logger.info(f"Checking reconstruction pipeline: {recon_name}")
            self.stats['recon_pipelines_found'].append(recon_name)
            self.stats['subjects_by_pipeline'][recon_name] = []
            self.stats['missing_subjects_by_pipeline'][recon_name] = []
            
            # Check which subjects have outputs in this recon pipeline
            found_subjects = []
            for subj_dir in recon_pipeline.glob("sub-*"):
                if subj_dir.is_dir():
                    found_subjects.append(subj_dir.name)
                    self.stats['subjects_by_pipeline'][recon_name].append(subj_dir.name)
            
            # Check for missing subjects (those with DWI data but no recon output)
            missing_subjects = [subj for subj in subjects_with_dwi if subj not in found_subjects]
            
            if missing_subjects:
                self.stats['missing_subjects_by_pipeline'][recon_name] = missing_subjects
                for missing_subj in missing_subjects:
                    self.add_missing_item(
                        f"QSIRecon subject missing from reconstruction pipeline:\n"
                        f"    Pipeline:  {recon_name}\n"
                        f"    Subject:   {missing_subj}\n"
                        f"    Expected:  {recon_pipeline}/{missing_subj}\n"
                        f"    Note:      Subject has DWI data in BIDS source"
                    )
            
            # For found subjects, check if they have proper session structure and files
            for subj in found_subjects:
                subj_dir = recon_pipeline / subj
                self._check_subject_recon_outputs(subj_dir, subj, recon_name)
        
        # Check for HTML reports
        self._check_html_reports(derivatives_dir, subjects_with_dwi)
    
    def _check_subject_recon_outputs(self, subj_dir: Path, subj: str, recon_name: str):
        """Check reconstruction outputs for a specific subject."""
        # Get sessions from BIDS source for this subject
        bids_subj_dir = None
        for bids_dir in self.get_subjects():
            if bids_dir.name == subj:
                bids_subj_dir = bids_dir
                break
        
        if not bids_subj_dir:
            return
        
        sessions_with_dwi = []
        for sess_dir in self.get_sessions(bids_subj_dir):
            dwi_dir = sess_dir / "dwi"
            if dwi_dir.exists() and list(dwi_dir.glob("*_dwi.nii*")):
                sessions_with_dwi.append(sess_dir.name)
        
        # Check if subject has session subdirectories or is single-session
        session_dirs = list(subj_dir.glob("ses-*"))
        
        if session_dirs:
            # Multi-session structure
            found_sessions = [d.name for d in session_dirs]
            missing_sessions = [sess for sess in sessions_with_dwi if sess not in found_sessions]
            
            if missing_sessions:
                for missing_sess in missing_sessions:
                    self.add_missing_item(
                        f"QSIRecon session missing:\n"
                        f"    Pipeline:  {recon_name}\n"
                        f"    Subject:   {subj}\n"
                        f"    Session:   {missing_sess}\n"
                        f"    Expected:  {subj_dir}/{missing_sess}"
                    )
            
            # Check each session for DWI outputs
            for sess_dir in session_dirs:
                dwi_dir = sess_dir / "dwi"
                if dwi_dir.exists():
                    nii_files = list(dwi_dir.glob("*.nii.gz"))
                    if not nii_files:
                        self.add_missing_item(
                            f"QSIRecon DWI files missing:\n"
                            f"    Pipeline:   {recon_name}\n"
                            f"    Subject:    {subj}\n"
                            f"    Session:    {sess_dir.name}\n"
                            f"    Directory:  {dwi_dir}\n"
                            f"    Expected:   *.nii.gz files"
                        )
                    else:
                        self.add_found_item(
                            f"QSIRecon files for {subj}/{sess_dir.name} in {recon_name}: {len(nii_files)} .nii.gz files"
                        )
                else:
                    if sess_dir.name in sessions_with_dwi:
                        self.add_missing_item(
                            f"QSIRecon DWI directory missing:\n"
                            f"    Pipeline:  {recon_name}\n"
                            f"    Subject:   {subj}\n"
                            f"    Session:   {sess_dir.name}\n"
                            f"    Expected:  {dwi_dir}"
                        )
        else:
            # Single-session structure - check for DWI files directly
            dwi_dir = subj_dir / "dwi"
            if dwi_dir.exists():
                nii_files = list(dwi_dir.glob("*.nii.gz"))
                if not nii_files:
                    self.add_missing_item(
                        f"QSIRecon DWI files missing:\n"
                        f"    Pipeline:   {recon_name}\n"
                        f"    Subject:    {subj}\n"
                        f"    Directory:  {dwi_dir}\n"
                        f"    Expected:   *.nii.gz files"
                    )
                else:
                    self.add_found_item(
                        f"QSIRecon files for {subj} in {recon_name}: {len(nii_files)} .nii.gz files"
                    )
            else:
                if sessions_with_dwi:  # Only report if subject actually has DWI data
                    self.add_missing_item(
                        f"QSIRecon DWI directory missing:\n"
                        f"    Pipeline:  {recon_name}\n"
                        f"    Subject:   {subj}\n"
                        f"    Expected:  {dwi_dir}"
                    )
    
    def _check_html_reports(self, derivatives_dir: Path, subjects_with_dwi: List[str]):
        """Check for HTML reports in reconstruction pipelines."""
        for recon_pipeline in derivatives_dir.glob("qsirecon-*"):
            if not recon_pipeline.is_dir():
                continue
            
            recon_name = recon_pipeline.name
            html_files = list(recon_pipeline.glob("*.html"))
            
            if not html_files:
                self.add_missing_item(
                    f"No HTML reports found in reconstruction pipeline:\n"
                    f"    Pipeline:  {recon_name}\n"
                    f"    Expected:  {recon_pipeline}/*.html"
                )
                continue
            
            # Check if we have reports for subjects with DWI data
            found_reports = [f.stem.split('_ses-')[0] if '_ses-' in f.stem else f.stem 
                           for f in html_files]
            missing_reports = [subj for subj in subjects_with_dwi 
                             if not any(subj in report for report in found_reports)]
            
            if missing_reports:
                for subj in missing_reports:
                    self.add_missing_item(
                        f"QSIRecon HTML report missing:\n"
                        f"    Pipeline:  {recon_name}\n"
                        f"    Subject:   {subj}\n"
                        f"    Expected:  {recon_pipeline}/{subj}*.html"
                    )
    
    def _check_direct_structure(self, qsi_pipeline: Path, pipeline_name: str):
        """Check QSIRecon outputs in direct structure (fallback for older versions)."""
        for subj_dir in self.get_subjects():
            subj = subj_dir.name
            
            for sess_dir in self.get_sessions(subj_dir):
                sess = sess_dir.name
                qsirecon_dwi_dir = qsi_pipeline / subj / sess / "dwi"
                
                if not qsirecon_dwi_dir.exists():
                    self.add_missing_item(
                        f"QSIRecon directory missing:\n"
                        f"    Pipeline:  {pipeline_name}\n"
                        f"    Expected:  {qsirecon_dwi_dir}\n"
                        f"    Subject:   {subj}\n"
                        f"    Session:   {sess}"
                    )
                else:
                    # Check for at least one .nii.gz file
                    nii_files = list(qsirecon_dwi_dir.glob("*.nii.gz"))
                    if not nii_files:
                        self.add_missing_item(
                            f"QSIRecon output files missing:\n"
                            f"    Pipeline:   {pipeline_name}\n"
                            f"    Directory:  {qsirecon_dwi_dir}\n"
                            f"    Expected:   *.nii.gz files\n"
                            f"    Found:      {len(list(qsirecon_dwi_dir.glob('*')))} files total"
                        )
                    else:
                        self.add_found_item(
                            f"QSIRecon files for {subj}/{sess}: {len(nii_files)} .nii.gz files"
                        )


class BIDSOutputValidator:
    """Main validator class that orchestrates all pipeline checks."""
    
    PIPELINE_CHECKERS = {
        'fmriprep': FMRIPrepChecker,
        'freesurfer': FreeSurferChecker,
        'qsiprep': QSIPrepChecker,
        'qsirecon': QSIReconChecker,
    }
    
    def __init__(self, bids_dir: Path, derivatives_dir: Path, verbose: bool = False, quiet: bool = False, log_file: Optional[Path] = None):
        self.bids_dir = bids_dir
        self.derivatives_dir = derivatives_dir
        self.setup_logging(verbose, quiet, log_file)
        self.results = {}
    
    def setup_logging(self, verbose: bool, quiet: bool, log_file: Optional[Path] = None):
        """Setup logging configuration."""
        # Determine log level
        if verbose:
            level = logging.DEBUG
        elif quiet:
            level = logging.WARNING
        else:
            level = logging.INFO
        
        # Clear any existing handlers
        logging.getLogger().handlers.clear()
        
        # Setup formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s: %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # Console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        
        # Root logger setup
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)  # Capture everything, handlers filter
        root_logger.addHandler(console_handler)
        
        # File handler if specified
        if log_file:
            try:
                file_handler = logging.FileHandler(log_file, mode='w', encoding='utf-8')
                file_handler.setLevel(logging.DEBUG)  # Always log everything to file
                file_handler.setFormatter(formatter)
                root_logger.addHandler(file_handler)
                logging.info(f"Logging to file: {log_file}")
            except Exception as e:
                logging.error(f"Could not setup file logging: {e}")
        
        self.logger = logging.getLogger(__name__)
    
    def discover_pipelines(self) -> List[str]:
        """Automatically discover available pipelines in derivatives directory."""
        discovered = []
        
        for pipeline_name in self.PIPELINE_CHECKERS.keys():
            if pipeline_name == 'qsirecon':
                # QSIRecon has special structure
                qsirecon_dirs = list(self.derivatives_dir.glob("qsirecon*"))
                if qsirecon_dirs:
                    discovered.append(pipeline_name)
            else:
                pipeline_dir = self.derivatives_dir / pipeline_name
                if pipeline_dir.exists() and pipeline_dir.is_dir():
                    discovered.append(pipeline_name)
        
        return discovered
    
    def validate_pipeline(self, pipeline_name: str) -> Dict:
        """Validate a specific pipeline."""
        if pipeline_name not in self.PIPELINE_CHECKERS:
            raise ValueError(f"Unknown pipeline: {pipeline_name}")
        
        self.logger.info(f"Validating pipeline: {pipeline_name}")
        
        # Determine pipeline directory
        if pipeline_name == 'qsirecon':
            pipeline_dir = self.derivatives_dir  # QSIRecon looks for subdirs
        else:
            pipeline_dir = self.derivatives_dir / pipeline_name
            if not pipeline_dir.exists():
                return {
                    'pipeline': pipeline_name,
                    'status': 'not_found',
                    'missing_items': [f"Pipeline directory not found: {pipeline_dir}"],
                    'total_missing': 1
                }
        
        # Run the checker
        checker_class = self.PIPELINE_CHECKERS[pipeline_name]
        checker = checker_class(self.bids_dir, self.derivatives_dir)
        
        success = checker.check_pipeline(pipeline_dir)
        
        return {
            'pipeline': pipeline_name,
            'status': 'passed' if success else 'failed',
            'missing_items': checker.missing_items,
            'total_missing': len(checker.missing_items),
            'stats': getattr(checker, 'stats', {})  # Include pipeline-specific statistics
        }
    
    def validate_all(self, specific_pipeline: Optional[str] = None) -> Dict:
        """Validate all discovered pipelines or a specific one."""
        if specific_pipeline:
            pipelines = [specific_pipeline] if specific_pipeline in self.PIPELINE_CHECKERS else []
            if not pipelines:
                raise ValueError(f"Unknown pipeline: {specific_pipeline}")
        else:
            pipelines = self.discover_pipelines()
        
        if not pipelines:
            self.logger.warning("No pipelines found to validate")
            return {'pipelines': {}, 'summary': {'total_pipelines': 0, 'passed': 0, 'failed': 0}}
        
        self.logger.info(f"Found pipelines to check: {', '.join(pipelines)}")
        
        results = {}
        for pipeline in pipelines:
            results[pipeline] = self.validate_pipeline(pipeline)
        
        # Generate summary
        total_pipelines = len(results)
        passed = sum(1 for r in results.values() if r['status'] == 'passed')
        failed = total_pipelines - passed
        
        summary = {
            'total_pipelines': total_pipelines,
            'passed': passed,
            'failed': failed,
            'total_missing_items': sum(r['total_missing'] for r in results.values())
        }
        
        return {'pipelines': results, 'summary': summary}
    
    def print_results(self, results: Dict, output_format: str = 'text', quiet: bool = False):
        """Print validation results."""
        if output_format == 'json':
            print(json.dumps(results, indent=2, default=str))
            return
        
        # Text output
        if not quiet:
            print("=" * 60)
            print("BIDS PIPELINE OUTPUT VALIDATION RESULTS")
            print("=" * 60)
        
        summary = results['summary']
        
        if quiet:
            # Minimal output for quiet mode
            if summary['failed'] > 0:
                print(f"❌ FAILED: {summary['failed']}/{summary['total_pipelines']} pipelines failed")
                print(f"Total missing items: {summary['total_missing_items']}")
            else:
                print(f"✅ PASSED: All {summary['total_pipelines']} pipelines validated successfully")
        else:
            # Normal detailed output
            print(f"Total pipelines checked: {summary['total_pipelines']}")
            print(f"Passed: {summary['passed']}")
            print(f"Failed: {summary['failed']}")
            print(f"Total missing items: {summary['total_missing_items']}")
            print()
            
            for pipeline_name, pipeline_result in results['pipelines'].items():
                status = pipeline_result['status']
                status_symbol = "✅" if status == 'passed' else "❌"
                print(f"{status_symbol} {pipeline_name.upper()}: {status.upper()}")
                
                # Print pipeline-specific statistics
                if 'stats' in pipeline_result and pipeline_result['stats']:
                    self._print_pipeline_statistics(pipeline_name, pipeline_result['stats'])
                
                if pipeline_result['missing_items']:
                    print(f"  Missing items ({len(pipeline_result['missing_items'])}):")
                    # Show fewer items in normal mode, more in verbose
                    max_items = 20 if logging.getLogger().getEffectiveLevel() == logging.DEBUG else 5
                    for item in pipeline_result['missing_items'][:max_items]:
                        print(f"    - {item}")
                    if len(pipeline_result['missing_items']) > max_items:
                        remaining = len(pipeline_result['missing_items']) - max_items
                        print(f"    ... and {remaining} more items (use --verbose to see all)")
                print()
            
            print("=" * 60)
    
    def _print_pipeline_statistics(self, pipeline_name: str, stats: Dict):
        """Print detailed statistics for a pipeline."""
        if pipeline_name == 'qsiprep' and stats:
            print(f"  📊 Subject Statistics:")
            print(f"    Total subjects checked: {stats.get('total_subjects', 0)}")
            
            if stats.get('subjects_with_dwi', 0) > 0:
                print(f"    Subjects with DWI data: {stats.get('subjects_with_dwi', 0)}")
                
                # Session-specific statistics
                session_stats = stats.get('session_statistics', {})
                if session_stats:
                    print(f"  📋 Session-specific Issues:")
                    for session, session_data in sorted(session_stats.items()):
                        missing_count = len(session_data.get('missing_subjects', []))
                        total_count = session_data.get('total_subjects', 0)
                        if missing_count > 0:
                            print(f"    {session}: Missing in {missing_count} subjects")
                            if logging.getLogger().getEffectiveLevel() == logging.DEBUG:
                                missing_subjects = session_data.get('missing_subjects', [])
                                print(f"      Affected subjects: {', '.join(missing_subjects[:5])}")
                                if len(missing_subjects) > 5:
                                    print(f"      ... and {len(missing_subjects) - 5} more")
                
                # Subjects with missing sessions
                missing_sessions = stats.get('subjects_with_missing_sessions', [])
                if missing_sessions:
                    print(f"    Subjects with missing sessions: {len(missing_sessions)}")
                    if logging.getLogger().getEffectiveLevel() == logging.DEBUG:
                        print(f"      Affected subjects: {', '.join(missing_sessions[:5])}")
                        if len(missing_sessions) > 5:
                            print(f"      ... and {len(missing_sessions) - 5} more")
            
            # Subjects with no DWI data
            no_dwi_subjects = stats.get('subjects_with_no_dwi', [])
            if no_dwi_subjects:
                print(f"    Subjects with no DWI data: {len(no_dwi_subjects)}")
                if logging.getLogger().getEffectiveLevel() == logging.DEBUG:
                    print(f"      Subjects: {', '.join(no_dwi_subjects[:5])}")
                    if len(no_dwi_subjects) > 5:
                        print(f"      ... and {len(no_dwi_subjects) - 5} more")
        
        elif pipeline_name == 'qsirecon' and stats:
            print(f"  📊 Reconstruction Statistics:")
            print(f"    Total subjects checked: {stats.get('total_subjects', 0)}")
            print(f"    Subjects with DWI data: {stats.get('subjects_with_dwi', 0)}")
            
            recon_pipelines = stats.get('recon_pipelines_found', [])
            if recon_pipelines:
                print(f"    Reconstruction pipelines found: {len(recon_pipelines)}")
                print(f"      Pipelines: {', '.join(recon_pipelines)}")
                
                print(f"  📋 Pipeline-specific Results:")
                subjects_by_pipeline = stats.get('subjects_by_pipeline', {})
                missing_by_pipeline = stats.get('missing_subjects_by_pipeline', {})
                
                for pipeline in recon_pipelines:
                    found_count = len(subjects_by_pipeline.get(pipeline, []))
                    missing_count = len(missing_by_pipeline.get(pipeline, []))
                    total_dwi = stats.get('subjects_with_dwi', 0)
                    
                    print(f"    {pipeline}:")
                    print(f"      Subjects processed: {found_count}/{total_dwi}")
                    if missing_count > 0:
                        print(f"      Missing subjects: {missing_count}")
                        if logging.getLogger().getEffectiveLevel() == logging.DEBUG:
                            missing_subjects = missing_by_pipeline.get(pipeline, [])
                            print(f"        Missing: {', '.join(missing_subjects[:5])}")
                            if len(missing_subjects) > 5:
                                print(f"        ... and {len(missing_subjects) - 5} more")
            else:
                print(f"    No reconstruction pipelines found in derivatives structure")


def extract_missing_subjects_from_results(results: Dict) -> Set[str]:
    """Extract unique subject IDs from validation results that have missing data."""
    missing_subjects = set()
    
    # Extract subjects from pipeline results
    if 'pipelines' in results:
        for pipeline_name, pipeline_data in results['pipelines'].items():
            if 'missing_items' in pipeline_data:
                for item in pipeline_data['missing_items']:
                    # Extract subject ID from missing item description
                    # Look for patterns like "sub-123" in the item string
                    import re
                    match = re.search(r'sub-\d+', item)
                    if match:
                        missing_subjects.add(match.group())
    
    return missing_subjects


def save_detailed_missing_report(results: Dict, output_file: Path, pipeline_filter: Optional[str] = None):
    """Save detailed missing subjects/sessions report to JSON file."""
    from datetime import datetime
    
    missing_data = {}
    
    if 'pipelines' in results:
        for pipeline_name, pipeline_data in results['pipelines'].items():
            if pipeline_filter and pipeline_name != pipeline_filter:
                continue
                
            pipeline_missing = {
                'missing_items': pipeline_data.get('missing_items', []),
                'total_missing': len(pipeline_data.get('missing_items', [])),
                'subjects_with_missing_data': list(extract_missing_subjects_from_results({'pipelines': {pipeline_name: pipeline_data}}))
            }
            
            missing_data[pipeline_name] = pipeline_missing
    
    report = {
        'metadata': {
            'generated_by': 'BIDS App Output Checker',
            'timestamp': datetime.now().isoformat(),
            'command': ' '.join(sys.argv),
            'pipeline_filter': pipeline_filter
        },
        'missing_data_by_pipeline': missing_data,
        'summary': {
            'total_pipelines_checked': len(missing_data),
            'pipelines_with_missing_data': len([p for p in missing_data.values() if p['total_missing'] > 0]),
            'all_missing_subjects': sorted(list(extract_missing_subjects_from_results(results)))
        }
    }
    
    try:
        with open(output_file, 'w') as f:
            json.dump(report, f, indent=2)
        print(f"Detailed missing report saved to: {output_file}", file=sys.stderr)
    except Exception as e:
        print(f"Error saving report: {e}", file=sys.stderr)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Validate BIDS pipeline outputs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s /data/bids /data/derivatives
  %(prog)s /data/bids /data/derivatives -p fmriprep
  %(prog)s /data/bids /data/derivatives --json --verbose --log validation.log
  %(prog)s /data/bids /data/derivatives --quiet  # Minimal output
        """
    )
    
    parser.add_argument('bids_dir', type=Path, help='BIDS source directory')
    parser.add_argument('derivatives_dir', type=Path, help='Derivatives directory')
    parser.add_argument('-p', '--pipeline', help='Check specific pipeline only')
    parser.add_argument('--json', action='store_true', help='Output results in JSON format')
    parser.add_argument('-v', '--verbose', action='store_true', help='Verbose output (DEBUG level)')
    parser.add_argument('-q', '--quiet', action='store_true', help='Quiet mode (WARNING level only)')
    parser.add_argument('--log', type=Path, help='Write log to file')
    parser.add_argument('--list-missing-subjects', action='store_true', 
                       help='Output only missing subject IDs (one per line) for use with run_bids_apps.py')
    parser.add_argument('--output-json', type=Path, 
                       help='Save detailed missing subjects/sessions report to JSON file')
    
    # Show help if no arguments provided
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)
    
    args = parser.parse_args()
    
    # Validate directories
    if not args.bids_dir.exists():
        print(f"Error: BIDS directory does not exist: {args.bids_dir}", file=sys.stderr)
        sys.exit(1)
    
    if not args.derivatives_dir.exists():
        print(f"Error: Derivatives directory does not exist: {args.derivatives_dir}", file=sys.stderr)
        sys.exit(1)
    
    # Validate conflicting options
    if args.verbose and args.quiet:
        print("Error: Cannot use --verbose and --quiet together", file=sys.stderr)
        sys.exit(1)
    
    if args.list_missing_subjects and (args.json or args.verbose):
        print("Error: --list-missing-subjects cannot be used with --json or --verbose", file=sys.stderr)
        sys.exit(1)
    
    # Run validation
    try:
        validator = BIDSOutputValidator(
            args.bids_dir, 
            args.derivatives_dir, 
            args.verbose, 
            args.quiet, 
            args.log
        )
        results = validator.validate_all(args.pipeline)
        
        # Handle list-missing-subjects mode
        if args.list_missing_subjects:
            missing_subjects = extract_missing_subjects_from_results(results)
            
            if missing_subjects:
                for subject in sorted(missing_subjects):
                    print(subject)
                sys.exit(1)  # Exit with error code to indicate missing subjects
            else:
                sys.exit(0)  # No missing subjects
        
        # Handle detailed JSON output
        if args.output_json:
            save_detailed_missing_report(results, args.output_json, args.pipeline)
        
        output_format = 'json' if args.json else 'text'
        validator.print_results(results, output_format, args.quiet)
        
        # Exit with error code if any pipeline failed
        if results['summary']['failed'] > 0:
            sys.exit(1)
        
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
