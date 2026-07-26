'''Tests for config.enums module.'''

import pytest
from automation.infrastructure.config.enums import ConfigEnv


class TestConfigEnv:
    '''Test ConfigEnv enum.'''

    def test_members(self):
        assert ConfigEnv.LOCAL.value == 'local'
        assert ConfigEnv.SIT.value == 'sit'
        assert ConfigEnv.UAT.value == 'uat'

    def test_from_str_valid(self):
        assert ConfigEnv.from_str('local') == ConfigEnv.LOCAL
        assert ConfigEnv.from_str('LOCAL') == ConfigEnv.LOCAL
        assert ConfigEnv.from_str('sit') == ConfigEnv.SIT
        assert ConfigEnv.from_str('SIT') == ConfigEnv.SIT
        assert ConfigEnv.from_str('uat') == ConfigEnv.UAT
        assert ConfigEnv.from_str('UAT') == ConfigEnv.UAT

    def test_from_str_invalid(self):
        with pytest.raises(ValueError, match='Unknown config environment'):
            ConfigEnv.from_str('invalid')
        with pytest.raises(ValueError, match='Unknown config environment'):
            ConfigEnv.from_str('prod')
        with pytest.raises(ValueError, match='Unknown config environment'):
            ConfigEnv.from_str('')

    def test_enum_value_behavior(self):
        assert ConfigEnv.LOCAL.value == 'local'
        assert ConfigEnv.LOCAL == 'local'
        assert ConfigEnv('local') == ConfigEnv.LOCAL
        assert repr(ConfigEnv.LOCAL).startswith('<ConfigEnv.LOCAL')

    def test_all_enums_covered(self):
        values = [e.value for e in ConfigEnv]
        assert 'local' in values
        assert 'sit' in values
        assert 'uat' in values
        assert len(values) == 3

