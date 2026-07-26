import hashlib
import logging

logger = logging.getLogger(__name__)


def verify_wechat_signature(signature: str, timestamp: str, nonce: str, token: str) -> bool:
    try:
        items = [token, timestamp, nonce]
        items.sort()
        
        temp = ''.join(items)
        hashcode = hashlib.sha1(temp.encode('utf-8')).hexdigest()
        
        logger.debug(f"签名验证参数: token={token}, timestamp={timestamp}, nonce={nonce}")
        logger.debug(f"排序后的字符串: {temp}")
        logger.debug(f"计算出的hash: {hashcode}")
        logger.debug(f"微信发送的signature: {signature}")
        
        result = hashcode == signature
        logger.info(f"签名验证结果: {'成功' if result else '失败'}")
        
        return result
    except Exception as e:
        logger.error(f"验证微信签名时发生异常: {e}", exc_info=True)
        return False


def generate_wechat_user_password(openid: str) -> str:
    return openid[:32]


def generate_wechat_username(openid: str) -> str:
    return f"wechat_{openid[:10]}"