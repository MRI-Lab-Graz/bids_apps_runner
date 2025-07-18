# Pilot Mode Changes

## Summary of Changes

The `pilottest` option has been moved from the JSON configuration file to a command line argument for better usability and consistency.

## Before (Old Way)
```json
{
  "common": {
    "bids_folder": "/path/to/bids",
    "output_folder": "/path/to/output", 
    "pilottest": true,
    "jobs": 4
  }
}
```

```bash
./run_bids_apps.py -x config.json
```

## After (New Way)
```json
{
  "common": {
    "bids_folder": "/path/to/bids",
    "output_folder": "/path/to/output",
    "jobs": 4
  }
}
```

```bash
./run_bids_apps.py -x config.json --pilot
```

## Benefits

1. **No config file editing**: Switch between pilot and full runs without modifying JSON files
2. **Automatic job limiting**: Pilot mode automatically sets jobs=1 for better debugging
3. **Command line consistency**: All runtime options are now command line arguments
4. **Better debugging**: Pilot mode works seamlessly with `--debug` flag

## Migration

### Update existing config files:
- Remove `"pilottest": true/false` lines from all JSON config files
- Keep `"jobs"` setting for normal runs

### Update scripts/workflows:
- Replace config file modifications with `--pilot` flag
- Can now combine with other flags like `--debug`, `--dry-run`, etc.

## Example Usage

```bash
# Test configuration with pilot mode
./run_bids_apps.py -x config.json --pilot --dry-run

# Debug a single random subject
./run_bids_apps.py -x config.json --pilot --debug

# Quick pilot run
./run_bids_apps.py -x config.json --pilot

# Normal full run (no changes needed)
./run_bids_apps.py -x config.json
```

This change makes the tool more flexible and easier to use in testing scenarios.
