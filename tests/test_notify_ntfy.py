from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "notify_ntfy.sh"


def _source() -> str:
    return SCRIPT.read_text()


class TestNotifyReliability:
    """Regression test: notify_ntfy.sh silently failed even when ntfy.sh
    itself was reachable (confirmed real incident, 2026-09-21). ntfy.sh
    round-robins across several backend IPs, at least one of which is
    routinely unreachable from this HPC, and IPv6 is unreachable from this
    host entirely ("Network is unreachable"). A bare `curl --max-time 10`
    with no retry hit a dead backend, timed out, and gave up -- reported as
    "failed to reach ntfy.sh" even though a plain `curl -v` to the same URL
    immediately afterward succeeded. Verified fix empirically: 3 real calls
    against the actual configured topic, one of which hit the same dead-IP
    timeout and was rescued by the retry.
    """

    def test_curl_call_forces_ipv4(self):
        assert "curl -4 " in _source(), (
            "IPv6 is unreachable from this host -- must skip it with -4, "
            "not waste a connection attempt on a route that never works"
        )

    def test_curl_call_retries(self):
        source = _source()
        assert "--retry" in source, (
            "a single bad IP from ntfy.sh's round-robin DNS must not kill "
            "the whole notification -- one retry against a re-resolved IP "
            "is what actually recovers from the routine bad-backend case"
        )

    def test_max_time_budget_accounts_for_the_retry(self):
        # the old 10s max-time barely covered ONE failed connection attempt
        # (confirmed: a dead backend took ~7.7s just to time out) -- with a
        # retry added, the overall budget must be large enough to survive
        # one bad attempt AND still complete a real one afterward.
        import re

        source = _source()
        curl_line = next(line for line in source.splitlines() if line.strip().startswith("curl -4"))
        match = re.search(r"--max-time (\d+)", curl_line)
        assert match, "no --max-time flag found on the curl invocation"
        assert int(match.group(1)) >= 20
