import pytest
import settings


@pytest.mark.parametrize("raw", ["True", "true", "TRUE", "1", "yes", "on", " true "])
def test_bool_setting_true_spellings(raw: str) -> None:
    assert settings.parse_env_value(False, raw) is True


@pytest.mark.parametrize("raw", ["False", "false", "0", "no", "off", ""])
def test_bool_setting_false_spellings(raw: str) -> None:
    assert settings.parse_env_value(True, raw) is False


def test_bool_setting_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        settings.parse_env_value(False, "maybe")


@pytest.mark.parametrize(
    ("default", "raw", "expected"),
    [(16, "32", 32), ("local", "s3", "s3"), (None, "0.5", 0.5), ([], "[320, 640]", [320, 640])],
)
def test_non_bool_settings_still_evaluated(default: object, raw: str, expected: object) -> None:
    assert settings.parse_env_value(default, raw) == expected
