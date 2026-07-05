from lycosa_agent.hwprofile import collect_profile


def test_profile_has_controller_schema_fields() -> None:
    profile = collect_profile(ollama_url="http://localhost:1")  # unreachable: no runtime found
    assert profile["cpu_model"]
    assert profile["cpu_cores"] >= 1
    assert profile["ram_gb"] > 0
    assert profile["storage_gb"] > 0
    assert profile["os"]["name"]
    assert isinstance(profile["gpus"], list)
    assert isinstance(profile["runtimes"], list)
