import hmac
import hashlib
import base64
from pypinyin import pinyin, Style
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    truncated_password = plain_password[:72]
    return pwd_context.verify(truncated_password, hashed_password)

def get_password_hash(password: str) -> str:
    truncated_password = password[:72]
    return pwd_context.hash(truncated_password)

def chinese_to_pinyin(input_str):
    pinyin_list = pinyin(input_str, style=Style.NORMAL, heteronym=False)
    pinyin_result = [item[0] for item in pinyin_list]
    return ''.join(pinyin_result)

def generate_password(input_str, secret_key="usp-zl-mf", length=8):
    hmac_obj = hmac.new(
        key=secret_key.encode(),
        msg=input_str.encode(),
        digestmod=hashlib.sha256
    )
    hmac_digest = hmac_obj.digest()
    
    b64 = base64.b64encode(hmac_digest).decode('ascii')
    
    char_map = {
        '+': '@',
        '/': '#',
        '=': '$',
        '0': '!',
        'O': '*',
        'l': '%',
        'I': '^'
    }
    
    mapped = []
    for char in b64[:length + 4]:
        mapped.append(char_map.get(char, char))
    
    password_chars = mapped[:length]
    specials = "@#$!%^&*"
    
    if not any(c in specials for c in password_chars):
        pos = ord(input_str[0]) % length if input_str else 3
        special_idx = ord(input_str[-1]) % len(specials) if input_str else 0
        password_chars[pos] = specials[special_idx]
    
    return ''.join(password_chars[:length])

def generate_pinyin_password_pair(chinese_str, secret_key="usp-zl-mf", password_length=8):
    username = chinese_to_pinyin(chinese_str)
    password = generate_password(username, secret_key, password_length)
    
    return {
        "username": username,
        "password": password
    }