import pytest
import settings


@pytest.mark.parametrize("raw", ["True", "true", "TRUE", "1", "yes", "on", " true ", '"True"', "'true'"])
def test_bool_setting_true_spellings(raw: str) -> None:
    assert settings.parse_env_value(False, raw) is True


@pytest.mark.parametrize("raw", ["False", "false", "0", "no", "off", "", "None", '"False"', '""'])
def test_bool_setting_false_spellings(raw: str) -> None:
    assert settings.parse_env_value(True, raw) is False


def test_bool_setting_rejects_garbage() -> None:
    with pytest.raises(ValueError, match="ALLOW_VIDEO"):
        settings.parse_env_value(False, "maybe", "ALLOW_VIDEO")


@pytest.mark.parametrize(
    ("default", "raw", "expected"),
    [(16, "32", 32), ("local", "s3", "s3"), (None, "0.5", 0.5), ([], "[320, 640]", [320, 640])],
)
def test_non_bool_settings_still_evaluated(default: object, raw: str, expected: object) -> None:
    assert settings.parse_env_value(default, raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("80,443", [80, 443]), ("8080", [8080]), ("[443, 80, 80]", [80, 443]), (" 80, 8080 ", [80, 8080]), ("None", None)],
)
def test_port_list_spellings(raw: str, expected: object) -> None:
    assert settings.parse_ports(settings.parse_env_value([80, 443], raw)) == expected


@pytest.mark.parametrize("raw", ["", "80;443", "80 443", "http", "0", "70000", "80,x", "[]", "True", "80.5"])
def test_port_list_rejects_anything_else(raw: str) -> None:
    with pytest.raises(ValueError, match="URL_UPLOAD_PORTS"):
        settings.parse_ports(settings.parse_env_value([80, 443], raw), "URL_UPLOAD_PORTS")
