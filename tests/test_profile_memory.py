"""Keep physical memory distinct from WebKit's large reserved address space."""

import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location("profile_memory", Path(__file__).parents[1] / "scripts/profile_memory.py")
profiler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profiler)


class TestProfileMemory:
    def test_footprint_and_allocator_usage_ignore_reserved_virtual_memory(self):
        values = profiler.parse_summary("""Physical footprint:         197.7M
Physical footprint (peak):  1.2G
WebKit Malloc                    128.0M    48.3M    43.7M       0K
WebKit Malloc metadata           256.1M     432K     400K       0K
JS JIT Generated Code            512.0M    1344K    1312K       0K
TOTAL                            457.6G     1.3G   279.8M       0K
""")
        assert values['footprint_mib'] == 197.7
        assert values['peak_mib'] == 1.2 * 1024
        assert values['WebKit Malloc'] == {'resident_mib': 48.3, 'dirty_mib': 43.7}
        assert values['JS JIT Generated Code']['dirty_mib'] == 1312 / 1024

    def test_missing_footprint_is_not_a_zero_memory_sample(self):
        with pytest.raises(ValueError):
            profiler.parse_summary("vmmap: process no longer exists")
