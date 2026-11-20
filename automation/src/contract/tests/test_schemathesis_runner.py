from automation.src.contract import build_contract_command


def test_build_contract_command_contains_core_options():
    command = build_contract_command(
        "http://localhost:8400/api/openapi.json",
        "http://localhost:8400",
        max_examples=3,
        include_path_regex="^/api/health$",
    )

    assert "run" in command
    assert "http://localhost:8400/api/openapi.json" in command
    assert "--url" in command
    assert "--max-examples" in command
    assert "3" in command
    assert "--include-path-regex" in command
    assert "^/api/health$" in command