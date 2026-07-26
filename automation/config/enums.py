from enum import Enum


class ConfigEnv(str, Enum):
    '''Supported automation test environments.'''
    LOCAL = 'local'
    SIT = 'sit'
    UAT = 'uat'

    @classmethod
    def from_str(cls, value: str) -> 'ConfigEnv':
        '''Parse a string to ConfigEnv, case-insensitive.'''
        for member in cls:
            if member.value == value.lower():
                return member
        raise ValueError(f'Unknown config environment: {value}. Valid: {[e.value for e in cls]}')
