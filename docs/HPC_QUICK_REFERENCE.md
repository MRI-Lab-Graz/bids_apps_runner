# HPC/DataLad Quick Reference

## Files Created

| File | Purpose |
|------|---------|
| `hpc_datalad_runner.py` | SLURM script generator |
| `hpc_batch_submit.py` | Batch job submission tool |
| `config_hpc_datalad.json` | Example HPC configuration |
| `README_HPC_DATALAD.md` | Complete usage guide |
| `EXAMPLES_HPC_DATALAD.md` | 8 practical examples |
| `GUI_HPC_INTEGRATION.md` | Frontend integration guide |
| `IMPLEMENTATION_SUMMARY.md` | Implementation overview |

## Files Modified

| File | Changes |
|------|---------|
| `app_gui.py` | Added 6 HPC endpoints + imports |

## CLI Commands

### Generate Single Script
```bash
python hpc_datalad_runner.py -c config.json -s sub-001 -o job.sh
```

### Submit Job
```bash
sbatch job.sh
```

### Batch Submit
```bash
python hpc_batch_submit.py -c config.json --max-jobs 50
```

### Dry Run
```bash
python hpc_batch_submit.py -c config.json --dry-run
```

## API Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/check_hpc_environment` | Check HPC tools |
| POST | `/generate_hpc_script` | Generate script |
| POST | `/save_hpc_script` | Save to disk |
| POST | `/submit_hpc_job` | Submit to SLURM |
| POST | `/get_hpc_job_status` | Check job status |
| POST | `/cancel_hpc_job` | Cancel job |

## Quick Start

```bash
# 1. Prepare repos
datalad create /path/to/bids
datalad create /path/to/results

# 2. Create config
cp config_hpc_datalad.json my_config.json
# Edit my_config.json with your settings

# 3. Generate scripts
python hpc_datalad_runner.py -c my_config.json -s sub-001 -o job_001.sh

# 4. Submit
sbatch job_001.sh

# 5. Monitor
squeue -u $USER
```

## Generated Script Structure

```bash
#!/bin/bash
#SBATCH directives...

# Setup (modules, environment, directories)
# Clone DataLad dataset with lock
# Get directory structure
# Create job-specific branches
# Run datalad containers-run
# Push results with lock
# Cleanup
```

## Key Configuration Fields

### DataLad
```json
"datalad": {
  "input_repo": "URL to input BIDS",
  "output_repos": ["List of output repos"],
  "clone_method": "clone or install",
  "lock_file": "/path/to/lock"
}
```

### HPC
```json
"hpc": {
  "partition": "compute",
  "time": "24:00:00",
  "mem": "32G",
  "cpus": 8,
  "modules": ["module1", "module2"],
  "environment": {"VAR": "value"}
}
```

### Container
```json
"container": {
  "name": "app_name",
  "image": "/path/to/image.sif",
  "outputs": ["output_dir1"],
  "bids_args": {
    "analysis_level": "participant",
    "n_cpus": 8
  }
}
```

## Environment Check

```javascript
// Browser console
fetch('/check_hpc_environment')
  .then(r => r.json())
  .then(d => console.log(d))

// Shows:
{
  "slurm": true,
  "datalad": true,
  "git": true,
  "git_annex": true,
  "apptainer": true
}
```

## Submit via API

```bash
curl -X POST http://localhost:8080/submit_hpc_job \
  -H "Content-Type: application/json" \
  -d '{
    "script_path": "/tmp/job_001.sh",
    "dry_run": false
  }'

# Returns: {"message": "...", "job_id": "12345"}
```

## Monitor Jobs

```bash
# Command line
squeue -u $USER

# Via API
curl -X POST http://localhost:8080/get_hpc_job_status \
  -H "Content-Type: application/json" \
  -d '{"job_ids": ["12345"]}'
```

## Workflow Diagram

```
┌─────────────────────────────────────────────────┐
│           BIDS Apps Runner HPC Mode              │
└─────────────────────────────────────────────────┘
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
   CLI Tool        Web GUI         Batch Submit
   (Generate)    (Full UI)         (Batch)
        │              │              │
        └──────────────┼──────────────┘
                       │
                ┌──────▼──────┐
                │ SLURM Script │
                │  Generator   │
                └──────┬──────┘
                       │
        ┌──────────────▼──────────────┐
        │   DataLad Workflow Script    │
        ├──────────────────────────────┤
        │ 1. Clone input repo          │
        │ 2. Get structure             │
        │ 3. Create branches           │
        │ 4. Run container             │
        │ 5. Push results              │
        │ 6. Cleanup                   │
        └──────────────┬───────────────┘
                       │
                  ┌────▼────┐
                  │  SLURM   │
                  │ Cluster  │
                  └──────────┘
```

## Performance Tips

1. **Use scratch storage for work_dir**
   - Not NFS if possible
   - Local SSD preferred

2. **Pre-clone large datasets**
   - Clone once, reuse for many jobs

3. **Submit in waves**
   - Don't submit 1000 jobs at once
   - Submit 100-200 at a time

4. **Use fast lock file location**
   - Not on slow network storage

5. **Optimize container args**
   - Match HPC resources
   - n_cpus = SLURM cpus
   - mem-mb ≈ SLURM mem

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "datalad not found" | `module load datalad` |
| "sbatch not found" | Running on HPC? Check SLURM installation |
| Permission denied | Check SSH keys: `ssh -T git@github.com` |
| Script hangs | Check lock file: `ls -la /tmp/datalad.lock` |
| Memory error | Increase `hpc.mem` and `container.bids_args.mem-mb` |

## Documentation Links

| Document | Content |
|----------|---------|
| `README_HPC_DATALAD.md` | Full configuration guide |
| `EXAMPLES_HPC_DATALAD.md` | 8 detailed examples |
| `GUI_HPC_INTEGRATION.md` | Frontend integration |
| `IMPLEMENTATION_SUMMARY.md` | Architecture overview |

## Example Configs

- `config_hpc_datalad.json` - Full fMRIPrep example
- `config_hpc.json` - QSIRecon example (original)

## Next Steps

1. ✅ Prepare DataLad repositories
2. ✅ Create HPC configuration
3. ✅ Test script generation
4. ✅ Test SLURM submission
5. ✅ Monitor jobs
6. ⏳ Integrate frontend (optional)

## Support

- Check `README_HPC_DATALAD.md` for detailed guide
- See `EXAMPLES_HPC_DATALAD.md` for practical examples
- Review `IMPLEMENTATION_SUMMARY.md` for architecture
- Use `--log-level DEBUG` for troubleshooting

---

**Ready to run BIDS Apps on HPC with DataLad! 🚀**
