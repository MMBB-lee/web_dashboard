from intelligent_analysis.service import health


def test_health_reports_module_version():
    result = health()

    assert result["module"] == "intelligent_analysis"
    assert result["status"] == "ready"
    assert result["version"]
