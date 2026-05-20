"""S5: CENTRALIZED_ALLOCATOR feature-flag tests."""
import os

from src.runtime.runtime_flags import _centralized_allocator_enabled


class TestCentralizedAllocatorEnabled:
    def setup_method(self, _method):
        os.environ.pop("CENTRALIZED_ALLOCATOR", None)

    def teardown_method(self, _method):
        os.environ.pop("CENTRALIZED_ALLOCATOR", None)

    def test_default_is_false(self):
        assert _centralized_allocator_enabled({}) is False

    def test_env_true(self):
        os.environ["CENTRALIZED_ALLOCATOR"] = "true"
        assert _centralized_allocator_enabled({}) is True

    def test_env_one(self):
        os.environ["CENTRALIZED_ALLOCATOR"] = "1"
        assert _centralized_allocator_enabled({}) is True

    def test_env_yes(self):
        os.environ["CENTRALIZED_ALLOCATOR"] = "yes"
        assert _centralized_allocator_enabled({}) is True

    def test_env_on(self):
        os.environ["CENTRALIZED_ALLOCATOR"] = "on"
        assert _centralized_allocator_enabled({}) is True

    def test_env_false(self):
        os.environ["CENTRALIZED_ALLOCATOR"] = "false"
        assert _centralized_allocator_enabled({}) is False

    def test_settings_overrides_env_false(self):
        os.environ["CENTRALIZED_ALLOCATOR"] = "true"
        assert _centralized_allocator_enabled({"CENTRALIZED_ALLOCATOR": "false"}) is False

    def test_settings_on(self):
        assert _centralized_allocator_enabled({"CENTRALIZED_ALLOCATOR": "true"}) is True

    def test_non_dict_settings_uses_env(self):
        os.environ.pop("CENTRALIZED_ALLOCATOR", None)
        assert _centralized_allocator_enabled(None) is False  # type: ignore[arg-type]

    def test_case_insensitive_env(self):
        os.environ["CENTRALIZED_ALLOCATOR"] = "TRUE"
        assert _centralized_allocator_enabled({}) is True
