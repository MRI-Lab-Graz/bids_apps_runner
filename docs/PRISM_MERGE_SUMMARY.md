# PRISM Runner Summary (Current)

PRISM Runner provides a unified CLI entry point that auto-detects execution mode.

## CLI

```bash
python scripts/prism_runner.py -c configs/config.json
```

## Mode Detection

- If hpc is present in config, HPC mode is used.
- Otherwise, local mode is used.
- Use --local or --hpc to override.

## GUI

The GUI uses project.json and launches runs from the Run App tab.

## Conclusion

The PRISM Runner architecture successfully unifies local and HPC execution into a single, maintainable, and user-friendly interface. The implementation preserves all existing functionality while providing a cleaner, more modular codebase for future development.
