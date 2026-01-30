# Tests Directory

Test scripts for development and debugging.

## Test Scripts

### `test_docker_api.py`
Quick test of Docker Hub API connectivity.
```bash
python tests/test_docker_api.py
```
Tests fetching container tags from Docker Hub registry.

### `test_parse.py`
Test script for argparse help output parsing.
```bash
python tests/test_parse.py
```
Validates the help text parsing logic used in the GUI.

## Running Tests

These are development/debugging scripts, not formal unit tests. They can be run directly:

```bash
# Test Docker Hub API
python tests/test_docker_api.py

# Test argparse parsing
python tests/test_parse.py
```

## Future Work

Consider migrating to proper testing framework:
- pytest for unit tests
- Integration tests for full workflows
- Continuous integration with GitHub Actions
